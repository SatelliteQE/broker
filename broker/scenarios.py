"""Broker Scenarios module for chaining multiple Broker actions together.

This module provides functionality to execute scenario files that define
a sequence of Broker-based actions (checkout, checkin, execute, ssh, etc.)
with support for templating, looping, error handling, and variable capture.

Usage:
    runner = ScenarioRunner("/path/to/scenario.yaml", cli_vars={"MY_VAR": "value"})
    runner.run()
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import jinja2
import jsonschema
from ruamel.yaml import YAML

from broker import helpers
from broker.broker import Broker
from broker.exceptions import ScenarioError
from broker.settings import BROKER_DIRECTORY, create_settings

logger = logging.getLogger(__name__)

yaml = YAML()
yaml.default_flow_style = False
yaml.sort_keys = False

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

    scenarios = []
    for file in SCENARIOS_DIR.iterdir():
        if file.suffix in (".yaml", ".yml"):
            scenarios.append(file.stem)
    return sorted(scenarios)


def render_template(template_str, context):
    """Render a Jinja2 template string using the provided context.

    If the input is not a string or doesn't contain template syntax,
    it is returned as-is.

    Args:
        template_str: String that may contain Jinja2 template syntax
        context: Dictionary of variables for template rendering

    Returns:
        Rendered string or original value if not a template
    """
    if not isinstance(template_str, str):
        return template_str

    # Check if it looks like a template
    if "{{" not in template_str and "{%" not in template_str:
        return template_str

    try:
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        template = env.from_string(template_str)
        return template.render(**context)
    except jinja2.UndefinedError as e:
        logger.warning(f"Template rendering warning: {e}")
        raise ScenarioError(f"Undefined variable in template: {e}") from e


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


def resolve_hosts_reference(hosts_ref, scenario_inventory, context):
    """Resolve a hosts reference to a list of host objects.

    Args:
        hosts_ref: Either 'scenario_inventory' or an inventory filter expression
        scenario_inventory: List of hosts checked out by this scenario
        context: Template context for rendering

    Returns:
        List of host objects
    """
    if hosts_ref == "scenario_inventory":
        return scenario_inventory.copy()

    # Check if it's a filter expression (contains @inv)
    if "@inv" in hosts_ref:
        # Filter against the scenario inventory using Broker's filter
        return helpers.eval_filter(scenario_inventory, hosts_ref, filter_key="inv")

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
        self.variables.update(self.cli_vars)

        # Steps memory: mapping step name -> StepMemory
        self.steps_memory = {}

        # Scenario inventory: hosts checked out by this scenario
        self.scenario_inventory = []

        # Inventory file path for persistence
        self.inventory_path = self._get_inventory_path()

        # Setup scenario-specific file logging
        self._setup_logging()

        logger.info(f"Initialized scenario: {self.scenario_name}")

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
            if key.startswith("config."):
                key = key[7:]

            # Navigate the config dict and set the value
            parts = key.split(".")
            target = self.config
            for part in parts[:-1]:
                if part not in target:
                    target[part] = {}
                target = target[part]
            target[parts[-1]] = value

    def _get_inventory_path(self):
        """Get the path for the scenario-specific inventory file.

        Returns:
            Path object for the inventory file
        """
        if inv_path := self.config.get("inventory_path"):
            return Path(inv_path)
        return BROKER_DIRECTORY / f"scenario_{self.scenario_name}_inventory.yaml"

    def _setup_logging(self):
        """Setup scenario-specific file logging."""
        log_dir = BROKER_DIRECTORY / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        # Note: Actual file handler setup would be done here
        # For now, we rely on the main broker logger

    def _load_scenario_inventory(self):
        """Load existing scenario inventory from disk if it exists."""
        if self.inventory_path.exists():
            inv_data = helpers.load_file(self.inventory_path, warn=False)
            if inv_data:
                # Reconstruct host objects from the inventory data
                for host_data in inv_data:
                    try:
                        host = Broker(broker_settings=self._settings).reconstruct_host(host_data)
                        if host:
                            self.scenario_inventory.append(host)
                    except Exception as e:
                        logger.warning(f"Failed to reconstruct host from inventory: {e}")

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
        steps_dict = {}
        for name, mem in self.steps_memory.items():
            steps_dict[name] = mem

        return {
            "step": self.steps_memory.get(current_step_name),
            "previous_step": previous_step_memory,
            "steps": steps_dict,
            "scenario_inventory": self.scenario_inventory,
            **self.variables,
        }

    def _execute_step(self, step_data, previous_step_memory):
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

        # Check 'when' condition
        if "when" in step_data:
            try:
                should_run = evaluate_condition(step_data["when"], context)
                if not should_run:
                    logger.info(f"Skipping step '{step_name}' due to condition")
                    step_mem.status = "skipped"
                    return step_mem
            except Exception as e:
                logger.warning(f"Error evaluating 'when' condition for '{step_name}': {e}")
                step_mem.status = "skipped"
                return step_mem

        logger.info(f"Executing step: {step_name} (action: {action})")

        try:
            # Render arguments with template context
            arguments = recursive_render(step_data.get("arguments", {}), context)

            # Resolve target hosts if 'with' is specified
            target_hosts = None
            if "with" in step_data:
                hosts_ref = step_data["with"]["hosts"]
                target_hosts = resolve_hosts_reference(hosts_ref, self.scenario_inventory, context)

            # Execute the action (loop or single)
            if "loop" in step_data:
                result = self._execute_loop(step_data, arguments, target_hosts, context)
            else:
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

            # Handle on_error steps
            if "on_error" in step_data:
                logger.info(f"Executing on_error handler for step '{step_name}'")
                try:
                    self._execute_steps(step_data["on_error"])
                except Exception as handler_err:
                    logger.error(f"on_error handler also failed: {handler_err}")
                    raise handler_err from e
            elif step_data.get("exit_on_error", True):
                raise ScenarioError(f"Step '{step_name}' failed and exit_on_error is True") from e
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

    def _execute_loop(self, step_data, base_arguments, target_hosts, context):
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
        if "@inv" in iterable_expr:
            # It's an inventory filter
            resolved_iterable = helpers.eval_filter(
                self.scenario_inventory, iterable_expr, filter_key="inv"
            )
        else:
            # Try to render as a template
            resolved_iterable = recursive_render(iterable_expr, context)

        # Ensure it's a list
        if not isinstance(resolved_iterable, (list, tuple)):
            resolved_iterable = [resolved_iterable]

        loop_output = {}

        for item in resolved_iterable:
            # Create a loop-specific context
            loop_context = context.copy()
            loop_context[iter_var_name] = item

            # Re-render arguments with the loop variable
            iter_args = recursive_render(base_arguments, loop_context)

            try:
                result = self._dispatch_action(step_data, iter_args, target_hosts, parallel=False)
                loop_output[str(item)] = result
            except Exception as e:
                if on_error == "continue":
                    logger.warning(f"Loop iteration failed for {item}: {e}, continuing...")
                    loop_output[str(item)] = {"error": str(e)}
                else:
                    raise

        return loop_output

    def _dispatch_action(self, step_data, arguments, hosts=None, parallel=True):
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
        else:
            raise ScenarioError(f"Unknown action: {action}")

    def _action_checkout(self, step_name, arguments):
        """Handle checkout action."""
        broker_inst = Broker(broker_settings=self._settings, **arguments)
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
        """Handle ssh (execute command) action."""
        if not hosts:
            raise ScenarioError("SSH action requires target hosts")

        command = arguments.get("command")
        if not command:
            raise ScenarioError("SSH action requires 'command' argument")

        timeout = arguments.get("timeout")

        def run_on_host(host):
            return host.execute(command, timeout=timeout)

        if parallel and len(hosts) > 1:
            results = []
            with ThreadPoolExecutor(max_workers=len(hosts)) as executor:
                futures = {executor.submit(run_on_host, h): h for h in hosts}
                for future in as_completed(futures):
                    results.append(future.result())
            return results
        else:
            return [run_on_host(h) for h in hosts]

    def _action_scp(self, arguments, hosts, parallel):
        """Handle scp action."""
        if not hosts:
            raise ScenarioError("SCP action requires target hosts")

        source = arguments.get("source")
        destination = arguments.get("destination")
        if not source or not destination:
            raise ScenarioError("SCP action requires 'source' and 'destination' arguments")

        def scp_to_host(host):
            # Use sftp_write for uploading files
            host.session.sftp_write(source, destination)
            return helpers.Result(stdout=f"Copied {source} to {destination}", stderr="", status=0)

        if parallel and len(hosts) > 1:
            results = []
            with ThreadPoolExecutor(max_workers=len(hosts)) as executor:
                futures = {executor.submit(scp_to_host, h): h for h in hosts}
                for future in as_completed(futures):
                    results.append(future.result())
            return results
        else:
            return [scp_to_host(h) for h in hosts]

    def _action_sftp(self, arguments, hosts, parallel):
        """Handle sftp action."""
        if not hosts:
            raise ScenarioError("SFTP action requires target hosts")

        source = arguments.get("source")
        destination = arguments.get("destination")
        direction = arguments.get("direction", "upload")

        def sftp_on_host(host):
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

        if parallel and len(hosts) > 1:
            results = []
            with ThreadPoolExecutor(max_workers=len(hosts)) as executor:
                futures = {executor.submit(sftp_on_host, h): h for h in hosts}
                for future in as_completed(futures):
                    results.append(future.result())
            return results
        else:
            return [sftp_on_host(h) for h in hosts]

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

        # Load any existing scenario inventory
        self._load_scenario_inventory()

        try:
            self._execute_steps(self.data.get("steps", []))
            logger.info(f"Scenario '{self.scenario_name}' completed successfully")
        except SystemExit as e:
            # Normal exit from exit action
            logger.info(f"Scenario '{self.scenario_name}' exited with code {e.code}")
            if e.code != 0:
                raise ScenarioError(f"Scenario exited with non-zero code: {e.code}")
        except ScenarioError:
            raise
        except Exception as e:
            raise ScenarioError(f"Scenario failed: {e}") from e

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
    except Exception as e:
        return False, f"Failed to parse YAML: {e}"

    schema = get_schema()
    if not schema:
        return True, "Schema not found, skipping validation"

    try:
        jsonschema.validate(instance=data, schema=schema)
        return True, None
    except jsonschema.ValidationError as e:
        return False, f"Validation error: {e.message}"
