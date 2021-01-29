import broker
import os
import sys
import click
import logging
from logzero import logger
from broker.broker import PROVIDERS, PROVIDER_ACTIONS, VMBroker
from broker import logger as b_log
from broker import helpers, settings


def update_log_level(ctx, param, value):
    silent = False
    if value == "silent":
        silent = True
        value = "info"
    if getattr(logging, value.upper()) is not logger.getEffectiveLevel() or silent:
        b_log.setup_logzero(level=value, silent=silent)
        if not silent:
            click.echo(f"Log level changed to [{value}]")


def fork_broker():
    pid = os.fork()
    if pid:
        logger.info(f"Running broker in the background with pid: {pid}")
        sys.exit(0)
    update_log_level(None, None, "silent")


def populate_providers(click_group):
    """Populates the subcommands for providers subcommand using provider information
    Providers become subcommands and their actions become arguments to their subcommand

    Example:
        Usage: broker providers AnsibleTower [OPTIONS]

        Options:
        --workflows      Get available workflows
        --workflow TEXT  Get information about a workflow
        --help           Show this message and exit.
    """
    for prov, prov_class in (pairs for pairs in PROVIDERS.items()):

        @click_group.command(name=prov, hidden=prov_class.hidden)
        def provider_cmd(*args, **kwargs):  # the actual subcommand
            """Get information about a provider's actions"""
            broker_inst = VMBroker(**kwargs)
            broker_inst.nick_help()

        # iterate through available actions and populate options from them
        for action in (
            action
            for action, prov_info in PROVIDER_ACTIONS.items()
            if prov_info[0] == prov_class
        ):
            action = action.replace("_", "-")
            plural = action.replace('y', 'ies') if action.endswith('y') else f"{action}s"
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


