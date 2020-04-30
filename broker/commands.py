from pathlib import Path
import click
from logzero import logger
from dynaconf import settings
from broker.broker import VMBroker
from broker import logger as b_log
from broker import helpers


@click.group()
@click.option("--debug", is_flag=True)
def cli(debug):
    if debug or settings.DEBUG:
        b_log.setup_logzero(level="debug")
    logger.debug("I'm in debug mode")


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
        broker_args = helpers.resolve_nick(nick)
    if workflow:
        broker_args["workflow"] = workflow
    # if additional arguments were passes, include them in the broker args
    broker_args.update(dict(zip(ctx.args[::2], ctx.args[1::2])))
    broker_inst = VMBroker(**broker_args)
    broker_inst.checkout()
    new_hosts = []
    for host in broker_inst._hosts:
        logger.info(f"{host.__class__.__name__}: {host.hostname}")
        new_hosts.append(host.to_dict())
    helpers.update_inventory(new_hosts)


@cli.command()
@click.argument("vm", type=str, nargs=-1)
def checkin(vm):
    """Checkin a VM or series of VMs

    COMMAND: broker checkin <vm hostname>|<local id>|all
    """
    inventory = helpers.load_inventory()
    to_remove = []
    for num, host in enumerate(inventory):
        if str(num) in vm or host["hostname"] in vm or "all" in vm:
            to_remove.append((num, host))
    # reconstruct the hosts and call their release methhod
    for num, host in to_remove[::-1]:
        del inventory[num]
        logger.info(f"Checking in {host['hostname']}")
    helpers.update_inventory(inventory, replace_all=True)


@cli.command()
@click.option("--details", is_flag=True, help="Display all hist details")
def inventory(details):
    """Get a list of all VMs you've checked out"""
    logger.info("Pulling local inventory")
    inventory = helpers.load_inventory()
    for num, host in enumerate(inventory):
        if details:
            logger.info(f"{num}: {host.pop('hostname')}, Details: {host}")
        else:
            logger.info(f"{num}: {host['hostname']}")
