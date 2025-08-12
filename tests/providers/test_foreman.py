from contextlib import nullcontext
import json

import pytest

from broker import Broker
from broker.binds.foreman import ForemanBind
from broker.exceptions import (
    AuthenticationError,
    ForemanBindError,
    ProviderError,
    ConfigurationError,
)
from broker.helpers import MockStub
from broker.providers.foreman import Foreman
from broker.settings import create_settings


HOSTGROUP_VALID = "hg1"
HOSTGROUP_INVALID = "hg7"


class ForemanApiStub(MockStub, ForemanBind):
    """Runtime to mock queries to Foreman."""

    def __init__(self, broker_settings=None, **kwargs):
        # Initialize MockStub and bind with provided broker_settings
        MockStub.__init__(self, in_dict={})
        ForemanBind.__init__(self, broker_settings=broker_settings)

    def _post(self, url, **kwargs):
        if "/api/job_invocations" in url:
            command_slug = kwargs["json"]["job_invocation"]["inputs"]["command"].split(" ")[0]
            with open("tests/data/foreman/fake_jobs.json") as jobs_file:
                job_data = json.load(jobs_file)
            return job_data[command_slug]
        if "/api/hosts" in url:
            with open("tests/data/foreman/fake_hosts.json") as hosts_file:
                host_data = json.load(hosts_file)
                return host_data
        print(url)

    def _get(self, url):
        with open("tests/data/foreman/fake_get.json") as file_:
            data = json.load(file_)
        try:
            return data[url]
        except:
            raise ProviderError(
                provider=self.__class__.__name__,
                message=f"Could not find endpoint {url}",
            )

    def _delete(self, url, **kwargs):
        print(url)

    def obtain_id_from_name(self, resource_type, resource_name):
        with open("tests/data/foreman/fake_resources.json") as resources_file:
            resources_data = json.load(resources_file)
        try:
            return resources_data[resource_type][resource_name]
        except:
            raise ProviderError(
                provider=self.__class__.__name__,
                message=f"Could not find {resource_name} in {resource_type}",
            )

    def job_output(self, job_id):
        jobs = {
            42: "success",
            43: "simple",
            44: "complex",
            45: "hostname",
            46: "complex-fail",
        }

        with open("tests/data/foreman/fake_jobs.json") as jobs_file:
            job_data = json.load(jobs_file)
        job_key = jobs[job_id]

        return job_data[job_key]["result"]

    def wait_for_job_to_finish(self, job_id):
        return

    def wait_for_host_to_install(self, hostname):
        return


# Minimal in-memory settings for tests
@pytest.fixture
def broker_settings():
    return create_settings(
        config_dict={
            "FOREMAN": {
                "organization": "ORG",
                "location": "LOC",
                "foreman_username": "test_user",
                "foreman_password": "test_password",
                "foreman_url": "https://foreman.example.com",
                "name_prefix": "broker",
                "verify": False,
            }
        },
        perform_migrations=False,
    )


@pytest.fixture
def foreman_stub(broker_settings):
    # Foreman provider expects the full settings object and a bind class
    return Foreman(bind=ForemanApiStub, broker_settings=broker_settings)


def test_empty_init(broker_settings):
    assert Foreman(
        bind=ForemanApiStub,
        broker_settings=broker_settings,
    )


@pytest.fixture
def mock_broker(broker_settings):
    # Create a Broker instance with the mocked settings for tests that need it
    return Broker(broker_settings=broker_settings)


def test_inventory(foreman_stub):
    inventory = foreman_stub.get_inventory()
    assert len(inventory) == 2
    assert inventory[1]["name"] == "host2.fq.dn"
    assert inventory[0]["ip"] == "1.2.3.4"


def test_positive_host_creation(foreman_stub):
    new_host = foreman_stub.create_host(hostgroup=HOSTGROUP_VALID)
    assert new_host["name"] == "broker.local"
    assert new_host["mac"] == "00:11:22:33:44:55"


def test_negative_host_creation(foreman_stub):
    with pytest.raises(ProviderError):
        foreman_stub.create_host(hostgroup=HOSTGROUP_INVALID)


def test_positive_host(foreman_stub, mock_broker):
    bx = mock_broker
    new_host = foreman_stub.create_host(hostgroup=HOSTGROUP_VALID)
    host = foreman_stub.construct_host(new_host, bx.host_classes)

    assert isinstance(host, bx.host_classes["host"])
    assert host.hostname == "broker.local"


def test_positive_remote_execution(foreman_stub, mock_broker):
    bx = mock_broker
    new_host = foreman_stub.create_host(hostgroup=HOSTGROUP_VALID)
    host = foreman_stub.construct_host(new_host, bx.host_classes)

    result = host.execute("success")
    complex_result = host.execute("complex")

    assert result.status == 0
    assert complex_result.status == 0


def test_negative_remote_execution(foreman_stub, mock_broker):
    bx = mock_broker
    new_host = foreman_stub.create_host(hostgroup=HOSTGROUP_VALID)
    host = foreman_stub.construct_host(new_host, bx.host_classes)

    simple_result = host.execute("simple")
    complex_result = host.execute("complex-fail")

    assert simple_result.status == 1
    assert complex_result.status == 100


@pytest.mark.parametrize(
    "response,expected,context",
    [
        ({1: 2}, {1: 2}, nullcontext()),
        (
            {"error": {"message": "Unable to authenticate user"}},
            None,
            pytest.raises(AuthenticationError),
        ),
        (
            {"error": {"full_messages": "foo", "message": "bar"}},
            None,
            pytest.raises(ForemanBindError),
        ),
        ({"errors": {"base": "foo"}}, None, pytest.raises(ForemanBindError)),
        ({"error": {"full_messages": ["bar"]}}, None, pytest.raises(ForemanBindError)),
    ],
)
def test_interpret_response(response, expected, context, broker_settings):
    bind = ForemanBind(broker_settings=broker_settings)

    with context:
        assert bind._interpret_response(response) == expected
