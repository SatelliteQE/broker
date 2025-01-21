"""Defines the CLI commands for Broker."""

from functools import wraps
import signal
import sys

from logzero import logger
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table
import rich_click as click

from broker import exceptions, helpers, settings
from broker.broker import Broker
from broker.config_manager import ConfigManager
from broker.logger import LOG_LEVEL
from broker.providers import PROVIDER_ACTIONS, PROVIDER_HELP, PROVIDERS

signal.signal(signal.SIGINT, helpers.handle_keyboardinterrupt)
CONSOLE = Console(no_color=settings.settings.less_colors)  # rich console for pretty printing

click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.COMMAND_GROUPS = {
    "broker": [
        {"name": "Core Actions", "commands": ["checkout", "checkin", "inventory"]},
        {"name": "Extras", "commands": ["execute", "extend", "providers", "config"]},
    ]
}


def loggedcli(group=None, *cli_args, **cli_kwargs):
    """Update the group command wrapper function in order to add logging."""
    if not group:
        group = cli  # default to the main cli group

    def decorator(func):
        @group.command(*cli_args, **cli_kwargs)
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger.log(LOG_LEVEL.TRACE.value, f"Calling {func=}(*{args=} **{kwargs=}")
            retval = func(*args, **kwargs)
            logger.log(
                LOG_LEVEL.TRACE.value,
                f"Finished {func=}(*{args=} **{kwargs=}) {retval=}",
            )
            return retval

        return wrapper

    return decorator


def parse_labels(provider_labels):
    """Parse the provided label string and returns labels in a dict."""
    return {
        label[0]: "=".join(label[1:])
        for label in [kv_pair.split("=") for kv_pair in provider_labels.split(",")]
    }


class ExceptionHandler(click.RichGroup):
    """Wraps click group to catch and handle raised exceptions."""

    def __call__(self, *args, **kwargs):
        """Override the __call__ method to catch and handle exceptions."""
        try:
            res = self.main(*args, **kwargs)
            helpers.emit(return_code=0)
            return res
        except Exception as err:  # noqa: BLE001
            if not isinstance(err, exceptions.BrokerError):
                err = exceptions.BrokerError(err)
            helpers.emit(return_code=err.error_code, error_message=str(err.message))
            sys.exit(err.error_code)


def provider_options(command):
    """Apply provider-specific decorators to each command this decorates."""
    for prov in PROVIDERS.values():
        if prov.hidden:
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

        @loggedcli(
            group=click_group,
            name=prov,
            hidden=prov_class.hidden,
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
        for option, (p_cls, is_flag) in PROVIDER_HELP.items():
            if p_cls is not prov_class:
                continue
            option = option.replace("_", "-")  # noqa: PLW2901
            if is_flag:
                provider_cmd = click.option(
                    f"--{option}", is_flag=True, help=f"Get available {option}"
                )(provider_cmd)
            else:
                provider_cmd = click.option(
                    f"--{option}", type=str, help=f"Get information about a {option}"
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


@click.group(cls=ExceptionHandler, invoke_without_command=True)
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
        table.add_row("Settings File", str(settings.settings_path.absolute()))
        table.add_row("Inventory File", f"{settings.BROKER_DIRECTORY.absolute()}/inventory.yaml")
        table.add_row("Log File", f"{settings.BROKER_DIRECTORY.absolute()}/logs/broker.log")

        # Print the table
        CONSOLE.print(table)


@loggedcli(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
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


@cli.group(cls=ExceptionHandler)
def providers():
    """Get information about a provider and its actions."""
    pass


populate_providers(providers)


@loggedcli()
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
        logger.warning(f"The following hosts were not found in inventory: {', '.join(unmatched)}")
    if to_remove:
        Broker(hosts=to_remove).checkin(sequential=sequential)


@loggedcli()
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


@loggedcli()
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


@loggedcli(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
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
    elif output_format == "yaml":
        click.echo(helpers.yaml_format(result))


@cli.group(cls=ExceptionHandler)
def config():
    """View and manage Broker's configuration.

    Note: One important concept of these commands is the concept of a "chunk".

    A chunk is a part of the configuration file that can be accessed or updated.
    Chunks are specified by their keys in the configuration file.
    Nested chunks are separated by periods.

    e.g. broker config view AnsibleTower.instances.my_instance
    """


@loggedcli(group=config)
@click.argument("chunk", type=str, required=False)
@click.option("--no-syntax", is_flag=True, help="Disable syntax highlighting")
def view(chunk, no_syntax):
    """View all or part of the broker configuration."""
    result = helpers.yaml_format(ConfigManager(settings.settings_path).get(chunk))
    if no_syntax:
        CONSOLE.print(result)
    else:
        CONSOLE.print(Syntax(result, "yaml", background_color="default"))


@loggedcli(group=config)
@click.argument("chunk", type=str, required=False)
def edit(chunk):
    """Directly edit the broker configuration file.

    You can define the scope of the edit by specifying a chunk.
    Otherwise, the entire configuration file will be opened.
    """
    ConfigManager(settings.settings_path).edit(chunk)


@loggedcli(group=config, name="set")
@click.argument("chunk", type=str, required=True)
@click.argument("new-value", type=str, required=True)
def _set(chunk, new_value):
    """Set a value in the Broker configuration file.

    These updates take the form of `<chunk> <value>` pairs.
    You can also pass a yaml or json file containing the new contents of a chunk.
    """
    new_value = helpers.resolve_file_args({"nv": new_value})["nv"]
    ConfigManager(settings.settings_path).update(chunk, new_value)


@loggedcli(group=config)
def restore():
    """Restore the broker configuration file to the last backup."""
    ConfigManager(settings.settings_path).restore()


@loggedcli(group=config)
@click.argument("chunk", type=str, required=False)
@click.option("--from", "_from", type=str, help="A file path or URL to initialize the config from.")
def init(chunk=None, _from=None):
    """Initialize the broker configuration file from your local clone or GitHub.

    You can also init specific chunks by passing the chunk name.
    Additionally, if you want to initialize from a file or URL, you can pass the `--from` flag.
    Keep in mind that the file and url contents need to be valid yaml.
    """
    ConfigManager(settings.settings_path).init_config_file(chunk=chunk, _from=_from)


@loggedcli(group=config)
def nicks():
    """Get a list of nicks."""
    result = ConfigManager(settings.settings_path).nicks()
    CONSOLE.print("\n".join(result))


@loggedcli(group=config)
@click.argument("nick", type=str, required=True)
@click.option("--no-syntax", is_flag=True, help="Disable syntax highlighting")
def nick(nick, no_syntax):
    """Get information about a specific nick."""
    result = helpers.yaml_format(ConfigManager(settings.settings_path).nicks(nick))
    if no_syntax:
        CONSOLE.print(result)
    else:
        CONSOLE.print(Syntax(result, "yaml", background_color="default"))


@loggedcli(group=config)
@click.option("-f", "--force-version", type=str, help="Force the migration to a specific version")
def migrate(force_version=None):
    """Migrate the broker configuration file to the latest version."""
    ConfigManager(settings.settings_path).migrate(force_version=force_version)


@loggedcli(group=config)
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
    except exceptions.BrokerError as err:
        logger.warning(f"Validation failed: {err}")
