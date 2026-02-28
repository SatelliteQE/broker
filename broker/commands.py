"""Defines the CLI commands for Broker."""

import contextlib
from functools import wraps
import logging
from pathlib import Path
import signal
import sys

# CRITICAL: Import and setup basic logging BEFORE any other broker imports
# This captures import-time logs from metaclasses and module initialization
from broker.logging import setup_logging

setup_logging(console_level=logging.INFO)  # Basic setup until settings are loaded

from click_shell import shell
import requests
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table
import rich_click as click

from broker import exceptions, helpers, settings
from broker.broker import Broker
from broker.config_manager import ConfigManager
from broker.logging import LOG_LEVEL
from broker.providers import PROVIDER_ACTIONS, PROVIDER_HELP, PROVIDERS

# Now configure logging with actual settings
setup_logging(
    console_level=settings.settings.logging.console_level,
    file_level=settings.settings.logging.file_level,
    log_path=settings.settings.logging.log_path,
    structured=settings.settings.logging.structured,
)

# Get logger for this module
logger = logging.getLogger(__name__)

signal.signal(signal.SIGINT, helpers.handle_keyboardinterrupt)
CONSOLE = Console(no_color=settings.settings.less_colors)  # rich console for pretty printing

click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.COMMAND_GROUPS = {
    "broker": [
        {"name": "Core Actions", "commands": ["checkout", "checkin", "inventory"]},
        {
            "name": "Extras",
            "commands": ["execute", "extend", "providers", "config", "scenarios", "shell"],
        },
    ]
}


def guarded_command(group=None, *cli_args, **cli_kwargs):
    """Wrap commands with logging and exception handling."""
    if not group:
        group = cli  # default to the main cli group

    def decorator(func):
        @group.command(*cli_args, **cli_kwargs)
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                logger.log(LOG_LEVEL.TRACE.value, f"Calling {func=}(*{args=} **{kwargs=})")
                retval = func(*args, **kwargs)
                logger.log(
                    LOG_LEVEL.TRACE.value,
                    f"Finished {func=}(*{args=} **{kwargs=}) {retval=}",
                )
                helpers.emit(return_code=0)
                return retval
            except Exception as err:  # noqa: BLE001 -- we want to catch all exceptions
                if isinstance(err, exceptions.ScenarioError):
                    # Show full message for scenario errors since context is important
                    logger.error(f"Scenario failed: {err.message}")
                    CONSOLE.print(f"[red]Scenario failed:[/red] {err.message}")
                elif not isinstance(err, exceptions.BrokerError):
                    err = exceptions.BrokerError(err)
                    logger.error(f"Command failed: {err.message}")
                    CONSOLE.print(f"[red]Command failed:[/red] {err.message}")
                else:  # BrokerError children already log their messages
                    logger.error(f"Command failed due to: {type(err).__name__}")
                    CONSOLE.print(f"[red]Command failed due to:[/red] {type(err).__name__}")
                helpers.emit(return_code=err.error_code, error_message=str(err.message))
                sys.exit(err.error_code)

        return wrapper

    return decorator


def parse_labels(provider_labels):
    """Parse the provided label string and returns labels in a dict."""
    return {
        label[0]: "=".join(label[1:])
        for label in [kv_pair.split("=") for kv_pair in provider_labels.split(",")]
    }


def provider_options(command):
    """Apply provider-specific decorators to each command this decorates."""
    for prov in PROVIDERS.values():
        if not settings.settings.get(prov.__name__):
            continue
        for option in getattr(prov, f"_{command.__name__}_options"):
            command = option(command)
    return command


def populate_providers(click_group):
    """Populate the subcommands for providers subcommand using provider information.

    Providers become subcommands and their actions become arguments to their subcommand.

    Example:
        Usage: broker providers AnsibleTower [OPTIONS]

        Options:
        --workflows      Get available workflows
        --workflow TEXT  Get information about a workflow
        --help           Show this message and exit.

    Note: This currently only works for the default instance for each provider
    """
    for prov, prov_class in (pairs for pairs in PROVIDERS.items()):
        if not settings.settings.get(prov_class.__name__):
            continue

        @guarded_command(
            group=click_group,
            name=prov,
            context_settings={
                "allow_extra_args": True,
                "ignore_unknown_options": True,
            },
        )
        @click.pass_context
        def provider_cmd(ctx, *args, **kwargs):  # the actual subcommand
            """Get information about a provider's actions."""
            # add additional args flags to the kwargs
            for arg in ctx.args:
                if arg.startswith("--"):
                    kwargs[arg[2:]] = True
            # if additional arguments were passed, include them in the broker args
            # strip leading -- characters
            kwargs.update(
                {
                    (key[2:] if key.startswith("--") else key): val
                    for key, val in zip(ctx.args[::2], ctx.args[1::2])
                }
            )
            broker_inst = Broker(**kwargs)
            broker_inst.provider_help(ctx.info_name)

        # iterate through available actions and populate options from them
        for option, p_cls, is_flag, alt_text in PROVIDER_HELP:
            if p_cls is not prov_class:
                continue
            option = option.replace("_", "-")  # noqa: PLW2901
            if is_flag:
                provider_cmd = click.option(
                    f"--{option}", is_flag=True, help=alt_text or f"Get available {option}"
                )(provider_cmd)
            else:
                provider_cmd = click.option(
                    f"--{option}", type=str, help=alt_text or f"Get information about a {option}"
                )(provider_cmd)
        provider_cmd = click.option(
            "--results-limit",
            type=int,
            help="The maximum number of results to get back",
        )(provider_cmd)
        provider_cmd = click.option(
            "--results-filter",
            type=str,
            help="Apply a broker filter to returned results",
        )(provider_cmd)