@click.group(invoke_without_command=True)
@click.option(
    "--log-level",
    type=click.Choice(["info", "warning", "error", "critical", "debug", "silent"]),
    default="info",
    callback=update_log_level,
    is_eager=True,
    expose_value=False,
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
@click.option("--workflow", type=str)
@click.option("-n", "--nick", type=str, help="Use a nickname defined in your settings")
@click.option(
    "-c", "--count", type=int, help="Number of times broker repeats the checkout"
)
@click.pass_context
def checkout(ctx, background, workflow, nick, count):
    """Checkout or "create" a Virtual Machine broker instance
    COMMAND: broker checkout --workflow "workflow-name" --workflow-arg1 something
    or
    COMMAND: broker checkout --nick "nickname"

    :param ctx: clicks context object

    :param background: run a new broker subprocess to carry out command

    :param workflow: workflow template stored in Ansible Tower, passed in as a string

    :param nick: shortcut for arguments saved in settings.yaml, passed in as a string
    """
    broker_args = {}
    if nick:
        broker_args["nick"] = nick
    if workflow:
        broker_args["workflow"] = workflow
    if count:
        broker_args["_count"] = count
    # if additional arguments were passed, include them in the broker args
    # strip leading -- characters
    broker_args.update(
        {
            (key[2:] if key.startswith("--") else key): val
            for key, val in zip(ctx.args[::2], ctx.args[1::2])
        }
    )
    if background:
        fork_broker()
    broker_inst = VMBroker(**broker_args)
    broker_inst.checkout()


@cli.group()
def providers():
    """Get information about a provider and its actions"""
    pass


populate_providers(providers)


@cli.command()
@click.argument("vm", type=str, nargs=-1)
@click.option("-b", "--background", is_flag=True, help="Run checkin in the background")
@click.option("--all", "all_", is_flag=True, help="Select all VMs")
@click.option(
    "--filter", type=str, help="Checkin only what matches the specified filter"
)
def checkin(vm, background, all_, filter):
    """Checkin or "remove" a VM or series of VM broker instances

    COMMAND: broker checkin <vm hostname>|<local id>|all

    :param vm: Hostname or local id of host

    :param background: run a new broker subprocess to carry out command

    :param filter: a filter string matching broker's specification
    """
    if background:
        fork_broker()
    inventory = helpers.load_inventory(filter=filter)
    to_remove = []
    for num, host in enumerate(inventory):
        if str(num) in vm or host["hostname"] in vm or host["name"] in vm or all_:
            to_remove.append(VMBroker().reconstruct_host(host))
    broker_inst = VMBroker(hosts=to_remove)
    broker_inst.checkin()


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
        VMBroker.sync_inventory(provider=sync)
    logger.info("Pulling local inventory")
    inventory = helpers.load_inventory(filter=filter)
    for num, host in enumerate(inventory):
        if details:
            logger.info(
                f"{num}: {host['hostname'] or host['name']}, Details: {helpers.yaml_format(host)}"
            )
        else:
            logger.info(f"{num}: {host['hostname'] or host['name']}")


@cli.command()
@click.argument("vm", type=str, nargs=-1)
@click.option("-b", "--background", is_flag=True, help="Run extend in the background")
@click.option("--all", "all_", is_flag=True, help="Select all VMs")
@click.option(
    "--filter", type=str, help="Extend only what matches the specified filter"
)
def extend(vm, background, all_, filter):
    """Extend a host's lease time

    COMMAND: broker extend <vm hostname>|<vm name>|<local id>

    :param vm: Hostname, VM Name, or local id of host

    :param background: run a new broker subprocess to carry out command

    :param all_: Click option all

    :param filter: a filter string matching broker's specification
    """
    if background:
        fork_broker()
    inventory = helpers.load_inventory(filter=filter)
    to_extend = []
    for num, host in enumerate(inventory):
        if str(num) in vm or host["hostname"] in vm or host["name"] in vm or all_:
            to_extend.append(VMBroker().reconstruct_host(host))
    broker_inst = VMBroker(hosts=to_extend)
    broker_inst.extend()


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
        fork_broker()
    inventory = helpers.load_inventory(filter=filter)
    for num, host in enumerate(inventory):
        if str(num) in vm or host["hostname"] in vm or host["name"] in vm or all_:
            broker_args = host.get("_broker_args")
            if broker_args:
                if count:
                    broker_args["_count"] = count
                logger.info(f"Duplicating: {host['hostname']}")
                broker_inst = VMBroker(**broker_args)
                broker_inst.checkout()
            else:
                logger.warning(
                    f"Unable to duplicate {host['hostname']}, no _broker_args found"
                )


@cli.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
@click.option("-b", "--background", is_flag=True, help="Run execute in the background")
@click.option("--workflow", type=str)
@click.option("--job-template", type=str)
@click.option("--nick", type=str, help="Use a nickname defined in your settings")
@click.option(
    "--output-format", "-o", type=click.Choice(["log", "raw", "yaml"]), default="log"
)
@click.option(
    "--artifacts",
    type=click.Choice(["merge", "last"]),
    help="AnsibleTower: return artifacts associated with the execution.",
)
@click.pass_context
def execute(ctx, background, workflow, job_template, nick, output_format, artifacts):
    """Execute an arbitrary provider action
    COMMAND: broker execute --workflow "workflow-name" --workflow-arg1 something
    or
    COMMAND: broker execute --nick "nickname"

    :param ctx: clicks context object

    :param background: run a new broker subprocess to carry out command

    :param workflow: workflow template stored in Ansible Tower, passed in as a string

    :param job-template: job template stored in Ansible Tower, passed in as a string

    :param nick: shortcut for arguments saved in settings.yaml, passed in as a string

    :param output_format: change the format of the output to one of the choice options

    :param artifacts: AnsibleTower provider specific option for choosing what to return
    """
    broker_args = {}
    if nick:
        broker_args["nick"] = nick
    if workflow:
        broker_args["workflow"] = workflow
    if job_template:
        broker_args["job_template"] = job_template
    if artifacts:
        broker_args["artifacts"] = artifacts
    # if additional arguments were passed, include them in the broker args
    # strip leading -- characters
    broker_args.update(
        {
            (key[2:] if key.startswith("--") else key): val
            for key, val in zip(ctx.args[::2], ctx.args[1::2])
        }
    )
    if background:
        fork_broker()
    broker_inst = VMBroker(**broker_args)
    result = broker_inst.execute()
    if output_format == "raw":
        print(result)
    elif output_format == "log":
        logger.info(result)
    elif output_format == "yaml":
        print(helpers.yaml_format(result))
