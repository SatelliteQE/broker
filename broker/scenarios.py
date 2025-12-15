"""Broker Scenarios module for chaining multiple Broker actions together.

This module provides functionality to execute scenario files that define
a sequence of Broker-based actions (checkout, checkin, execute, ssh, etc.)
with support for templating, looping, error handling, and variable capture.

Usage:
    runner = ScenarioRunner("/path/to/scenario.yaml", cli_vars={"MY_VAR": "value"})
    runner.run()
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
from pathlib import Path
import sys

import jinja2
import jsonschema
from ruamel.yaml import YAML

from broker import helpers
from broker.broker import Broker
from broker.exceptions import ScenarioError
from broker.logging import setup_logging
from broker.providers import PROVIDERS
from broker.settings import BROKER_DIRECTORY, create_settings

logger = logging.getLogger(__name__)

yaml = YAML()
yaml.default_flow_style = False
yaml.sort_keys = False

# Mapping of user-friendly argument names to Broker's internal argument names
# These are arguments that have a different name when passed to Broker directly
ARGUMENT_NAME_MAP = {
    "count": "_count",
}

# Directory where scenarios are stored by default
SCENARIOS_DIR = BROKER_DIRECTORY / "scenarios"
SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)

# Load the schema from the package
SCHEMA_PATH = Path(__file__).parent / "scenario_schema.json"


def get_schema():
    """Load and return the scenario JSON schema."""
    if SCHEMA_PATH.exists():
        with SCHEMA_PATH.open() as f:
            return json.load(f)
    return None


def find_scenario(name_or_path):
    """Find a scenario file by name or path.

    Args:
        name_or_path: Either a scenario name (without extension) or a full path

    Returns:
        Path object to the scenario file

    Raises:
        ScenarioError: If the scenario cannot be found
    """
    # First, check if it's a direct path
    path = Path(name_or_path)
    if path.exists() and path.is_file():
        return path

    # Add .yaml extension if not present
    if not path.suffix:
        path = path.with_suffix(".yaml")
        if path.exists():
            return path

    # Check in the scenarios directory
    scenario_path = SCENARIOS_DIR / path.name
    if scenario_path.exists():
        return scenario_path

    # Check with .yaml extension
    scenario_path = SCENARIOS_DIR / f"{name_or_path}.yaml"
    if scenario_path.exists():
        return scenario_path

    # Check with .yml extension
    scenario_path = SCENARIOS_DIR / f"{name_or_path}.yml"
    if scenario_path.exists():
        return scenario_path

    raise ScenarioError(f"Scenario not found: {name_or_path}")


def list_scenarios():
    """List all scenarios in the scenarios directory.

    Returns:
        List of scenario names (without extensions)
    """
    if not SCENARIOS_DIR.exists():
        return []

    return sorted(f.stem for f in SCENARIOS_DIR.iterdir() if f.suffix in (".yaml", ".yml"))


def render_template(template_str, context):
    """Render a Jinja2 template string using the provided context.

    If the input is not a string or doesn't contain template syntax,
    it is returned as-is.

    For simple variable references like "{{ variable }}", this function
    preserves the original Python type (int, bool, list, dict, etc.).
    For complex templates with text or multiple expressions, it returns a string.

    Args:
        template_str: String that may contain Jinja2 template syntax
        context: Dictionary of variables for template rendering

    Returns:
        Rendered value (preserving type for simple refs) or string for complex templates
    """
    if not isinstance(template_str, str):
        return template_str

    # Check if it looks like a template
    if "{{" not in template_str and "{%" not in template_str:
        return template_str

    # Check if this is a simple variable reference like "{{ var }}" or "{{ obj.attr }}"
    # If so, use evaluate_expression to preserve the Python type
    stripped = template_str.strip()
    if (
        stripped.startswith("{{")
        and stripped.endswith("}}")
        and stripped.count("{{") == 1
        and "{%" not in stripped
    ):
        # Extract the expression inside {{ }}
        expr = stripped[2:-2].strip()
        try:
            return evaluate_expression(expr, context)
        except ScenarioError:
            # Fall through to standard rendering if expression evaluation fails
            pass

    try:
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        template = env.from_string(template_str)
        return template.render(**context)
    except jinja2.UndefinedError as e:
        logger.warning(f"Template rendering warning: {e}")
        raise ScenarioError(f"Undefined variable in template: {e}") from e


def evaluate_expression(expression, context):
    """Evaluate a Jinja2 expression and return the actual Python object.

    Unlike render_template which always returns a string, this function
    returns the actual Python object resulting from the expression evaluation.
    This is useful for getting iterables, dicts, etc.

    Args:
        expression: A Jinja2 expression (without {{ }} delimiters)
        context: Dictionary of variables for expression evaluation

    Returns:
        The Python object resulting from the expression evaluation
    """
    try:
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        compiled_expr = env.compile_expression(expression)
        return compiled_expr(**context)
    except jinja2.UndefinedError as e:
        logger.warning(f"Expression evaluation warning: {e}")
        raise ScenarioError(f"Undefined variable in expression: {e}") from e


def evaluate_condition(expression, context):
    """Evaluate a Jinja2 expression returning a boolean.

    Used for 'when' conditions in steps.

    Args:
        expression: A string expression that should evaluate to True/False
        context: Dictionary of variables for expression evaluation

    Returns:
        Boolean result of the expression
    """
    if not expression:
        return True

    # Wrap in {{ }} if not already a Jinja2 expression
    if not expression.strip().startswith("{{"):
        expression = f"{{{{ {expression} }}}}"

    result = render_template(expression, context)

    # Convert string result to boolean
    if isinstance(result, bool):
        return result
    if str(result).lower() in ("true", "yes", "1"):
        return True
    if str(result).lower() in ("false", "no", "0", "none", ""):
        return False
    return bool(result)


def recursive_render(data, context):
    """Recursively render template strings in a dictionary or list.

    Args:
        data: Dictionary, list, or value that may contain template strings
        context: Dictionary of variables for template rendering

    Returns:
        Data structure with all template strings rendered
    """
    if isinstance(data, dict):
        return {k: recursive_render(v, context) for k, v in data.items()}
    elif isinstance(data, list):
        return [recursive_render(item, context) for item in data]
    elif isinstance(data, str):
        return render_template(data, context)
    else:
        return data


def resolve_hosts_reference(hosts_ref, scenario_inventory, context, broker_inst=None):  # noqa: PLR0911
    """Resolve a hosts reference to a list of host objects.

    Args:
        hosts_ref: Either 'scenario_inventory', 'inventory', or an inventory filter expression.
            Filter expressions can use:
            - @inv: Filter against Broker's main inventory
            - @scenario_inv: Filter against the scenario's inventory
        scenario_inventory: List of hosts checked out by this scenario
        context: Template context for rendering
        broker_inst: Optional Broker instance for reconstructing hosts from inventory data

    Returns:
        List of host objects
    """
    if hosts_ref == "scenario_inventory":
        return scenario_inventory.copy()

    # Check if it's the main Broker inventory
    if hosts_ref == "inventory":
        inv_data = helpers.load_inventory()
        if broker_inst and inv_data:
            return [broker_inst.reconstruct_host(h) for h in inv_data]
        return inv_data

    # Check if it's a filter expression for the scenario inventory
    if "@scenario_inv" in hosts_ref:
        return helpers.eval_filter(scenario_inventory, hosts_ref, filter_key="scenario_inv")

    # Check if it's a filter expression for the main inventory
    if "@inv" in hosts_ref:
        inv_data = helpers.load_inventory()
        filtered = helpers.eval_filter(inv_data, hosts_ref, filter_key="inv")
        if broker_inst and filtered:
            return [broker_inst.reconstruct_host(h) for h in filtered]
        return filtered

    # Try to render as a template and see if it's a variable
    rendered = render_template(hosts_ref, context)
    if isinstance(rendered, list):
        return rendered

    return []


class StepMemory:
    """Memory storage for a single step's execution state."""

    def __init__(self, name):
        self.name = name
        self.output = None
        self.status = "pending"
        self._broker_inst = None
        self._error = None

    def to_dict(self):
        """Convert step memory to a dictionary for template access."""
        return {
            "name": self.name,
            "output": self.output,
            "status": self.status,
        }

    def __getitem__(self, key):
        """Allow dictionary-style access for template compatibility."""
        return getattr(self, key, None)

    def get(self, key, default=None):
        """Get attribute with default value."""
        return getattr(self, key, default)