@click.group(
    invoke_without_command=True,
    no_args_is_help=True,
)
@click.option(
    "--log-level",
    type=click.Choice(["info", "warning", "error", "debug", "trace", "silent"]),
    default=settings.settings.logging.console_level,
    callback=helpers.update_log_level,
    is_eager=True,
    expose_value=False,
)
@click.option(
    "--output-file",
    type=click.Path(dir_okay=False),
    callback=helpers.set_emit_file,
    is_eager=True,
    expose_value=False,
    help="Path to file where emitted json values should be stored",
)
@click.option(
    "--version",
    is_flag=True,
    help="Get broker system-level information",
)
def cli(version):
    """Command-line interface for interacting with providers."""
    if version:
        from packaging.version import Version
        import requests

        # Check against the latest version published to PyPi
        try:
            latest_version = Version(
                requests.get("https://pypi.org/pypi/broker/json", timeout=60).json()["info"][
                    "version"
                ]
            )
            if latest_version > Version(ConfigManager.version):
                click.secho(
                    f"A newer version of broker is available: {latest_version}",
                    fg="yellow",
                )
        except requests.exceptions.RequestException as err:
            logger.warning(f"Unable to check for latest version: {err}")

        # Create a rich table
        table = Table(title=f"Broker {ConfigManager.version}")

        table.add_column("", justify="left", style="cyan", no_wrap=True)
        table.add_column("Location", justify="left", style="magenta")

        table.add_row("Broker Directory", str(settings.BROKER_DIRECTORY.absolute()))
        table.add_row("Scenarios Directory", f"{settings.BROKER_DIRECTORY.absolute()}/scenarios")
        table.add_row("Settings File", str(settings.settings_path.absolute()))
        table.add_row("Inventory File", f"{settings.BROKER_DIRECTORY.absolute()}/inventory.yaml")
        table.add_row("Log File", f"{settings.BROKER_DIRECTORY.absolute()}/logs/broker.log")

        # Print the table
        CONSOLE.print(table)


@guarded_command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
@click.option("-b", "--background", is_flag=True, help="Run checkout in the background")
@click.option("-n", "--nick", type=str, help="Use a nickname defined in your settings")
@click.option("-c", "--count", type=int, help="Number of times broker repeats the checkout")
@click.option(
    "-l",
    "--provider-labels",
    type=str,
    help="A string representing the list"
    " of k=v pairs (comma-separated) to be used as provider resource"
    " labels (e.g. '-l k1=v1,k2=v2,k3=v3=z4').",
)
@click.option(
    "--args-file",
    type=click.Path(exists=True),
    help="A json or yaml file mapping arguments to values",
)
@provider_options
@click.pass_context
def checkout(ctx, background, nick, count, args_file, provider_labels, **kwargs):
    """Checkout or "create" a Virtual Machine broker instance.

    COMMAND: broker checkout --workflow "workflow-name" --workflow_arg1 something

    COMMAND: broker checkout --nick "nickname"

    You can also checkout against a non-default provider instance, e.g:

    COMMAND: broker checkout ... --AnsibleTower <instance name>
    """
    broker_args = helpers.clean_dict(kwargs)
    if nick:
        broker_args["nick"] = nick
    if count:
        broker_args["_count"] = count
    if args_file:
        broker_args["args_file"] = args_file
    if provider_labels:
        broker_args["provider_labels"] = parse_labels(provider_labels)

    broker_args.update(helpers.kwargs_from_click_ctx(ctx))
    if background:
        helpers.fork_broker()
    Broker(**broker_args).checkout()


@cli.group()
def providers():
    """Get information about a provider and its actions."""
    pass


populate_providers(providers)


@guarded_command()
@click.argument("hosts", type=str, nargs=-1)
@click.option("-b", "--background", is_flag=True, help="Run checkin in the background")
@click.option("--all", "all_", is_flag=True, help="Select all hosts")
@click.option("--sequential", is_flag=True, help="Run checkins sequentially")
@click.option("--filter", type=str, help="Checkin only what matches the specified filter")
def checkin(hosts, background, all_, sequential, filter):
    """Checkin or "remove" a host or series of hosts.

    COMMAND: broker checkin <hostname>|<local id>|--all
    """
    if background:
        helpers.fork_broker()
    inventory = helpers.load_inventory(filter=filter)
    to_remove = []
    unmatched = set(hosts)  # Track unmatched hosts

    for num, host in enumerate(inventory):
        # Check if this host matches any of our criteria
        if (
            all_
            or str(num) in unmatched
            or host.get("hostname") in unmatched
            or host.get("name") in unmatched
        ):
            to_remove.append(Broker().reconstruct_host(host))
            # Remove all possible match values for this host from unmatched
            unmatched.discard(str(num))
            unmatched.discard(host.get("hostname"))
            unmatched.discard(host.get("name"))

    if unmatched:
        logger.warning(
            "The following hosts were not found in inventory: %s",
            ", ".join(unmatched),
        )
        CONSOLE.print(
            f"[yellow]Warning:[/yellow] The following hosts were not found in inventory: {', '.join(unmatched)}"
        )
    if to_remove:
        Broker(hosts=to_remove).checkin(sequential=sequential)


