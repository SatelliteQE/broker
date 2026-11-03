from broker import broker, Broker, helpers, settings
from broker.hosts import Host
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


@pytest.mark.parametrize("sequential", [False, True])
@pytest.mark.parametrize("failure_stage", ["release", "close"])
@pytest.mark.parametrize("failed_indices", [(), (2,), (0, 2), (0, 1, 2)])
def test_broker_checkin_failures(
    broker_settings, monkeypatch, tmp_path, sequential, failure_stage, failed_indices
):
    """Save successful checkins before reporting every failed host."""
    monkeypatch.setattr("broker.settings.inventory_path", tmp_path / "inventory.yaml")
    hosts = [
        Host(hostname=f"host-{i}.example.com", name=f"vm-{i}", broker_settings=broker_settings)
        for i in range(3)
    ]
    failed_hosts = [hosts[i] for i in failed_indices]
    attempts = []

    def close(host):
        attempts.append(("close", host.name))
        if failure_stage == "close" and host in failed_hosts:
            raise RuntimeError(f"close failed for {host.name}")

    def release(host):
        attempts.append(("release", host.name))
        if failure_stage == "release" and host in failed_hosts:
            raise Broker.ProviderError("TestProvider", f"release failed for {host.name}")

    monkeypatch.setattr(Host, "close", close)
    monkeypatch.setattr(Host, "release", release)
    helpers.update_inventory(add=[host.to_dict() for host in hosts])
    broker_inst = Broker(hosts=hosts, broker_settings=broker_settings)

    if failed_hosts:
        with pytest.raises(Broker.BrokerError) as exc_info:
            broker_inst.checkin(sequential=sequential)
        for host in failed_hosts:
            assert host.hostname in str(exc_info.value)
            assert f"{failure_stage} failed for {host.name}" in str(exc_info.value)
    else:
        broker_inst.checkin(sequential=sequential)

    assert broker_inst._hosts == failed_hosts
    assert helpers.load_inventory() == [host.to_dict() for host in failed_hosts]
    for host in hosts:
        assert attempts.count(("close", host.name)) == 1
        expected_releases = 0 if failure_stage == "close" and host in failed_hosts else 1
        assert attempts.count(("release", host.name)) == expected_releases


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
    assert not helpers.load_inventory(filter='@inv._broker_provider == "TestProvider"'), "Inventory cleanup failed before test execution."

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


def test_origin_captured_before_threading(broker_settings):
    """Test that origin is captured before threading and passed to provider methods."""
    broker_inst = Broker(nick="test_nick", broker_settings=broker_settings)
    # Verify that _broker_origin is captured during checkout
    host_checkout = broker_inst.checkout()
    # After checkout, the _kwargs should contain _broker_origin
    assert "_broker_origin" in broker_inst._kwargs
    # The origin should indicate it came from a test
    assert "test_origin_captured_before_threading" in broker_inst._kwargs["_broker_origin"]
    broker_inst.checkin()


def test_logging_not_reconfigured_on_broker_init(broker_settings):
    """Test that instantiating Broker with LOGGING overrides does not mutate global logging.

    This is a regression test to ensure that additional Broker instances,
    particularly in multi_manager contexts, do not reconfigure global logging settings.
    """
    import logging
    from broker.logging import setup_logging

    # Configure logging once at startup (simulating CLI initialization)
    setup_logging(
        console_level="warning",
        file_level="info",
        log_path="logs/test_broker.log",
        structured=False,
    )

    # Capture the initial root logger configuration
    root_logger = logging.getLogger()
    initial_handlers = root_logger.handlers.copy()
    initial_level = root_logger.level
    initial_handler_count = len(initial_handlers)
    initial_handler_levels = [h.level for h in initial_handlers]

    # Create a settings object with different LOGGING configuration
    test_config = broker_settings.to_dict()
    test_config["LOGGING"] = {
        "console_level": "debug",
        "file_level": "trace",
        "log_path": "logs/different.log",
        "structured": True,
    }
    from broker.settings import create_settings
    custom_settings = create_settings(config_dict=test_config)

    # Instantiate Broker with custom settings that include LOGGING overrides
    broker_inst = Broker(broker_settings=custom_settings, nick="test_nick")

    # Assert that root logger configuration remains unchanged
    assert len(root_logger.handlers) == initial_handler_count, \
        f"Handler count changed from {initial_handler_count} to {len(root_logger.handlers)}"
    assert root_logger.level == initial_level, \
        f"Root logger level changed from {initial_level} to {root_logger.level}"
    assert [h.level for h in root_logger.handlers] == initial_handler_levels, \
        "Handler levels changed after Broker instantiation"
    assert root_logger.handlers == initial_handlers, \
        "Root logger handlers were modified after Broker instantiation"


class SomeException(Exception):
    pass
