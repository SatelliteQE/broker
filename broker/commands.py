"""Defines the CLI commands for Broker."""
from functools import wraps
import signal
import sys

import click
from logzero import logger

from broker import exceptions, helpers, settings
from broker.broker import Broker
from broker.logger import LOG_LEVEL
from broker.providers import PROVIDER_HELP, PROVIDERS

signal.signal(signal.SIGINT, helpers.handle_keyboardinterrupt)


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


class ExceptionHandler(click.Group):
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
        import pkg_resources
        import requests

        broker_version = pkg_resources.get_distribution("broker").version
        # check the latest version publish to PyPi
        try:
            latest_version = Version(
                requests.get("https://pypi.org/pypi/broker/json", timeout=60).json()["info"][
                    "version"
                ]
            )
            if latest_version > Version(broker_version):
                click.secho(
                    f"A newer version of broker is available: {latest_version}",
                    fg="yellow",
                )
        except requests.exceptions.RequestException as err:
            logger.warning(f"Unable to check for latest version: {err}")
        click.echo(f"Version: {broker_version}")
        broker_directory = settings.BROKER_DIRECTORY.absolute()
        click.echo(f"Broker Directory: {broker_directory}")
        click.echo(f"Settings File: {settings.settings_path.absolute()}")
        click.echo(f"Inventory File: {broker_directory}/inventory.yaml")
        click.echo(f"Log File: {broker_directory}/logs/broker.log")


@loggedcli(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
@click.option("-b", "--background", is_flag=True, help="Run checkout in the background")
@click.option("-n", "--nick", type=str, help="Use a nickname defined in your settings")
@click.option("-c", "--count", type=int, help="Number of times broker repeats the checkout")
@click.option(
    "--args-file",
    type=click.Path(exists=True),
    help="A json or yaml file mapping arguments to values",
)
@provider_options
@click.pass_context
def checkout(ctx, background, nick, count, args_file, **kwargs):
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
    # if additional arguments were passed, include them in the broker args
    # strip leading -- characters
    broker_args.update(
        {
            (key[2:] if key.startswith("--") else key): val
            for key, val in zip(ctx.args[::2], ctx.args[1::2])
        }
    )
    if background:
        helpers.fork_broker()
    Broker(**broker_args).checkout()


@cli.group(cls=ExceptionHandler)
def providers():
    """Get information about a provider and its actions."""
    pass


populate_providers(providers)


@loggedcli()
@click.argument("vm", type=str, nargs=-1)
@click.option("-b", "--background", is_flag=True, help="Run checkin in the background")
@click.option("--all", "all_", is_flag=True, help="Select all VMs")
@click.option("--sequential", is_flag=True, help="Run checkins sequentially")
@click.option("--filter", type=str, help="Checkin only what matches the specified filter")
def checkin(vm, background, all_, sequential, filter):
    """Checkin or "remove" a VM or series of VM broker instances.

    COMMAND: broker checkin <vm hostname>|<local id>|--all
    """
    if background:
        helpers.fork_broker()
    inventory = helpers.load_inventory(filter=filter)
    to_remove = []
    for num, host in enumerate(inventory):
        if str(num) in vm or host.get("hostname") in vm or host.get("name") in vm or all_:
            to_remove.append(Broker().reconstruct_host(host))
    Broker(hosts=to_remove).checkin(sequential=sequential)


@loggedcli()
@click.option("--details", is_flag=True, help="Display all host details")
@click.option(
    "--sync",
    type=str,
    help="Class-style name of a supported broker provider. (AnsibleTower)",
)
@click.option("--filter", type=str, help="Display only what matches the specified filter")
def inventory(details, sync, filter):
    """Get a list of all VMs you've checked out showing hostname and local id.

    hostname pulled from list of dictionaries.
    """
    if sync:
        Broker.sync_inventory(provider=sync)
    logger.info("Pulling local inventory")
    inventory = helpers.load_inventory(filter=filter)
    emit_data = []
    for num, host in enumerate(inventory):
        emit_data.append(host)
        if (display_name := host.get("hostname")) is None:
            display_name = host.get("name")
        # if we're filtering, then don't show an index.
        # Otherwise, a user might perform an action on the incorrect (unfiltered) index.
        index = f"{num}: " if filter is None else ""
        if details:
            logger.info(f"{index}{display_name}:\n{helpers.yaml_format(host)}")
        else:
            logger.info(f"{index}{display_name}")
    helpers.emit({"inventory": emit_data})


@loggedcli()
@click.argument("vm", type=str, nargs=-1)
@click.option("-b", "--background", is_flag=True, help="Run extend in the background")
@click.option("--all", "all_", is_flag=True, help="Select all VMs")
@click.option("--sequential", is_flag=True, help="Run extends sequentially")
@click.option("--filter", type=str, help="Extend only what matches the specified filter")
@provider_options
def extend(vm, background, all_, sequential, filter, **kwargs):
    """Extend a host's lease time.

    COMMAND: broker extend <vm hostname>|<vm name>|<local id>|--all
    """
    broker_args = helpers.clean_dict(kwargs)
    if background:
        helpers.fork_broker()
    inventory = helpers.load_inventory(filter=filter)
    to_extend = []
    for num, host in enumerate(inventory):
        if str(num) in vm or host["hostname"] in vm or host.get("name") in vm or all_:
            to_extend.append(Broker().reconstruct_host(host))
    Broker(hosts=to_extend, **broker_args).extend(sequential=sequential)


@loggedcli()
@click.argument("vm", type=str, nargs=-1)
@click.option("-b", "--background", is_flag=True, help="Run duplicate in the background")
@click.option("-c", "--count", type=int, help="Number of times broker repeats the duplicate")
@click.option("--all", "all_", is_flag=True, help="Select all VMs")
@click.option("--filter", type=str, help="Duplicate only what matches the specified filter")
def duplicate(vm, background, count, all_, filter):
    """Duplicate a broker-procured vm.

    DEPRECATED! This will be removed in Broker 0.5. If you need this feature, please open an issue.

    COMMAND: broker duplicate <vm hostname>|<local id>|all
    """
    logger.warning(
        "Duplicate will be remove in Broker 0.5. If you need this feature, please open an issue."
    )
    if background:
        helpers.fork_broker()
    inventory = helpers.load_inventory(filter=filter)
    for num, host in enumerate(inventory):
        if str(num) in vm or host["hostname"] in vm or host.get("name") in vm or all_:
            broker_args = host.get("_broker_args")
            if broker_args:
                if count:
                    broker_args["_count"] = count
                logger.info(f"Duplicating: {host['hostname']}")
                broker_inst = Broker(**broker_args)
                broker_inst.checkout()
            else:
                logger.warning(f"Unable to duplicate {host['hostname']}, no _broker_args found")


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
@provider_options
@click.pass_context
def execute(ctx, background, nick, output_format, artifacts, args_file, **kwargs):
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
    # if additional arguments were passed, include them in the broker args
    # strip leading -- characters
    broker_args.update(
        {
            (key[2:] if key.startswith("--") else key): val
            for key, val in zip(ctx.args[::2], ctx.args[1::2])
        }
    )
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