@guarded_command()
@click.option("--details", is_flag=True, help="Display all host details")
@click.option("--list", "_list", is_flag=True, help="Display only hostnames and local ids")
@click.option(
    "--sync",
    type=str,
    help="Class-style name of a supported broker provider. (AnsibleTower)",
)
@click.option("--filter", type=str, help="Display only what matches the specified filter")
def inventory(details, _list, sync, filter):
    """Display a table of hosts in your local inventory.

    Inventory fields are configurable in Broker's settings file.

    Run a sync for your providers to pull down your host information.

    e.g. `broker inventory --sync AnsibleTower`

    Note: Applying a filter will result in incorrect id's being displayed.
    """
    if sync:
        Broker.sync_inventory(provider=sync)
    inventory = helpers.load_inventory(filter=filter)
    helpers.emit({"inventory": inventory})
    # details is handled differently than the normal and list views
    if details:
        detailed = helpers.yaml_format(dict(enumerate(inventory)))
        CONSOLE.print(Syntax(detailed, "yaml", background_color="default"))
        return

    inventory_fields = (
        {"Host": settings.settings.inventory_list_vars}
        if _list
        else settings.settings.inventory_fields
    )
    curated_host_info = [
        helpers.inventory_fields_to_dict(
            inventory_fields=inventory_fields,
            host_dict=host,
            provider_actions=PROVIDER_ACTIONS,
        )
        for host in inventory
    ]
    if not curated_host_info:
        CONSOLE.print("No hosts found in inventory.")
        return
    table = helpers.dictlist_to_table(curated_host_info, "Host Inventory", _id=True)
    if _list:
        table.title = None
        table.box = None
    CONSOLE.print(table)


@guarded_command()
@click.argument("hosts", type=str, nargs=-1)
@click.option("-b", "--background", is_flag=True, help="Run extend in the background")
@click.option("--all", "all_", is_flag=True, help="Select all hosts")
@click.option("--sequential", is_flag=True, help="Run extends sequentially")
@click.option("--filter", type=str, help="Extend only what matches the specified filter")
@click.option(
    "-l",
    "--provider-labels",
    type=str,
    help="A string representing the list"
    " of k=v pairs (comma-separated) to be used as provider resource"
    " labels (e.g. '-l k1=v1,k2=v2,k3=v3=z4').",
)
@provider_options
def extend(hosts, background, all_, sequential, filter, **kwargs):
    """Extend a host's lease time.

    COMMAND: broker extend <hostname>|<host name>|<local id>|--all
    """
    broker_args = helpers.clean_dict(kwargs)
    if background:
        helpers.fork_broker()
    inventory = helpers.load_inventory(filter=filter)
    to_extend = []
    for num, host in enumerate(inventory):
        if str(num) in hosts or host["hostname"] in hosts or host.get("name") in hosts or all_:
            to_extend.append(Broker().reconstruct_host(host))
    Broker(hosts=to_extend, **broker_args).extend(sequential=sequential)


@guarded_command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
@click.option("-b", "--background", is_flag=True, help="Run execute in the background")
@click.option("--nick", type=str, help="Use a nickname defined in your settings")
@click.option("--output-format", "-o", type=click.Choice(["log", "raw", "yaml"]), default="log")
@click.option(
    "--artifacts",
    type=click.Choice(["merge", "last"]),
    help="AnsibleTower: return artifacts associated with the execution.",
)
@click.option(
    "--args-file",
    type=click.Path(exists=True),
    help="A json or yaml file mapping arguments to values",
)
@click.option(
    "-l",
    "--provider-labels",
    type=str,
    help="A string representing the list"
    " of k=v pairs (comma-separated) to be used as provider resource"
    " labels (e.g. '-l k1=v1,k2=v2,k3=v3=z4').",
)
@provider_options
@click.pass_context
def execute(ctx, background, nick, output_format, artifacts, args_file, provider_labels, **kwargs):
    """Execute an arbitrary provider action.

    COMMAND: broker execute --workflow "workflow-name" --workflow_arg1 something

    COMMAND: broker execute --nick "nickname"
    """
    broker_args = helpers.clean_dict(kwargs)
    if nick:
        broker_args["nick"] = nick
    if artifacts:
        broker_args["artifacts"] = artifacts
    if args_file:
        broker_args["args_file"] = args_file
    if provider_labels:
        broker_args["provider_labels"] = parse_labels(provider_labels)

    broker_args.update(helpers.kwargs_from_click_ctx(ctx))

    if background:
        helpers.fork_broker()
    result = Broker(**broker_args).execute()
    helpers.emit({"output": result})
    if output_format == "raw":
        click.echo(result)
    elif output_format == "log":
        logger.info(result)
        CONSOLE.print(result)
    elif output_format == "yaml":
        click.echo(helpers.yaml_format(result))


