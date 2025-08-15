from broker import broker, Broker, helpers, settings
from broker.providers import test_provider
import pytest


def test_empty_init(broker_settings):
    """Broker should be able to init without any arguments"""
    broker_inst = Broker(broker_settings=broker_settings)
    assert isinstance(broker_inst, Broker)


def test_kwarg_assignment(broker_settings):
    """Broker should copy all kwargs into its _kwargs attribute"""
    broker_kwargs = {"test": "value", "another": 17}
    broker_inst = Broker(broker_settings=broker_settings, **broker_kwargs)
    assert broker_inst._kwargs == broker_kwargs


def test_full_init(broker_settings):
    """Make sure all init checks and assignments work"""
    broker_hosts = ["test1.example.com", "test2.example.com", "test3.example.com"]
    broker_inst = Broker(
        hosts=broker_hosts, test_action="blank", nick="test_nick", broker_settings=broker_settings
    )
    assert broker_inst._hosts == broker_hosts
    assert not broker_inst._kwargs.get("hosts")
    assert broker_inst._provider_actions == {
        "test_action": (test_provider.TestProvider, "test_action")
    }
    assert not broker_inst._kwargs.get("nick")
    assert broker_inst._kwargs["test_action"] == "blank"


def test_specified_instance(broker_settings):
    """Make sure that a specified instance is used"""
    broker_inst = Broker(nick="test_nick", TestProvider="test2", broker_settings=broker_settings)
    host_checkout = broker_inst.checkout()
    assert host_checkout._broker_provider_instance == "test2"


def test_broker_e2e(broker_settings):
    """Run through the base functionality of broker"""
    broker_inst = Broker(nick="test_nick", broker_settings=broker_settings)
    host_checkout = broker_inst.checkout()
    assert len(broker_inst._hosts) == 1
    broker_host = broker_inst._hosts[0]
    assert broker_host.hostname == "test.host.example.com"
    assert broker_host == host_checkout
    broker_host_dict = broker_host.to_dict()
    assert broker_host_dict["_broker_provider"] == "TestProvider"
    broker_inst.checkin()
    assert len(broker_inst._hosts) == 0


def test_broker_empty_checkin(broker_settings):
    """Try to checkin with no hosts on the instance"""
    broker_inst = Broker(nick="test_nick", broker_settings=broker_settings)
    assert not broker_inst._hosts
    broker_inst.checkin()


def test_broker_checkin_n_sync_empty_hostname(broker_settings):
    """Test that broker can checkin and sync inventory with a host that has empty hostname"""
    # Ensure a clean slate by removing any existing TestProvider hosts from inventory
    initial_inventory = helpers.load_inventory(filter='@inv._broker_provider == "TestProvider"')
    for host_entry in initial_inventory:
        if host_entry.get("hostname"):
            helpers.update_inventory(remove=host_entry["hostname"])
        else:
            pass

    # Verify cleanup before proceeding
    assert not helpers.load_inventory(filter='@inv._broker_provider == "TestProvider"'), (
        "Inventory cleanup failed before test execution."
    )

    broker_inst = broker.Broker(nick="test_nick", broker_settings=broker_settings)
    broker_inst.checkout()
    inventory = helpers.load_inventory(filter='@inv._broker_provider == "TestProvider"')
    assert len(inventory) == 1  # This assertion should now reliably pass
    inventory[0]["hostname"] = None
    # remove the host from the inventory
    helpers.update_inventory(remove="test.host.example.com")
    # add the host back with no hostname
    helpers.update_inventory(add=inventory)
    hosts = broker_inst.from_inventory(filter='@inv._broker_provider == "TestProvider"')
    assert len(hosts) == 1
    assert hosts[0].hostname is None
    broker_inst = broker.Broker(hosts=hosts, broker_settings=broker_settings)
    broker_inst.checkin()
    assert not broker_inst.from_inventory(filter='@inv._broker_provider == "TestProvider"'), "Host was not removed from inventory after checkin"


def test_mp_checkout(broker_settings):
    """Test that broker can checkout multiple hosts using multiprocessing"""
    VM_COUNT = 50  # This is intentionaly made high to catch run condition that
    # was discovered when reviewing
    # https://github.com/SatelliteQE/broker/pull/53
    # With count like this, I've got reproducibility probability
    # arround 0.5
    broker_inst = Broker(nick="test_nick", broker_settings=broker_settings, _count=VM_COUNT)
    broker_inst.checkout()
    assert len(broker_inst._hosts) == VM_COUNT
    broker_inst.checkin()
    assert len(broker_inst._hosts) == 0


def test_mp_checkout_twice(broker_settings):
    broker_inst = Broker(nick="test_nick", broker_settings=broker_settings, _count=2)

    def cycle():
        assert len(broker_inst.checkout()) == 2
        assert len(broker_inst._hosts) == 2

        broker_inst.checkin()
        assert len(broker_inst._hosts) == 0

    cycle()
    cycle()


def test_multi_manager(broker_settings):
    """Test that we get the proper data structure and names as expected
    when using Broker.multi_manager.
    """
    with Broker.multi_manager(
        test_1={"nick": "test_nick"},
        test_2={"nick": "test_nick", "_count": 2},
        broker_settings=broker_settings,
    ) as host_dict:
        assert "test_1" in host_dict
        assert "test_2" in host_dict
        assert len(host_dict["test_1"]) == 1
        assert len(host_dict["test_2"]) == 2
        assert host_dict["test_1"][0].hostname == "test.host.example.com"
        assert host_dict["test_2"][1].hostname == "test.host.example.com"


class SomeException(Exception):
    pass