class ScenarioRunner:
    """Main class for loading, validating, and executing scenario files.

    A ScenarioRunner handles the complete lifecycle of a scenario:
    - Loading and validating the YAML file
    - Setting up configuration and variables
    - Executing steps in sequence
    - Managing the scenario-specific inventory
    - Handling errors and cleanup
    """

    def __init__(self, scenario_path, cli_vars=None, cli_config=None):
        """Initialize the ScenarioRunner.

        Args:
            scenario_path: Path to the scenario YAML file
            cli_vars: Dictionary of variables passed via CLI that override scenario variables
            cli_config: Dictionary of config overrides passed via CLI (e.g., config.settings.X)
        """
        self.scenario_path = Path(scenario_path)
        self.scenario_name = self.scenario_path.stem
        self.cli_vars = cli_vars or {}
        self.cli_config = cli_config or {}

        # Load and validate the scenario file
        self.data = self._load_and_validate()

        # Initialize configuration
        self.config = self.data.get("config", {})
        self._apply_cli_config_overrides()

        # Create settings object with scenario config merged
        user_settings = self.config.get("settings", {})
        self._settings = create_settings(config_dict=user_settings, skip_validation=True)

        # Initialize variables (scenario vars, then CLI overrides on top)
        self.variables = self.data.get("variables", {}).copy()
        # Apply CLI variable overrides with type conversion
        self._apply_cli_var_overrides()

        # Steps memory: mapping step name -> StepMemory
        self.steps_memory = {}

        # Scenario inventory: hosts checked out by this scenario
        self.scenario_inventory = []

        # Inventory file path for persistence
        self.inventory_path = self._get_inventory_path()

        # Setup scenario-specific file logging
        self._setup_logging()

        logger.debug(f"Initialized scenario: {self.scenario_name}")

    def _load_and_validate(self):
        """Load the scenario YAML file and validate against the schema.

        Returns:
            Parsed scenario data dictionary

        Raises:
            ScenarioError: If the file cannot be loaded or validation fails
        """
        if not self.scenario_path.exists():
            raise ScenarioError(f"Scenario file not found: {self.scenario_path}")

        try:
            with self.scenario_path.open() as f:
                data = yaml.load(f)
        except Exception as e:
            raise ScenarioError(f"Failed to parse scenario file: {e}") from e

        # Validate against schema if available
        schema = get_schema()
        if schema:
            try:
                jsonschema.validate(instance=data, schema=schema)
            except jsonschema.ValidationError as e:
                raise ScenarioError(f"Scenario validation failed: {e.message}") from e

        return data

    def _apply_cli_config_overrides(self):
        """Apply CLI config overrides to the scenario config.

        CLI config values are specified as dotted paths like:
        config.settings.AnsibleTower.workflow_timeout=500
        """
        for key, value in self.cli_config.items():
            # Remove 'config.' prefix if present
            config_key = key[7:] if key.startswith("config.") else key

            # Navigate the config dict and set the value
            parts = config_key.split(".")
            target = self.config
            for part in parts[:-1]:
                if part not in target:
                    target[part] = {}
                target = target[part]
            target[parts[-1]] = value

    def _convert_cli_value(self, cli_value, original_value, var_name):
        """Convert a CLI string value to match the type of the original value.

        Args:
            cli_value: String value from CLI
            original_value: Original value from scenario YAML
            var_name: Name of the variable (for logging)

        Returns:
            Converted value, or original cli_value if conversion fails
        """
        original_type = type(original_value)

        # If original is None or already the right type, keep as-is
        if original_value is None or isinstance(cli_value, original_type):
            return cli_value

        try:
            if original_type is bool:
                lower_val = str(cli_value).lower()
                bool_map = {
                    "true": True,
                    "1": True,
                    "yes": True,
                    "on": True,
                    "false": False,
                    "0": False,
                    "no": False,
                    "off": False,
                }
                if lower_val in bool_map:
                    return bool_map[lower_val]
                logger.warning(
                    f"Could not convert CLI value '{cli_value}' to bool for '{var_name}', "
                    f"keeping as string"
                )
            elif original_type is int:
                return int(cli_value)
            elif original_type is float:
                return float(cli_value)
            elif original_type in (list, dict):
                # For collections, try to parse as JSON if it looks like JSON
                if isinstance(cli_value, str) and cli_value and cli_value[0] in ("{", "["):
                    return json.loads(cli_value)
                logger.warning(
                    f"CLI value for '{var_name}' doesn't appear to be valid {original_type.__name__} JSON, "
                    f"keeping as string"
                )
        except (ValueError, TypeError, IndexError) as e:
            logger.warning(
                f"Failed to convert CLI value '{cli_value}' to {original_type.__name__} for '{var_name}': {e}. "
                f"Keeping as string."
            )
        return cli_value

    def _apply_cli_var_overrides(self):
        """Apply CLI variable overrides with type conversion.

        CLI variables are passed as strings. This method attempts to convert them
        to match the type of the original scenario variable. If the original variable
        doesn't exist or conversion fails, the string value is kept with a warning.
        """
        for key, cli_value in self.cli_vars.items():
            if key not in self.variables:
                # New variable from CLI - keep as string
                self.variables[key] = cli_value
            else:
                # Convert to match original type
                self.variables[key] = self._convert_cli_value(cli_value, self.variables[key], key)

    def _get_inventory_path(self):
        """Get the path for the scenario-specific inventory file.

        Returns:
            Path object for the inventory file
        """
        if inv_path := self.config.get("inventory_path"):
            return Path(inv_path).expanduser()
        return BROKER_DIRECTORY / f"scenario_{self.scenario_name}_inventory.yaml"

    def _setup_logging(self):
        """Set up scenario-specific file logging.

        Path resolution rules for log_path config:
        - If not specified: Use the default broker.log file (no reconfiguration)
        - If filename only (no /): Use BROKER_DIRECTORY/logs/{filename}
        - If absolute path with filename: Use as-is
        - If absolute directory path: Use {path}/{scenario_name}.log
        """
        log_path_config = self.config.get("log_path")
        if not log_path_config:
            # Use default broker.log - no reconfiguration needed
            return

        log_path = Path(log_path_config)

        if not log_path.is_absolute():
            # Filename only - place in BROKER_DIRECTORY/logs/
            resolved_path = BROKER_DIRECTORY / "logs" / log_path
        elif log_path.suffix:
            # Absolute path with filename extension - use as-is
            resolved_path = log_path
        else:
            # Absolute directory path - use {path}/{scenario_name}.log
            resolved_path = log_path / f"{self.scenario_name}.log"

        # Ensure the parent directory exists
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

        # Reconfigure logging to use the custom log file
        setup_logging(log_path=resolved_path)

    def _reconstruct_host_safe(self, host_data):
        """Safely reconstruct a host from inventory data.

        Args:
            host_data: Dictionary of host data from inventory

        Returns:
            Host object or None if reconstruction fails
        """
        try:
            return Broker(broker_settings=self._settings).reconstruct_host(host_data)
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Failed to reconstruct host from inventory: {e}")
            return None

    def _load_scenario_inventory(self):
        """Load existing scenario inventory from disk if it exists."""
        if self.inventory_path.exists():
            inv_data = helpers.load_file(self.inventory_path, warn=False)
            if inv_data:
                # Reconstruct host objects from the inventory data
                hosts = [self._reconstruct_host_safe(h) for h in inv_data]
                self.scenario_inventory.extend(h for h in hosts if h is not None)

    def _clear_scenario_inventory(self):
        """Clear scenario inventory for a fresh run."""
        self.scenario_inventory.clear()
        if self.inventory_path.exists():
            self.inventory_path.unlink()
            logger.debug(f"Cleared existing scenario inventory: {self.inventory_path}")

    def _save_scenario_inventory(self):
        """Save the current scenario inventory to disk."""
        inv_data = [host.to_dict() for host in self.scenario_inventory]
        self.inventory_path.parent.mkdir(parents=True, exist_ok=True)
        self.inventory_path.touch()
        yaml.dump(inv_data, self.inventory_path)

    def _build_context(self, current_step_name, previous_step_memory):
        """Build the Jinja2 template context for a step.

        Args:
            current_step_name: Name of the current step
            previous_step_memory: StepMemory of the previous step (or None)

        Returns:
            Dictionary context for template rendering
        """
        # Create a dict-like wrapper for steps that allows both attribute and dict access
        steps_dict = dict(self.steps_memory.items())

        return {
            "step": self.steps_memory.get(current_step_name),
            "previous_step": previous_step_memory,
            "steps": steps_dict,
            "scenario_inventory": self.scenario_inventory,
            **self.variables,
        }

    def _execute_step(self, step_data, previous_step_memory):  # noqa: PLR0912, PLR0915
        """Execute a single step.

        Args:
            step_data: Dictionary containing step configuration
            previous_step_memory: StepMemory of the previous step

        Returns:
            StepMemory for this step
        """
        step_name = step_data["name"]
        action = step_data["action"]

        # Initialize or reset step memory
        if step_name not in self.steps_memory:
            self.steps_memory[step_name] = StepMemory(step_name)
        step_mem = self.steps_memory[step_name]
        step_mem.status = "running"

        # Build context for template rendering
        context = self._build_context(step_name, previous_step_memory)

        # Check 'when' condition only if there's no loop
        # (for loops, the condition is evaluated per-iteration inside _execute_loop)
        if "when" in step_data and "loop" not in step_data:
            try:
                should_run = evaluate_condition(step_data["when"], context)
                if not should_run:
                    logger.info(f"Skipping step '{step_name}' due to condition")
                    step_mem.status = "skipped"
                    return step_mem
            except (jinja2.TemplateError, ScenarioError) as e:
                logger.warning(f"Error evaluating 'when' condition for '{step_name}': {e}")
                step_mem.status = "skipped"
                return step_mem

        logger.info(f"Executing step: {step_name} (action: {action})")

        try:
            # Resolve target hosts if 'with' is specified
            target_hosts = None
            if "with" in step_data:
                hosts_ref = step_data["with"]["hosts"]
                broker_inst = Broker(broker_settings=self._settings)
                target_hosts = resolve_hosts_reference(
                    hosts_ref, self.scenario_inventory, context, broker_inst
                )

            # Execute the action (loop or single)
            if "loop" in step_data:
                # For loops, pass raw arguments - they'll be rendered per-iteration
                # with loop variables available in context
                raw_arguments = step_data.get("arguments", {})
                result = self._execute_loop(step_data, raw_arguments, target_hosts, context)
            else:
                # Render arguments with template context
                arguments = recursive_render(step_data.get("arguments", {}), context)
                parallel = step_data.get("parallel", True)
                result = self._dispatch_action(step_data, arguments, target_hosts, parallel)

            # Update step memory
            step_mem.output = result
            step_mem.status = "completed"

            # Handle capture
            if "capture" in step_data:
                self._capture_output(step_data["capture"], result, context)

        except Exception as e:
            logger.error(f"Step '{step_name}' failed: {e}")
            step_mem.output = str(e)
            step_mem._error = e
            step_mem.status = "failed"

            # Handle on_error - can be "continue" string or list of recovery steps
            on_error = step_data.get("on_error")
            if on_error == "continue":
                logger.warning(f"Step '{step_name}' failed but on_error=continue, continuing")
            elif isinstance(on_error, list):
                logger.info(f"Executing on_error handler for step '{step_name}'")
                try:
                    self._execute_steps(on_error)
                except SystemExit:
                    # Let SystemExit pass through (from exit action in error handler)
                    raise
                except Exception as handler_err:
                    # If the error handler itself failed, this is a secondary failure
                    # Re-raise it so it terminates the scenario
                    raise handler_err from e
            elif step_data.get("exit_on_error", True):
                raise ScenarioError(
                    f"Step failed: {e}",
                    step_name=step_name,
                    scenario_name=self.scenario_name,
                ) from e
            else:
                logger.warning(f"Step '{step_name}' failed but exit_on_error=False, continuing")

        return step_mem

    def _execute_steps(self, steps_list):
        """Execute a list of steps sequentially.

        Args:
            steps_list: List of step dictionaries to execute
        """
        previous_step = None

        for step_data in steps_list:
            step_mem = self._execute_step(step_data, previous_step)
            previous_step = step_mem

            # Save inventory after each step in case of failure
            self._save_scenario_inventory()

    def _execute_loop(self, step_data, base_arguments, target_hosts, context):  # noqa: PLR0912, PLR0915
        """Execute a step in a loop over an iterable.

        Args:
            step_data: Step configuration
            base_arguments: Base arguments to use for each iteration
            target_hosts: Target hosts (if any)
            context: Template context

        Returns:
            Dictionary mapping iteration items to their results
        """
        loop_config = step_data["loop"]
        iterable_expr = loop_config["iterable"]
        iter_var_name = loop_config["iter_var"]
        on_error = loop_config.get("on_error")

        # Resolve the iterable
        if "@scenario_inv" in iterable_expr:
            # Filter against scenario inventory
            resolved_iterable = helpers.eval_filter(
                self.scenario_inventory, iterable_expr, filter_key="scenario_inv"
            )
        elif "@inv" in iterable_expr:
            # Filter against main broker inventory and reconstruct hosts
            inv_data = helpers.load_inventory()
            filtered = helpers.eval_filter(inv_data, iterable_expr, filter_key="inv")
            broker_inst = Broker(broker_settings=self._settings)
            resolved_iterable = [broker_inst.reconstruct_host(h) for h in filtered]
        else:
            # Evaluate as a Jinja2 expression to get the actual Python object
            # Strip {{ }} if present, since evaluate_expression expects raw expression
            expr = iterable_expr.strip()
            if expr.startswith("{{") and expr.endswith("}}"):
                expr = expr[2:-2].strip()
            resolved_iterable = evaluate_expression(expr, context)

        # Ensure it's iterable - handle dicts specially
        if isinstance(resolved_iterable, dict):
            # Convert dict to list of tuples for iteration
            resolved_iterable = list(resolved_iterable.items())
        elif not isinstance(resolved_iterable, (list, tuple)):
            # Convert other iterables to a list, avoiding double-conversion of dict.items()
            try:
                # Convert to list once - dict_items, dict_keys, etc will be converted
                resolved_iterable = list(resolved_iterable)
            except TypeError:
                resolved_iterable = [resolved_iterable]

        # Parse iter_var - support "key, value" syntax for tuple unpacking
        iter_var_names = [v.strip() for v in iter_var_name.split(",")]

        # Check for capture.key to determine how to key the loop output
        capture_config = step_data.get("capture", {})
        capture_key_expr = capture_config.get("key")

        loop_output = {}

        for item in resolved_iterable:
            # Create a loop-specific context
            loop_context = context.copy()

            # Handle tuple unpacking for multiple iter_var names
            if len(iter_var_names) > 1:
                if isinstance(item, (list, tuple)) and len(item) >= len(iter_var_names):
                    for var_name, value in zip(iter_var_names, item):
                        loop_context[var_name] = value
                    default_loop_key = str(item[0]) if item else str(item)
                else:
                    raise ScenarioError(
                        f"Loop item cannot be unpacked: expected {len(iter_var_names)} "
                        f"values but got {item}. Ensure the iterable contains "
                        f"tuples/lists with {len(iter_var_names)} elements."
                    )
            else:
                loop_context[iter_var_names[0]] = item
                default_loop_key = str(item)

            # Check 'when' condition for this iteration (if present)
            when_condition = step_data.get("when")
            if when_condition:
                try:
                    should_run = evaluate_condition(when_condition, loop_context)
                    if not should_run:
                        logger.debug(f"Skipping loop iteration for {item} due to condition")
                        continue
                except (jinja2.TemplateError, ScenarioError) as e:
                    logger.warning(f"Error evaluating 'when' condition for iteration {item}: {e}")
                    continue

            # Re-render arguments with the loop variable
            iter_args = recursive_render(base_arguments, loop_context)

            try:
                result = self._dispatch_action(step_data, iter_args, target_hosts, parallel=False)

                # Determine the key for this iteration's result
                if capture_key_expr:
                    # Evaluate the key expression with loop context (including result)
                    key_context = loop_context.copy()
                    key_context["result"] = result
                    loop_key = str(render_template(f"{{{{ {capture_key_expr} }}}}", key_context))
                else:
                    loop_key = default_loop_key

                loop_output[loop_key] = result
            except Exception as e:
                if on_error == "continue":
                    logger.warning(f"Loop iteration failed for {item}: {e}, continuing...")
                    loop_output[default_loop_key] = {"error": str(e)}
                else:
                    raise

        return loop_output

    def _dispatch_action(self, step_data, arguments, hosts=None, parallel=True):  # noqa: PLR0911
        """Dispatch an action to the appropriate handler.

        Args:
            step_data: Step configuration
            arguments: Rendered arguments for the action
            hosts: Target hosts (if any)
            parallel: Whether to run in parallel for multi-host actions

        Returns:
            Result of the action
        """
        action = step_data["action"]
        step_name = step_data["name"]

        if action == "checkout":
            return self._action_checkout(step_name, arguments)
        elif action == "checkin":
            return self._action_checkin(arguments, hosts)
        elif action == "inventory":
            return self._action_inventory(arguments)
        elif action == "ssh":
            return self._action_ssh(arguments, hosts, parallel)
        elif action == "scp":
            return self._action_scp(arguments, hosts, parallel)
        elif action == "sftp":
            return self._action_sftp(arguments, hosts, parallel)
        elif action == "execute":
            return self._action_execute(step_name, arguments)
        elif action == "exit":
            return self._action_exit(arguments)
        elif action == "run_scenarios":
            return self._action_run_scenarios(arguments)
        elif action == "output":
            return self._action_output(arguments)
        elif action == "provider_info":
            return self._action_provider_info(step_name, arguments)
        else:
            raise ScenarioError(f"Unknown action: {action}")

    def _map_argument_names(self, arguments):
        """Map user-friendly argument names to Broker's internal names.

        Args:
            arguments: Dictionary of arguments from the scenario

        Returns:
            Dictionary with argument names mapped to Broker's expected names
        """
        mapped = {}
        for key, value in arguments.items():
            mapped_key = ARGUMENT_NAME_MAP.get(key, key)
            mapped[mapped_key] = value
        return mapped

    def _action_checkout(self, step_name, arguments):
        """Handle checkout action."""
        mapped_args = self._map_argument_names(arguments)
        broker_inst = Broker(broker_settings=self._settings, **mapped_args)
        self.steps_memory[step_name]._broker_inst = broker_inst

        hosts = broker_inst.checkout()
        if not isinstance(hosts, list):
            hosts = [hosts]

        # Add to scenario inventory
        self.scenario_inventory.extend(hosts)
        self._save_scenario_inventory()

        return hosts

    def _action_checkin(self, arguments, hosts):
        """Handle checkin action."""
        if not hosts:
            hosts = arguments.get("hosts", self.scenario_inventory)

        if not isinstance(hosts, list):
            hosts = [hosts]

        Broker(hosts=hosts, broker_settings=self._settings).checkin()

        # Remove from scenario inventory
        for host in hosts:
            if host in self.scenario_inventory:
                self.scenario_inventory.remove(host)
        self._save_scenario_inventory()

        return True

    def _action_inventory(self, arguments):
        """Handle inventory action."""
        if sync_provider := arguments.get("sync"):
            Broker.sync_inventory(provider=sync_provider, broker_settings=self._settings)
            return helpers.load_inventory()
        return helpers.load_inventory(filter=arguments.get("filter"))

    def _action_ssh(self, arguments, hosts, parallel):
        """Handle ssh (execute command) action.

        Returns:
            A single Result object if there's only one host,
            otherwise a dict mapping hostname to Result objects.
        """
        if not hosts:
            raise ScenarioError(
                "SSH action requires target hosts. Specify hosts for this step via "
                "the 'with' clause (e.g. 'with: { hosts: [...] }') or ensure that "
                "the scenario inventory contains hosts from a previous checkout or "
                "inventory action."
            )

        command = arguments.get("command")
        if not command:
            raise ScenarioError("SSH action requires 'command' argument")

        timeout = arguments.get("timeout")

        def run_on_host(host):
            return (host.hostname, host.execute(command, timeout=timeout))

        if len(hosts) == 1:
            # Return single result directly for easier template access
            return hosts[0].execute(command, timeout=timeout)

        # Multiple hosts: return dict mapping hostname to result
        if parallel:
            results = {}
            with ThreadPoolExecutor(max_workers=min(len(hosts), 10)) as executor:
                futures = [executor.submit(run_on_host, h) for h in hosts]
                for future in as_completed(futures):
                    hostname, result = future.result()
                    results[hostname] = result
            return results
        else:
            return {h.hostname: h.execute(command, timeout=timeout) for h in hosts}

    def _action_scp(self, arguments, hosts, parallel):
        """Handle scp action.

        Returns:
            A single Result object if there's only one host,
            otherwise a dict mapping hostname to Result objects.
        """
        if not hosts:
            raise ScenarioError("SCP action requires target hosts")

        source = arguments.get("source")
        destination = arguments.get("destination")
        if not source or not destination:
            raise ScenarioError("SCP action requires 'source' and 'destination' arguments")

        def scp_to_host(host):
            # Use sftp_write for uploading files
            host.session.sftp_write(source, destination)
            return (
                host.hostname,
                helpers.Result(stdout=f"Copied {source} to {destination}", stderr="", status=0),
            )

        if len(hosts) == 1:
            # Return single result directly for easier template access
            hosts[0].session.sftp_write(source, destination)
            return helpers.Result(stdout=f"Copied {source} to {destination}", stderr="", status=0)

        # Multiple hosts: return dict mapping hostname to result
        if parallel:
            results = {}
            with ThreadPoolExecutor(max_workers=min(len(hosts), 10)) as executor:
                futures = [executor.submit(scp_to_host, h) for h in hosts]
                for future in as_completed(futures):
                    hostname, result = future.result()
                    results[hostname] = result
            return results
        else:
            return dict(scp_to_host(h) for h in hosts)

    def _action_sftp(self, arguments, hosts, parallel):
        """Handle sftp action.

        Returns:
            A single Result object if there's only one host,
            otherwise a dict mapping hostname to Result objects.
        """
        if not hosts:
            raise ScenarioError("SFTP action requires target hosts")

        source = arguments.get("source")
        destination = arguments.get("destination")
        direction = arguments.get("direction", "upload")

        def sftp_on_host(host):
            if direction == "upload":
                host.session.sftp_write(source, destination)
                result = helpers.Result(
                    stdout=f"Uploaded {source} to {destination}", stderr="", status=0
                )
            else:
                host.session.sftp_read(source, destination)
                result = helpers.Result(
                    stdout=f"Downloaded {source} to {destination}", stderr="", status=0
                )
            return (host.hostname, result)

        def sftp_single(host):
            if direction == "upload":
                host.session.sftp_write(source, destination)
                return helpers.Result(
                    stdout=f"Uploaded {source} to {destination}", stderr="", status=0
                )
            else:
                host.session.sftp_read(source, destination)
                return helpers.Result(
                    stdout=f"Downloaded {source} to {destination}", stderr="", status=0
                )

        if len(hosts) == 1:
            # Return single result directly for easier template access
            return sftp_single(hosts[0])

        # Multiple hosts: return dict mapping hostname to result
        if parallel:
            results = {}
            with ThreadPoolExecutor(max_workers=min(len(hosts), 10)) as executor:
                futures = [executor.submit(sftp_on_host, h) for h in hosts]
                for future in as_completed(futures):
                    hostname, result = future.result()
                    results[hostname] = result
            return results
        else:
            return dict(sftp_on_host(h) for h in hosts)

    def _action_execute(self, step_name, arguments):
        """Handle execute action (provider action)."""
        broker_inst = Broker(broker_settings=self._settings, **arguments)
        self.steps_memory[step_name]._broker_inst = broker_inst
        return broker_inst.execute()

    def _action_exit(self, arguments):
        """Handle exit action."""
        return_code = int(arguments.get("return_code", 0))
        message = arguments.get("message", "Scenario exited explicitly")

        if return_code != 0:
            raise ScenarioError(f"Exit Action: {message} (code: {return_code})")

        logger.info(f"Exit Action: {message}")
        # Use a special exception to signal exit
        raise SystemExit(return_code)

    def _action_run_scenarios(self, arguments):
        """Handle run_scenarios action."""
        paths = arguments.get("paths", [])
        results = []

        for path in paths:
            runner = ScenarioRunner(
                scenario_path=path,
                cli_vars=self.cli_vars,
                cli_config=self.cli_config,
            )
            runner.run()
            results.append({"path": path, "success": True})

        return results

    def _action_output(self, arguments):
        """Handle output action - write content to stdout, stderr, or a file.

        Args:
            arguments: Dictionary containing:
                - content: The content to output (required). Can be a template string,
                    a variable name, or raw data.
                - destination: Where to write the output. Options:
                    - "stdout" (default): Write to standard output
                    - "stderr": Write to standard error
                    - A file path: Write/append to the specified file
                      (.json and .yaml/.yml files will be formatted appropriately)
                - mode: For file destinations only:
                    - "overwrite" (default): Overwrite existing file or create new
                    - "append": Append to existing file or create new

        Returns:
            The content that was written
        """
        content = arguments.get("content")
        if content is None:
            raise ScenarioError("Output action requires 'content' argument")

        # Check if content is a variable name (string that matches a captured variable)
        if isinstance(content, str) and content in self.variables:
            content = self.variables[content]

        destination = arguments.get("destination", "stdout")
        mode = arguments.get("mode", "overwrite")

        if destination == "stdout":
            # Convert content to string for stdout
            if not isinstance(content, str):
                content = helpers.yaml_format(content)
            sys.stdout.write(content)
            if not content.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()
        elif destination == "stderr":
            # Convert content to string for stderr
            if not isinstance(content, str):
                content = helpers.yaml_format(content)
            sys.stderr.write(content)
            if not content.endswith("\n"):
                sys.stderr.write("\n")
            sys.stderr.flush()
        else:
            # Treat as file path - use save_file helper for format-aware saving
            helpers.save_file(destination, content, mode=mode)
            logger.debug(f"Wrote output to file: {destination}")

        return content

    def _action_provider_info(self, step_name, arguments):
        """Handle provider_info action - query provider for available resources.

        Args:
            step_name: Name of the current step
            arguments: Dictionary containing:
                - provider: The provider name (required), e.g., "AnsibleTower", "Container"
                - query: What to query. Can be:
                    - A string for flag-style queries: "workflows", "inventories", etc.
                    - A dict for value-style queries: {"workflow": "my-workflow"}
                - Additional provider-specific arguments (e.g., tower_inventory)

        Returns:
            The data returned by the provider's provider_help method

        Example YAML:
            - name: List workflows
              action: provider_info
              arguments:
                provider: AnsibleTower
                query: workflows
                tower_inventory: my-inventory

            - name: Get workflow details
              action: provider_info
              arguments:
                provider: AnsibleTower
                query:
                  workflow: my-workflow-name
        """
        provider_name = arguments.get("provider")
        if not provider_name:
            raise ScenarioError("provider_info action requires 'provider' argument")

        query = arguments.get("query")
        if not query:
            raise ScenarioError("provider_info action requires 'query' argument")

        # Get the provider class
        provider_cls = PROVIDERS.get(provider_name)
        if not provider_cls:
            available = ", ".join(PROVIDERS.keys())
            raise ScenarioError(f"Unknown provider: {provider_name}. Available: {available}")

        # Build kwargs for provider_help
        # Start with all arguments except 'provider' and 'query'
        help_kwargs = {k: v for k, v in arguments.items() if k not in ("provider", "query")}

        # Process the query argument
        if isinstance(query, str):
            # Flag-style query: query: "workflows" -> workflows=True
            help_kwargs[query] = True
        elif isinstance(query, dict):
            # Value-style query: query: {workflow: "name"} -> workflow="name"
            help_kwargs.update(query)
        else:
            raise ScenarioError(f"Invalid query type: {type(query)}. Expected str or dict.")

        # Instantiate the provider and call provider_help
        try:
            provider_inst = provider_cls(broker_settings=self._settings)
            self.steps_memory[step_name]._broker_inst = provider_inst
            result = provider_inst.provider_help(**help_kwargs)
            return result
        except Exception as e:
            raise ScenarioError(f"provider_info failed for {provider_name}: {e}") from e

    def _capture_output(self, capture_config, result, context):
        """Capture step output into a variable.

        Args:
            capture_config: Configuration for capture (as, transform)
            result: The step result to capture
            context: Template context
        """
        var_name = capture_config["as"]
        transform = capture_config.get("transform")

        value_to_store = result

        if transform:
            # Create a temporary context with step.output set to result
            temp_context = context.copy()
            if temp_context.get("step"):
                # Make a copy to avoid modifying the original
                step_dict = temp_context["step"].to_dict()
                step_dict["output"] = result
                temp_context["step"] = step_dict

            value_to_store = render_template(transform, temp_context)

        self.variables[var_name] = value_to_store
        logger.debug(f"Captured variable '{var_name}'")

    def run(self):
        """Execute the scenario.

        This is the main entry point for running a scenario.

        Raises:
            ScenarioError: If the scenario execution fails
        """
        logger.info(f"Starting scenario: {self.scenario_name}")

        # Clear any existing scenario inventory for a fresh run
        self._clear_scenario_inventory()

        try:
            self._execute_steps(self.data.get("steps", []))
            logger.info(f"Scenario '{self.scenario_name}' completed successfully")
        except SystemExit as e:
            # Normal exit from exit action
            logger.info(f"Scenario '{self.scenario_name}' exited with code {e.code}")
            if e.code != 0:
                raise ScenarioError(
                    f"Exited with non-zero code: {e.code}",
                    scenario_name=self.scenario_name,
                )
        except ScenarioError:
            raise
        except Exception as e:
            raise ScenarioError(f"Unexpected error: {e}", scenario_name=self.scenario_name) from e

    def get_info(self):
        """Get summary information about the scenario.

        Returns:
            Dictionary containing scenario metadata
        """
        return {
            "name": self.scenario_name,
            "path": str(self.scenario_path),
            "config": self.config,
            "variables": self.data.get("variables", {}),
            "steps": [
                {"name": s["name"], "action": s["action"]} for s in self.data.get("steps", [])
            ],
        }


def validate_scenario(scenario_path):
    """Validate a scenario file against the schema.

    Args:
        scenario_path: Path to the scenario file

    Returns:
        Tuple of (is_valid, error_message or None)
    """
    path = Path(scenario_path)
    if not path.exists():
        return False, f"Scenario file not found: {scenario_path}"

    try:
        with path.open() as f:
            data = yaml.load(f)
    except (OSError, yaml.YAMLError) as e:
        return False, f"Failed to parse YAML: {e}"

    schema = get_schema()
    if not schema:
        return True, "Schema not found, skipping validation"

    try:
        jsonschema.validate(instance=data, schema=schema)
        return True, None
    except jsonschema.ValidationError as e:
        return False, f"Validation error: {e.message}"