@cli.group()
def config():
    """View and manage Broker's configuration.

    Note: One important concept of these commands is the concept of a "chunk".

    A chunk is a part of the configuration file that can be accessed or updated.
    Chunks are specified by their keys in the configuration file.
    Nested chunks are separated by periods.

    e.g. broker config view AnsibleTower.instances.my_instance
    """


@guarded_command(group=config)
@click.argument("chunk", type=str, required=False)
@click.option("--no-syntax", is_flag=True, help="Disable syntax highlighting")
def view(chunk, no_syntax):
    """View all or part of the broker configuration."""
    result = helpers.yaml_format(ConfigManager(settings.settings_path).get(chunk))
    if no_syntax:
        CONSOLE.print(result)
    else:
        CONSOLE.print(Syntax(result, "yaml", background_color="default"))


@guarded_command(group=config)
@click.argument("chunk", type=str, required=False)
def edit(chunk):
    """Directly edit the broker configuration file.

    You can define the scope of the edit by specifying a chunk.
    Otherwise, the entire configuration file will be opened.
    """
    ConfigManager(settings.settings_path).edit(chunk)


@guarded_command(group=config, name="set")
@click.argument("chunk", type=str, required=True)
@click.argument("new-value", type=str, required=True)
def _set(chunk, new_value):
    """Set a value in the Broker configuration file.

    These updates take the form of `<chunk> <value>` pairs.
    You can also pass a yaml or json file containing the new contents of a chunk.
    """
    new_value = helpers.resolve_file_args({"nv": new_value})["nv"]
    ConfigManager(settings.settings_path).update(chunk, new_value)


@guarded_command(group=config)
def restore():
    """Restore the broker configuration file to the last backup."""
    ConfigManager(settings.settings_path).restore()


@guarded_command(group=config)
@click.argument("chunk", type=str, required=False)
@click.option("--from", "_from", type=str, help="A file path or URL to initialize the config from.")
def init(chunk=None, _from=None):
    """Initialize the broker configuration file from your local clone or GitHub.

    You can also init specific chunks by passing the chunk name.
    Additionally, if you want to initialize from a file or URL, you can pass the `--from` flag.
    Keep in mind that the file and url contents need to be valid yaml.
    """
    ConfigManager(settings.settings_path).init_config_file(chunk=chunk, _from=_from)


@guarded_command(group=config)
def nicks():
    """Get a list of nicks."""
    result = ConfigManager(settings.settings_path).nicks()
    CONSOLE.print("\n".join(result))


@guarded_command(group=config)
@click.argument("nick", type=str, required=True)
@click.option("--no-syntax", is_flag=True, help="Disable syntax highlighting")
def nick(nick, no_syntax):
    """Get information about a specific nick."""
    result = helpers.yaml_format(ConfigManager(settings.settings_path).nicks(nick))
    if no_syntax:
        CONSOLE.print(result)
    else:
        CONSOLE.print(Syntax(result, "yaml", background_color="default"))


@guarded_command(group=config)
@click.option("-f", "--force-version", type=str, help="Force the migration to a specific version")
def migrate(force_version=None):
    """Migrate the broker configuration file to the latest version."""
    ConfigManager(settings.settings_path).migrate(force_version=force_version)


@guarded_command(group=config)
@click.argument("chunk", type=str, required=False, default="base")
def validate(chunk):
    """Validate top-level chunks of the broker configuration file.

    You can validate against the `base` settings by default or specify a provider.
    You can also validate against a specific provider instance with `ProviderClass:instance_name`.

    To validate everything, pass `all`
    """
    try:
        ConfigManager(settings.settings_path).validate(chunk, PROVIDERS)
        logger.info("Validation passed!")
        CONSOLE.print("[green]Validation passed![/green]")
    except exceptions.BrokerError as err:
        logger.warning(f"Validation failed: {err}")
        CONSOLE.print(f"[yellow]Validation failed:[/yellow] {err}")


# --- Scenarios CLI Group ---


@cli.group()
def scenarios():
    """Manage and execute Broker scenarios.

    Scenarios allow you to chain multiple Broker actions together in a YAML file.
    """
    pass


@guarded_command(group=scenarios, name="list")
def scenarios_list():
    """List all available scenarios in the scenarios directory."""
    from broker.scenarios import SCENARIOS_DIR, list_scenarios
    from broker.settings import BROKER_DIRECTORY

    scenario_paths = list_scenarios()
    if not scenario_paths:
        CONSOLE.print(f"No scenarios found in {SCENARIOS_DIR}")
        return

    table = Table(title="Available Scenarios")
    table.add_column("Name", style="cyan")
    table.add_column("Path", style="dim")

    for rel_path in scenario_paths:
        p = Path(rel_path)
        name = p.name
        parent_dir = SCENARIOS_DIR / p.parent
        display_path = str(parent_dir.relative_to(BROKER_DIRECTORY))
        table.add_row(name, display_path)

    CONSOLE.print(table)


