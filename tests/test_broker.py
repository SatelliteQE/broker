from broker import broker
from broker.providers import test_provider
import pytest


def test_empty_init():
    """Broker should be able to init without any arguments"""
    broker_inst = broker.Broker()
    assert isinstance(broker_inst, broker.Broker)


def test_kwarg_assignment():
    """Broker should copy all kwargs into its _kwargs attribute"""
    broker_kwargs = {"test": "value", "another": 17}
    broker_inst = broker.Broker(**broker_kwargs)
    assert broker_inst._kwargs == broker_kwargs


def test_full_init():
    """Make sure all init checks and assignments work"""
    broker_hosts = ["test1.example.com", "test2.example.com", "test3.example.com"]
    broker_inst = broker.Broker(
        hosts=broker_hosts, test_action="blank", nick="test_nick"
    )
    assert broker_inst._hosts == broker_hosts
    assert not broker_inst._kwargs.get("hosts")
    assert broker_inst._provider_actions == {
        "test_action": (test_provider.TestProvider, "test_action")
    }
    assert not broker_inst._kwargs.get("nick")
    assert broker_inst._kwargs["test_action"] == "blank"


def test_broker_e2e():
    """Run through the base functionality of broker"""
    broker_inst = broker.Broker(nick="test_nick")
    broker_inst.checkout()
    assert len(broker_inst._hosts) == 1
    broker_host = broker_inst._hosts[0]
    assert broker_host.hostname == "test.host.example.com"
    broker_host_dict = broker_host.to_dict()
    assert broker_host_dict["_broker_provider"] == "TestProvider"
    broker_inst.checkin()
    assert len(broker_inst._hosts) == 0


def test_broker_empty_checkin():
    """Try to checkin with no hosts on the instance"""
    broker_inst = broker.Broker(nick="test_nick")
    assert not broker_inst._hosts
    broker_inst.checkin()


def test_mp_checkout():
    """Test that broker can checkout multiple hosts using multiprocessing"""
    VM_COUNT = 50  # This is intentionaly made high to catch run condition that
    # was discovered when reviewing
    # https://github.com/SatelliteQE/broker/pull/53
    # With count like this, I've got reproducibility probability
    # arround 0.5
    broker_inst = broker.Broker(nick="test_nick", _count=VM_COUNT)
    broker_inst.checkout()
    assert len(broker_inst._hosts) == VM_COUNT
    broker_inst.checkin()
    assert len(broker_inst._hosts) == 0


def test_mp_checkout_twice():
    broker_inst = broker.Broker(nick="test_nick", _count=2)

    def cycle():
        assert len(broker_inst.checkout()) == 2
        assert len(broker_inst._hosts) == 2

        broker_inst.checkin()
        assert len(broker_inst._hosts) == 0

    cycle()
    cycle()


class SomeException(Exception):
    pass


class MyBroker:
    @broker.mp_decorator
    def workload(self):
        return []

    @broker.mp_decorator
    def failing_workload(self):
        raise SomeException()


def test_mp_decorator():
    tested_broker = MyBroker()
    tested_broker._kwargs = dict(_count=2)

    tested_broker.workload()
    with pytest.raises(SomeException):
        tested_broker.failing_workload()
