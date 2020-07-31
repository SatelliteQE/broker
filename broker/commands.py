import os
import sys
import click
import logging
from logzero import logger
from broker.broker import VMBroker
from broker import logger as b_log
from broker import helpers


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


@click.group()
@click.option(
    "--log-level",
    type=click.Choice(["info", "warning", "error", "critical", "debug", "silent"]),
    default="info",
    callback=update_log_level,
    is_eager=True,
    expose_value=False,
)
def cli():
    pass


@cli.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
@click.option("-b", "--background", is_flag=True, help="Run checkout in the background")
@click.option("--workflow", type=str)
@click.option("--nick", type=str, help="Use a nickname defined in your settings")
@click.pass_context
def checkout(ctx, background, workflow, nick):
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
    # if additional arguments were passed, include them in the broker args
    broker_args.update(dict(zip(ctx.args[::2], ctx.args[1::2])))
    if background:
        fork_broker()
    broker_inst = VMBroker(**broker_args)
    broker_inst.checkout()


@cli.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
@click.option(
    "--workflow", type=str, help="Get a set of valid arguments for a workflow"
)
@click.option(
    "--provider",
    type=str,
    help="Class-style name of a supported broker provider. (AnsibleTower)",
)
@click.pass_context
def nick_help(ctx, workflow, provider):
    """Get information from an action to determine accepted arguments
    or get a list of valid actions available from a provider
    COMMAND: broker nick-help --<action> <argument>
    COMMAND: broker nick-help --provider <ProviderName>
    """
    broker_args = {}
    if workflow:
        broker_args["workflow"] = workflow
    if provider:
        broker_args["provider"] = provider
    # if additional arguments were passed, include them in the broker args
    broker_args.update(dict(zip(ctx.args[::2], ctx.args[1::2])))
    broker_inst = VMBroker(**broker_args)
    broker_inst.nick_help()


@cli.command()
@click.argument("vm", type=str, nargs=-1)
@click.option("-b", "--background", is_flag=True, help="Run checkin in the background")
@click.option("--all", "all_", is_flag=True, help="Select all VMs")
def checkin(vm, background, all_):
    """Checkin or "remove" a VM or series of VM broker instances

    COMMAND: broker checkin <vm hostname>|<local id>|all

    :param vm: Hostname or local id of host

    :param background: run a new broker subprocess to carry out command
    """
    if background:
        fork_broker()
    inventory = helpers.load_inventory()
    to_remove = []
    for num, host in enumerate(inventory):
        if str(num) in vm or host["hostname"] in vm or host["name"] in vm or all_:
            to_remove.append(VMBroker.reconstruct_host(host))
    broker_inst = VMBroker(hosts=to_remove)
    broker_inst.checkin()


@cli.command()
@click.option("--details", is_flag=True, help="Display all host details")
@click.option(
    "--sync",
    type=str,
    help="Class-style name of a supported broker provider. (AnsibleTower)",
)
def inventory(details, sync):
    """Get a list of all VMs you've checked out showing hostname and local id
        hostname pulled from list of dictionaries
    """
    if sync:
        VMBroker.sync_inventory(provider=sync)
    logger.info("Pulling local inventory")
    inventory = helpers.load_inventory()
    for num, host in enumerate(inventory):
        if details:
            logger.info(
                f"{num}: {host['hostname'] or host['name']}, Details: {helpers.yaml_format(host)}"
            )
        else:
            logger.info(f"{num}: {host['hostname'] or host['name']}")


@cli.command()
@click.argument("vm", type=str, nargs=-1)
@click.option(
    "-b", "--background", is_flag=True, help="Run extend in the background"
)
@click.option("--all", "all_", is_flag=True, help="Select all VMs")
def extend(vm, background, all_):
    """Extend a host's lease time

    COMMAND: broker extend <vm hostname>|<vm name>|<local id>

    :param vm: Hostname, VM Name, or local id of host

    :param background: run a new broker subprocess to carry out command

    :param all_: Click option all
    """
    if background:
        fork_broker()
    inventory = helpers.load_inventory()
    to_extend = []
    for num, host in enumerate(inventory):
        if str(num) in vm or host["hostname"] in vm or host["name"] in vm or all_:
            to_extend.append(VMBroker.reconstruct_host(host))
    broker_inst = VMBroker(hosts=to_extend)
    broker_inst.extend()


@cli.command()
@click.argument("vm", type=str, nargs=-1)
@click.option(
    "-b", "--background", is_flag=True, help="Run duplicate in the background"
)
@click.option("--all", "all_", is_flag=True, help="Select all VMs")
def duplicate(vm, background, all_):
    """Duplicate a broker-procured vm

    COMMAND: broker duplicate <vm hostname>|<local id>|all

    :param vm: Hostname or local id of host

    :param background: run a new broker subprocess to carry out command

    :param all_: Click option all
    """
    if background:
        fork_broker()
    inventory = helpers.load_inventory()
    for num, host in enumerate(inventory):
        if str(num) in vm or host["hostname"] in vm or host["name"] in vm or all_:
            broker_args = host.get("_broker_args")
            if broker_args:
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
def execute(ctx, background, workflow, nick, output_format, artifacts):
    """Execute an arbitrary provider action
    COMMAND: broker execute --workflow "workflow-name" --workflow-arg1 something
    or
    COMMAND: broker execute --nick "nickname"

    :param ctx: clicks context object

    :param background: run a new broker subprocess to carry out command

    :param workflow: workflow template stored in Ansible Tower, passed in as a string

    :param nick: shortcut for arguments saved in settings.yaml, passed in as a string

    :param output_format: change the format of the output to one of the choice options

    :param artifacts: AnsibleTower provider specific option for choosing what to return
    """
    broker_args = {}
    if nick:
        broker_args["nick"] = nick
    if workflow:
        broker_args["workflow"] = workflow
    if artifacts:
        broker_args["artifacts"] = artifacts
    # if additional arguments were passed, include them in the broker args
    broker_args.update(dict(zip(ctx.args[::2], ctx.args[1::2])))
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