@guarded_command(
    group=scenarios,
    name="execute",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
@click.argument("scenario", type=str)
@click.option("-b", "--background", is_flag=True, help="Run scenario in the background")
@click.pass_context
def scenarios_execute(ctx, scenario, background):
    """Execute a scenario file.

    SCENARIO can be a name (found in scenarios dir) or a path to a YAML file.

    Additional arguments are passed as variable overrides:

        broker scenarios execute my_scenario --MY_VAR value --ANOTHER_VAR value

    Config overrides use dotted notation:

        broker scenarios execute my_scenario --config.settings.ssh.backend paramiko
    """
    from broker.scenarios import ScenarioRunner, find_scenario

    # Parse CLI args into variables and config overrides
    cli_vars = {}
    cli_config = {}
    extra_args = helpers.kwargs_from_click_ctx(ctx)

    for key, val in extra_args.items():
        if key.startswith("config."):
            cli_config[key] = val
        else:
            cli_vars[key] = val

    if background:
        helpers.fork_broker()

    scenario_path = find_scenario(scenario)
    runner = ScenarioRunner(
        scenario_path=scenario_path,
        cli_vars=cli_vars,
        cli_config=cli_config,
    )
    try:
        runner.run()
    finally:
        # Display scenario inventory if any hosts remain
        if runner.scenario_inventory:
            inv_data = [host.to_dict() for host in runner.scenario_inventory]
            curated_host_info = [
                helpers.inventory_fields_to_dict(
                    inventory_fields=settings.settings.inventory_fields,
                    host_dict=host,
                    provider_actions=PROVIDER_ACTIONS,
                )
                for host in inv_data
            ]
            table = helpers.dictlist_to_table(
                curated_host_info, "Scenario Inventory (hosts still checked out)", _id=True
            )
            CONSOLE.print(table)
            CONSOLE.print(f"[dim]Inventory file: {runner.inventory_path}[/dim]")


@guarded_command(group=scenarios)
@click.argument("scenario", type=str)
@click.option("--no-syntax", is_flag=True, help="Disable syntax highlighting")
def info(scenario, no_syntax):
    """Get information about a scenario.

    Displays the scenario's config, variables, and step names.
    """
    from broker.scenarios import ScenarioRunner, find_scenario

    scenario_path = find_scenario(scenario)
    runner = ScenarioRunner(scenario_path=scenario_path)
    info_data = runner.get_info()

    output = helpers.yaml_format(info_data)
    if no_syntax:
        CONSOLE.print(output)
    else:
        CONSOLE.print(Syntax(output, "yaml", background_color="default"))


@guarded_command(group=scenarios, name="validate")
@click.argument("scenario", type=str)
def scenarios_validate(scenario):
    """Validate a scenario file against the schema.

    Checks for syntax errors and schema violations.
    """
    from broker.scenarios import find_scenario, validate_scenario

    scenario_path = find_scenario(scenario)
    is_valid, error_msg = validate_scenario(scenario_path)

    if is_valid:
        CONSOLE.print(f"[green]Scenario '{scenario}' is valid![/green]")
        if error_msg:  # Schema not found message
            CONSOLE.print(f"[yellow]Warning:[/yellow] {error_msg}")
    else:
        CONSOLE.print(f"[red]Scenario '{scenario}' is invalid:[/red] {error_msg}")


def _display_imported_scenarios(manifest):
    """Display a table of imported scenarios."""
    if not manifest.get("imports"):
        CONSOLE.print("No imported scenarios found.")
        return

    table = Table(title="Imported Scenarios")
    table.add_column("Name", style="cyan")
    table.add_column("Source", style="blue", no_wrap=True)
    table.add_column("Imported", style="dim")
    table.add_column("Commit", style="dim")

    # Flatten the import entries to show individual scenarios
    for entry in manifest["imports"]:
        source_txt = entry.get("source", "")
        imported_at = entry.get("imported_at", "")
        commit_hash = (entry.get("resolved_commit") or "")[:7]

        # Format timestamp to be more human-readable (e.g., "2026-02-23" -> "Feb 23, 2026")
        formatted_date = imported_at
        if imported_at:
            try:
                from datetime import datetime

                # Parse ISO format and format it nicely
                dt = datetime.fromisoformat(imported_at.replace("Z", "+00:00"))
                formatted_date = dt.strftime("%b %d, %Y")
            except (ValueError, AttributeError):
                # Fallback to just the date part if parsing fails
                with contextlib.suppress(AttributeError):
                    formatted_date = imported_at.split("T")[0]

        # Add a row for each imported file
        for file_entry in entry.get("files", []):
            scenario_path = file_entry.get("path", "")
            # Extract scenario name from path (remove .yaml/.yml extension)
            scenario_name = Path(scenario_path).stem

            table.add_row(
                scenario_name,
                source_txt,
                formatted_date,
                commit_hash,
            )

    CONSOLE.print(table)


def _display_remote_scenarios(scenarios, source):
    """Display a table of remote scenarios."""
    table = Table(title=f"Remote Scenarios ({source})")
    table.add_column("Name", style="cyan")
    table.add_column("Categories", style="blue")
    table.add_column("Description", style="dim", max_width=60, overflow="fold")

    for s in scenarios:
        scenario_name = s.get("name", Path(s["path"]).stem)
        categories = ", ".join(s.get("categories", [])) if s.get("categories") else ""
        description = s.get("description", "")
        table.add_row(scenario_name, categories, description)

    CONSOLE.print(table)


def _check_local_modifications(dest_path, remote_path, tracked_files, force):
    """Check if a local file has been modified.

    Returns True if the import should proceed, False if it should be skipped.
    """
    from broker.scenarios import compute_sha256

    if not dest_path.exists():
        return True

    old_tracked_sha = tracked_files.get(remote_path)
    current_local_sha = compute_sha256(dest_path.read_bytes())

    # If we tracked a SHA and the current file differs -> local modification
    if old_tracked_sha and old_tracked_sha != current_local_sha:
        if not force:
            if ConfigManager.interactive_mode:
                CONSOLE.print(f"[yellow]Local file {remote_path} has been modified.[/yellow]")
                if not click.confirm("Overwrite with remote version?"):
                    CONSOLE.print(f"Skipping {remote_path}")
                    return False
            else:
                CONSOLE.print(
                    f"[yellow]Skipping modified file {remote_path} (use --force to overwrite)[/yellow]"
                )
                return False
    return True


def _download_and_validate_scenario(adapter, remote_path, ref, dest_path, status):
    """Download and validate a scenario file.

    Returns (content_bytes, sha256) on success, (None, None) on failure.
    """
    from broker.scenarios import compute_sha256, validate_scenario

    status.update(f"Downloading {remote_path}...")

    try:
        content_bytes = adapter.get_file_content(remote_path, ref)
        new_sha = compute_sha256(content_bytes)
    except (OSError, RuntimeError, exceptions.BrokerError, requests.RequestException) as e:
        CONSOLE.print(f"[red]Failed to download {remote_path}:[/red] {e}")
        logger.error(f"Failed to download {remote_path}: {e}")
        return None, None

    # Validate (Soft Gate)
    with helpers.data_to_tempfile(content_bytes, suffix=".yaml") as tmp:
        status.update(f"Validating {remote_path}...")
        is_valid, err = validate_scenario(tmp)

        if not is_valid:
            msg = f"Validation failed for {remote_path}: {err}"
            if ConfigManager.interactive_mode:
                CONSOLE.print(f"[yellow]{msg}[/yellow]")
                if not click.confirm("Import invalid scenario anyway?"):
                    return None, None
            else:
                logger.warning(msg)
                CONSOLE.print(
                    f"[yellow]Warning: Imported scenario {remote_path} failed validation.[/yellow]"
                )

    return content_bytes, new_sha


def _filter_scenarios(remote_files, allowed_files, category, name):
    """Apply filters to the remote scenarios list.

    Returns a list of filtered scenario dictionaries.
    """
    filtered_scenarios = []
    for scenario in remote_files:
        path = scenario["path"]

        # Check against update allow-list
        if allowed_files is not None and path not in allowed_files:
            continue

        # Category filter (metadata list OR directory prefix)
        if category:
            in_list = category in scenario.get("categories", [])
            in_path = path.startswith(f"{category}/")
            if not (in_list or in_path):
                continue

        # Name filter (metadata name OR file stem)
        if name:
            name_match = scenario.get("name") == name
            stem_match = Path(path).stem == name
            if not (name_match or stem_match):
                continue

        filtered_scenarios.append(scenario)

    return filtered_scenarios


def _handle_ambiguous_names(filtered_scenarios, name, category, import_all):
    """Handle ambiguous name matches interactively.

    Returns (filtered_scenarios, filtered_paths, should_continue).
    should_continue is False if user cancelled or error occurred.
    """
    filtered_paths = [s["path"] for s in filtered_scenarios]

    if not (name and not category and len(filtered_scenarios) > 1):
        return filtered_scenarios, filtered_paths, True

    if ConfigManager.interactive_mode and not import_all:
        CONSOLE.print(f"[yellow]Ambiguous name '{name}' matches multiple files:[/yellow]")
        for idx, s in enumerate(filtered_scenarios, 1):
            CONSOLE.print(f" {idx}. {s['path']}")

        choice = click.prompt(
            "Enter the number of the scenario to import (or 0 to cancel)", type=int, default=0
        )
        if choice == 0:
            return filtered_scenarios, filtered_paths, False
        if 1 <= choice <= len(filtered_scenarios):
            filtered_scenarios = [filtered_scenarios[choice - 1]]
            filtered_paths = [filtered_scenarios[0]["path"]]
            return filtered_scenarios, filtered_paths, True

        CONSOLE.print("[red]Invalid selection.[/red]")
        return filtered_scenarios, filtered_paths, False
    elif not import_all:
        CONSOLE.print(
            f"[red]Ambiguous name '{name}' matches multiple files: {', '.join(filtered_paths)}[/red]"
        )
        CONSOLE.print("Use --category to disambiguate or --all to import all.")
        return filtered_scenarios, filtered_paths, False

    return filtered_scenarios, filtered_paths, True


def _execute_import(adapter, filtered_paths, ref, tracked_files, force):
    """Execute the actual import of scenario files.

    Returns (imported_files, resolved_commit) where imported_files is a list of {path, sha256}.
    """
    from broker.scenarios import SCENARIOS_DIR

    imported_files = []
    try:
        resolved_commit = adapter.resolve_commit(ref)
    except (OSError, RuntimeError, exceptions.BrokerError, requests.RequestException) as e:
        logger.error(f"Failed to resolve commit for ref '{ref}': {e}")
        return [], None

    with CONSOLE.status("[bold green]Importing scenarios...") as status:
        for remote_path in filtered_paths:
            dest_path = SCENARIOS_DIR / remote_path

            # Check for local modifications
            if not _check_local_modifications(dest_path, remote_path, tracked_files, force):
                continue

            # Download and validate
            content_bytes, new_sha = _download_and_validate_scenario(
                adapter, remote_path, ref, dest_path, status
            )
            if content_bytes is None:
                continue

            # Write file
            if isinstance(dest_path, str):
                dest_path = Path(dest_path)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(content_bytes)
            imported_files.append({"path": remote_path, "sha256": new_sha})

    return imported_files, resolved_commit


def _update_import_manifest(manifest, entry, source, ref, resolved_commit, imported_files):
    """Update the import manifest with newly imported files."""
    from broker.scenarios import save_imports_manifest, upsert_import_entry

    if not imported_files:
        CONSOLE.print("No scenarios were imported.")
        return

    # Merge imported_files with existing ones for this source if we did partial update
    if entry:
        # Map existing files by path
        final_files_map = {f["path"]: f for f in entry["files"]}
        # Update with new imports
        for f in imported_files:
            final_files_map[f["path"]] = f
        final_files_list = list(final_files_map.values())
    else:
        final_files_list = imported_files

    upsert_import_entry(manifest, source, ref, resolved_commit, final_files_list)
    save_imports_manifest(manifest)

    CONSOLE.print(f"[green]Successfully imported {len(imported_files)} scenario(s).[/green]")


def _remove_imported_scenario(path_or_name):
    """Remove an imported scenario file and clean up the manifest entry.

    Accepts either a relative path (e.g. 'category/name.yaml') or a bare
    stem name (e.g. 'name').  Matches are resolved inside SCENARIOS_DIR.
    """
    from broker.scenarios import SCENARIOS_DIR, load_imports_manifest, save_imports_manifest

    # 1. Locate the file on-disk
    candidate = path_or_name.lstrip("/")
    if not candidate.endswith((".yaml", ".yml")):
        candidate_suffixes = [candidate + ".yaml", candidate + ".yml"]
    else:
        candidate_suffixes = [candidate]

    matched_file = None
    matched_rel = None

    for suffix in candidate_suffixes:
        exact = SCENARIOS_DIR / suffix
        if exact.exists():
            matched_file = exact
            matched_rel = suffix
            break

    # Fallback: glob by stem name across all subdirectories
    if matched_file is None:
        stem = Path(candidate).stem
        hits = list(SCENARIOS_DIR.rglob(f"{stem}.yaml")) + list(SCENARIOS_DIR.rglob(f"{stem}.yml"))
        if len(hits) > 1:
            CONSOLE.print(f"[yellow]Ambiguous name '{stem}' matches multiple files:[/yellow]")
            for h in hits:
                CONSOLE.print(f"  {h.relative_to(SCENARIOS_DIR)}")
            CONSOLE.print("Re-run with the full relative path to disambiguate.")
            return
        if hits:
            matched_file = hits[0]
            matched_rel = str(matched_file.relative_to(SCENARIOS_DIR))

    if matched_file is None or not matched_file.exists():
        CONSOLE.print(f"[red]Scenario file not found:[/red] {path_or_name}")
        return

    # 2. Delete the local file
    matched_file.unlink()
    CONSOLE.print(f"[green]Deleted[/green] {matched_rel}")

    # Clean up now-empty parent directories (but never remove SCENARIOS_DIR itself)
    parent = matched_file.parent
    while parent != SCENARIOS_DIR and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent

    # 3. Remove from the manifest
    norm_rel = matched_rel.replace("\\", "/")

    manifest = load_imports_manifest()
    changed = False
    surviving_imports = []
    for entry in manifest.get("imports", []):
        original_files = entry.get("files", [])
        kept = [f for f in original_files if f.get("path", "").replace("\\", "/") != norm_rel]
        if len(kept) != len(original_files):
            changed = True
            if kept:
                entry["files"] = kept
                surviving_imports.append(entry)
            # else: drop the whole import entry — no files remain
        else:
            surviving_imports.append(entry)

    if changed:
        manifest["imports"] = surviving_imports
        save_imports_manifest(manifest)
    else:
        CONSOLE.print(
            f"[yellow]No manifest entry found for[/yellow] {norm_rel} (file was still deleted)"
        )


@guarded_command(group=scenarios, name="import")
@click.argument("source", type=str, default="SatelliteQE/broker-scenarios", required=False)
@click.option(
    "--list", "-l", "list_mode", is_flag=True, help="List remote scenarios without downloading."
)
@click.option("--list-imported", "-L", is_flag=True, help="List previously imported scenarios.")
@click.option("--category", type=str, help="Filter by top-level category.")
@click.option("--name", type=str, help="Filter by scenario name.")
@click.option("--update", is_flag=True, help="Update existing scenarios.")
@click.option("--force", is_flag=True, help="Force overwrite modified files.")
@click.option("--all", "import_all", is_flag=True, help="Import all matches without prompting.")
@click.option(
    "--remove",
    "remove_name",
    type=str,
    default=None,
    help="Delete a local scenario file and remove it from the import manifest.",
)
def scenarios_import(
    source, list_mode, list_imported, category, name, update, force, import_all, remove_name
):
    """Import scenarios from a remote source.

    SOURCE default is SatelliteQE/broker-scenarios, but can be:
    - owner/repo (GitHub)
    - gitlab.com/owner/repo
    - A raw HTTP URL to a file or manifest
    """
    from broker.scenarios import (
        find_import_entry,
        load_imports_manifest,
    )

    # 1. Handle --remove: no SOURCE required
    if remove_name:
        _remove_imported_scenario(remove_name)
        return

    # 1b. Handle --list-imported
    if list_imported:
        manifest = load_imports_manifest()
        _display_imported_scenarios(manifest)
        return

    # 2. Parse Source
    try:
        adapter, rel_path, ref = helpers.parse_source(source)
    except (exceptions.BrokerError, click.ClickException) as e:
        CONSOLE.print(f"[red]Error parsing source:[/red] {e}")
        return

    # 3. Handle --update: restrict file list to what we tracked previously
    allowed_files = None
    if update:
        manifest = load_imports_manifest()
        if entry := find_import_entry(manifest, source, ref):
            allowed_files = {f["path"] for f in entry.get("files", [])}
            CONSOLE.print(
                f"[cyan]Updating {len(allowed_files)} tracked files from {source}...[/cyan]"
            )
        else:
            CONSOLE.print(
                f"[yellow]No existing import found for {source} @ {ref}. Performing fresh import.[/yellow]"
            )

    # 4. Resolve Remote Files
    try:
        if list_mode:
            CONSOLE.print(f"[cyan]Fetching file list from {source}...[/cyan]")

        remote_files = adapter.list_remote_scenarios(path=rel_path, ref=ref)
    except (OSError, RuntimeError, exceptions.BrokerError, requests.RequestException) as e:
        CONSOLE.print(f"[red]Failed to list remote scenarios:[/red] {e}")
        logger.error(f"Failed to list remote scenarios: {e}")
        return

    # 5. Apply Filters
    filtered_scenarios = _filter_scenarios(remote_files, allowed_files, category, name)

    if not filtered_scenarios:
        CONSOLE.print("[yellow]No scenarios found matching criteria.[/yellow]")
        return

    # Handle ambiguous name matches (if category wasn't specified)
    filtered_scenarios, filtered_paths, should_continue = _handle_ambiguous_names(
        filtered_scenarios, name, category, import_all
    )
    if not should_continue:
        return

    # 6. List Mode Output
    if list_mode:
        _display_remote_scenarios(filtered_scenarios, source)
        return

    # 7. Confirmation (Interactive only)
    if ConfigManager.interactive_mode and not import_all:
        if len(filtered_paths) > 1:
            if not click.confirm(f"Import {len(filtered_paths)} scenarios from {source}?"):
                return

    # 8. Import Execution
    manifest = load_imports_manifest()  # Refresh manifest
    entry = find_import_entry(manifest, source, ref)
    tracked_files = {f["path"]: f["sha256"] for f in entry["files"]} if entry else {}

    imported_files, resolved_commit = _execute_import(
        adapter, filtered_paths, ref, tracked_files, force
    )

    # 9. Update Manifest
    _update_import_manifest(manifest, entry, source, ref, resolved_commit, imported_files)


def _make_shell_help_func(cmd, shell_instance):
    """Create a help function that invokes the command with --help.

    This works around a compatibility issue between click_shell and rich_click where
    the shell's built-in help system uses a standard HelpFormatter that lacks
    rich_click's config attribute.
    """

    def help_func():
        # Invoke the command with --help which properly uses rich_click formatting
        with contextlib.suppress(SystemExit):
            cmd.main(["--help"], standalone_mode=False, parent=shell_instance.ctx)

    help_func.__name__ = f"help_{cmd.name}"
    return help_func


@shell(
    prompt="broker > ",
    intro="Welcome to Broker's interactive shell.\nType 'help' for commands, 'exit' or 'quit' to leave.",
)
def broker_shell():
    """Start an interactive Broker shell session."""
    pass


# Register commands to the shell
broker_shell.add_command(checkout)
broker_shell.add_command(checkin)
broker_shell.add_command(inventory)
broker_shell.add_command(execute)
broker_shell.add_command(providers)
broker_shell.add_command(config)
broker_shell.add_command(scenarios)


# Shell-only commands (not available as normal sub-commands)
@broker_shell.command(name="reload_config")
def reload_config_cmd():
    """Reload Broker's configuration from disk.

    This clears the cached settings, forcing them to be re-read
    from the settings file on next access.
    """
    settings.settings._settings = None
    setup_logging(
        console_level=settings.settings.logging.console_level,
        file_level=settings.settings.logging.file_level,
        log_path=settings.settings.logging.log_path,
        structured=settings.settings.logging.structured,
    )
    CONSOLE.print("Configuration reloaded.")


# Patch help functions on the shell instance to work around click_shell/rich_click incompatibility
for cmd_name, cmd in broker_shell.commands.items():
    setattr(broker_shell.shell, f"help_{cmd_name}", _make_shell_help_func(cmd, broker_shell.shell))


@cli.command(name="shell")
def shell_cmd():
    """Start an interactive Broker shell session.

    This provides a REPL-like interface for running Broker commands
    without needing to prefix each with 'broker'.
    """
    broker_shell(standalone_mode=False, args=[])
