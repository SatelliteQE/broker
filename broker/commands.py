from pathlib import Path
import click
import logging
from logzero import logger
from dynaconf import settings
from broker.broker import VMBroker
from broker import logger as b_log
from broker import helpers
from broker.hosts import Host


def update_log_level(ctx, param, value):
    if getattr(logging, value.upper()) is not logger.getEffectiveLevel():
        b_log.setup_logzero(level=value)
        click.echo(f"Log level changed to [{value}]")


@click.group()
@click.option(
    "--log-level",
    type=click.Choice(["info", "warning", "error", "critical", "debug"]),
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
@click.option("--workflow", type=str)
@click.option("--nick", type=str, help="Use a nickname defined in your settings")
@click.pass_context
def checkout(ctx, workflow, nick):
    """Checkout a Virtual Machine
    COMMAND: broker checkout --<action> <argument>
    """
    broker_args = {}
    if nick:
        broker_args["nick"] = nick
    if workflow:
        broker_args["workflow"] = workflow
    # if additional arguments were passed, include them in the broker args
    broker_args.update(dict(zip(ctx.args[::2], ctx.args[1::2])))
    broker_inst = VMBroker(**broker_args)
    broker_inst.checkout()


@cli.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
@click.option("--workflow", type=str)
@click.pass_context
def nick_help(ctx, workflow):
    """Get information from an action to determine accepted arguments
    COMMAND: broker nick-help --<action> <argument>
    """
    broker_args = {}
    if workflow:
        broker_args["workflow"] = workflow
    # if additional arguments were passed, include them in the broker args
    broker_args.update(dict(zip(ctx.args[::2], ctx.args[1::2])))
    broker_inst = VMBroker(**broker_args)
    broker_inst.nick_help()


@cli.command()
@click.argument("vm", type=str, nargs=-1)
@click.option("--all", "all_", is_flag=True, help="Select all VMs")
def checkin(vm, all_):
    """Checkin a VM or series of VMs

    COMMAND: broker checkin <vm hostname>|<local id>|all
    """
    inventory = helpers.load_inventory()
    to_remove = []
    for num, host_export in enumerate(inventory):
        if str(num) in vm or host_export["hostname"] in vm or all_:
            to_remove.append(VMBroker.reconstruct_host(host_export))
    broker_inst = VMBroker(hosts=to_remove)
    broker_inst.checkin()


@cli.command()
@click.option("--details", is_flag=True, help="Display all hist details")
def inventory(details):
    """Get a list of all VMs you've checked out"""
    logger.info("Pulling local inventory")
    inventory = helpers.load_inventory()
    for num, host in enumerate(inventory):
        if details:
            logger.info(
                f"{num}: {host.pop('hostname')}, Details: {helpers.yaml_format(host)}"
            )
        else:
            logger.info(f"{num}: {host['hostname']}")


@cli.command()
@click.argument("vm", type=str, nargs=-1)
@click.option("--all", "all_", is_flag=True, help="Select all VMs")
def duplicate(vm, all_):
    """Duplicate a broker-procured vm

    COMMAND: broker duplicate <vm hostname>|<local id>|all
    """
    inventory = helpers.load_inventory()
    for num, host in enumerate(inventory):
        if str(num) in vm or host["hostname"] in vm or all_:
            broker_args = host.get("_broker_args")
            if broker_args:
                logger.info(f"Duplicating: {host['hostname']}")
                broker_inst = VMBroker(**broker_args)
                broker_inst.checkout()
            else:
                logger.warning(
                    f"Unable to duplicate {host['hostname']}, no _broker_args found"
                )
