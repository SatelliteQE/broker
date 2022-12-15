import signal
import sys
import click
from logzero import logger
from broker.broker import PROVIDERS, PROVIDER_ACTIONS, Broker
from broker.providers import Provider
from broker import exceptions, helpers, settings


signal.signal(signal.SIGINT, helpers.handle_keyboardinterrupt)


class ExceptionHandler(click.Group):
    """Wraps click group to catch and handle raised exceptions"""

    def __call__(self, *args, **kwargs):
        try:
            return self.main(*args, **kwargs)
        except Exception as err:
            if not isinstance(err, exceptions.BrokerError):
                err = exceptions.BrokerError(err)
            helpers.emit(return_code=err.error_code, error_message=str(err.message))
            sys.exit(err.error_code)
        helpers.emit(return_code=0)


def provider_options(command):
    """Applies provider-specific decorators to each command this decorates"""
    for prov in Provider.__subclasses__():
        if prov.hidden:
            continue
        for option in getattr(prov, f"_{command.__name__}_options"):
            command = option(command)
    return command


def populate_providers(click_group):
    """Populates the subcommands for providers subcommand using provider information
    Providers become subcommands and their actions become arguments to their subcommand

    Example:
        Usage: broker providers AnsibleTower [OPTIONS]

        Options:
        --workflows      Get available workflows
        --workflow TEXT  Get information about a workflow
        --help           Show this message and exit.

    Note: This currently only works for the default instance for each provider
    """
    for prov, prov_class in (pairs for pairs in PROVIDERS.items()):

        @click_group.command(name=prov, hidden=prov_class.hidden)
        def provider_cmd(*args, **kwargs):  # the actual subcommand
            """Get information about a provider's actions"""
            broker_inst = Broker(**kwargs)
            broker_inst.nick_help()

        # iterate through available actions and populate options from them
        for action in (
            action
            for action, prov_info in PROVIDER_ACTIONS.items()
            if prov_info[0] == prov_class
        ):
            action = action.replace("_", "-")
            plural = (
                action.replace("y", "ies") if action.endswith("y") else f"{action}s"
            )
            provider_cmd = click.option(
                f"--{plural}", is_flag=True, help=f"Get available {plural}"
            )(provider_cmd)
            provider_cmd = click.option(
                f"--{action}", type=str, help=f"Get information about a {action}"
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
    type=click.Choice(["info", "warning", "error", "critical", "debug", "silent"]),
    default="debug" if settings.settings.debug else "info",
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
    if version:
        import pkg_resources

        broker_version = pkg_resources.get_distribution("broker").version
        click.echo(f"Version: {broker_version}")
        broker_directory = settings.BROKER_DIRECTORY.absolute()
        click.echo(f"Broker Directory: {broker_directory}")
        click.echo(f"Settings File: {settings.settings_path.absolute()}")
        click.echo(f"Inventory File: {broker_directory}/inventory.yaml")
        click.echo(f"Log File: {broker_directory}/logs/broker.log")


@cli.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
@click.option("-b", "--background", is_flag=True, help="Run checkout in the background")
@click.option("-n", "--nick", type=str, help="Use a nickname defined in your settings")
@click.option(
    "-c", "--count", type=int, help="Number of times broker repeats the checkout"
)
@click.option(
    "--args-file",
    type=click.Path(exists=True),
    help="A json or yaml file mapping arguments to values",
)
@provider_options
@click.pass_context
def checkout(ctx, background, nick, count, args_file, **kwargs):
    """Checkout or "create" a Virtual Machine broker instance
    COMMAND: broker checkout --workflow "workflow-name" --workflow-arg1 something
    or
    COMMAND: broker checkout --nick "nickname"

    :param ctx: clicks context object

    :param background: run a new broker subprocess to carry out command

    :param nick: shortcut for arguments saved in settings.yaml, passed in as a string

    :param args_file: this broker argument will be replaced with the contents of the file passed in
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
    broker_inst = Broker(**broker_args)
    broker_inst.checkout()


@cli.group(cls=ExceptionHandler)
def providers():
    """Get information about a provider and its actions"""
    pass


populate_providers(providers)


@cli.command()
@click.argument("vm", type=str, nargs=-1)
@click.option("-b", "--background", is_flag=True, help="Run checkin in the background")
@click.option("--all", "all_", is_flag=True, help="Select all VMs")
@click.option("--sequential", is_flag=True, help="Run checkins sequentially")
@click.option(
    "--filter", type=str, help="Checkin only what matches the specified filter"
)
def checkin(vm, background, all_, sequential, filter):
    """Checkin or "remove" a VM or series of VM broker instances

    COMMAND: broker checkin <vm hostname>|<local id>|all

    :param vm: Hostname or local id of host

    :param background: run a new broker subprocess to carry out command

    :param all_: Flag for whether to checkin everything

    :param sequential: Flag for whether to run checkins sequentially

    :param filter: a filter string matching broker's specification
    """
    if background:
        helpers.fork_broker()
    inventory = helpers.load_inventory(filter=filter)
    to_remove = []
    for num, host in enumerate(inventory):
        if (
            str(num) in vm
            or host.get("hostname") in vm
            or host.get("name") in vm
            or all_
        ):
            to_remove.append(Broker().reconstruct_host(host))
    broker_inst = Broker(hosts=to_remove)
    broker_inst.checkin(sequential=sequential)


@cli.command()
@click.option("--details", is_flag=True, help="Display all host details")
@click.option(
    "--sync",
    type=str,
    help="Class-style name of a supported broker provider. (AnsibleTower)",
)
@click.option(
    "--filter", type=str, help="Display only what matches the specified filter"
)
def inventory(details, sync, filter):
    """Get a list of all VMs you've checked out showing hostname and local id
    hostname pulled from list of dictionaries
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
        if details:
            logger.info(f"{num}: {display_name}, Details: {helpers.yaml_format(host)}")
        else:
            logger.info(f"{num}: {display_name}")
    helpers.emit({"inventory": emit_data})


@cli.command()
@click.argument("vm", type=str, nargs=-1)
@click.option("-b", "--background", is_flag=True, help="Run extend in the background")
@click.option("--all", "all_", is_flag=True, help="Select all VMs")
@click.option("--sequential", is_flag=True, help="Run extends sequentially")
@click.option(
    "--filter", type=str, help="Extend only what matches the specified filter"
)
@provider_options
def extend(vm, background, all_, sequential, filter, **kwargs):
    """Extend a host's lease time

    COMMAND: broker extend <vm hostname>|<vm name>|<local id>

    :param vm: Hostname, VM Name, or local id of host

    :param background: run a new broker subprocess to carry out command

    :param all_: Click option all

    :param sequential: Flag for whether to run extends sequentially

    :param filter: a filter string matching broker's specification
    """
    broker_args = helpers.clean_dict(kwargs)
    if background:
        helpers.fork_broker()
    inventory = helpers.load_inventory(filter=filter)
    to_extend = []
    for num, host in enumerate(inventory):
        if str(num) in vm or host["hostname"] in vm or host["name"] in vm or all_:
            to_extend.append(Broker().reconstruct_host(host))
    broker_inst = Broker(hosts=to_extend, **broker_args)
    broker_inst.extend(sequential=sequential)


@cli.command()
@click.argument("vm", type=str, nargs=-1)
@click.option(
    "-b", "--background", is_flag=True, help="Run duplicate in the background"
)
@click.option(
    "-c", "--count", type=int, help="Number of times broker repeats the duplicate"
)
@click.option("--all", "all_", is_flag=True, help="Select all VMs")
@click.option(
    "--filter", type=str, help="Duplicate only what matches the specified filter"
)
def duplicate(vm, background, count, all_, filter):
    """Duplicate a broker-procured vm

    COMMAND: broker duplicate <vm hostname>|<local id>|all

    :param vm: Hostname or local id of host

    :param background: run a new broker subprocess to carry out command

    :param all_: Click option all

    :param filter: a filter string matching broker's specification
    """
    if background:
        helpers.fork_broker()
    inventory = helpers.load_inventory(filter=filter)
    for num, host in enumerate(inventory):
        if str(num) in vm or host["hostname"] in vm or host["name"] in vm or all_:
            broker_args = host.get("_broker_args")
            if broker_args:
                if count:
                    broker_args["_count"] = count
                logger.info(f"Duplicating: {host['hostname']}")
                broker_inst = Broker(**broker_args)
                broker_inst.checkout()
            else:
                logger.warning(
                    f"Unable to duplicate {host['hostname']}, no _broker_args found"
                )


@cli.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
@click.option("-b", "--background", is_flag=True, help="Run execute in the background")
@click.option("--nick", type=str, help="Use a nickname defined in your settings")
@click.option(
    "--output-format", "-o", type=click.Choice(["log", "raw", "yaml"]), default="log"
)
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
    """Execute an arbitrary provider action
    COMMAND: broker execute --workflow "workflow-name" --workflow-arg1 something
    or
    COMMAND: broker execute --nick "nickname"

    :param ctx: clicks context object

    :param background: run a new broker subprocess to carry out command

    :param nick: shortcut for arguments saved in settings.yaml, passed in as a string

    :param output_format: change the format of the output to one of the choice options

    :param artifacts: AnsibleTower provider specific option for choosing what to return

    :param args_file: this broker argument will be replaced with the contents of the file passed in
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
    broker_inst = Broker(**broker_args)
    result = broker_inst.execute()
    helpers.emit({"output": result})
    if output_format == "raw":
        print(result)
    elif output_format == "log":
        logger.info(result)
    elif output_format == "yaml":
        print(helpers.yaml_format(result))
