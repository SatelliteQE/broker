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


HOSTGROUP_VALID = "hg1"
HOSTGROUP_INVALID = "hg7"


class ForemanApiStub(MockStub, ForemanBind):
    """Runtime to mock queries to Foreman."""

    def __init__(self, config=None, **kwargs):  # Added config parameter
        MockStub.__init__(self, in_dict={})
        ForemanBind.__init__(self, broker_settings=config)

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


@pytest.fixture
def config_stub():
    foreman_settings = MockStub(
        url="https://foreman.example.com",
        username="test_user",
        password="test_password",
        prefix="/api",
        verify_ssl=False,
        default_hostgroup="default_hg",
        default_organization="Default Organization",
        default_location="Default Location",
        # Add any other settings Foreman provider or ForemanBind might access
        # For example, if ForemanBind uses settings.FOREMAN.timeout
        timeout=60,
        host_power_action_timeout=120,
        host_power_action_retry_count=3,
        host_power_action_retry_delay=10,
        host_build_timeout=600,
        host_build_retry_count=5,
        host_build_retry_delay=30,
        remote_execution_timeout=300,
        remote_execution_retry_count=2,
        remote_execution_retry_delay=15,
        inventory_source="foreman",  # Example, if used
        # Ensure all keys accessed like self._settings.FOREMAN.get('some_key') are present
        # or that .get() has a default if the key might be missing.
    )
    # The main settings object should have a FOREMAN attribute
    return MockStub(FOREMAN=foreman_settings)


@pytest.fixture
def api_stub(config_stub):  # api_stub now depends on config_stub
    # Pass the FOREMAN part of the config to ForemanApiStub if it expects that structure
    # Or pass the whole config_stub if ForemanApiStub accesses other parts of settings
    return ForemanApiStub(config=config_stub.FOREMAN)


@pytest.fixture
def foreman_stub(api_stub, config_stub):
    # Foreman provider expects the whole config object, not just FOREMAN part
    return Foreman(bind=lambda: api_stub, config=config_stub)


def test_empty_init(config_stub):  # Pass config_stub
    # Foreman init: self._settings = config or settings
    # self.bind = bind or ForemanBind(config=self._settings)
    # So, config_stub is needed here.
    assert Foreman(bind=lambda: ForemanApiStub(config=config_stub.FOREMAN), config=config_stub)


@pytest.fixture
def mock_broker(config_stub):
    # Create a Broker instance with the mocked config for tests that need it
    # This ensures that Broker() uses the same mocked settings as the provider
    return Broker(config=config_stub)


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


def test_positive_host(foreman_stub, mock_broker):  # Use mock_broker
    bx = mock_broker  # Use the broker instance with mocked config
    new_host = foreman_stub.create_host(hostgroup=HOSTGROUP_VALID)
    host = foreman_stub.construct_host(new_host, bx.host_classes)

    assert isinstance(host, bx.host_classes["host"])
    assert host.hostname == "broker.local"


def test_positive_remote_execution(foreman_stub, mock_broker):  # Use mock_broker
    bx = mock_broker
    new_host = foreman_stub.create_host(hostgroup=HOSTGROUP_VALID)
    host = foreman_stub.construct_host(new_host, bx.host_classes)

    result = host.execute("success")
    complex_result = host.execute("complex")

    assert result.status == 0
    assert complex_result.status == 0


def test_negative_remote_execution(foreman_stub, mock_broker):  # Use mock_broker
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
def test_interpret_response(response, expected, context, config_stub):  # Pass config_stub
    # ForemanBind init needs settings, so pass the FOREMAN part of config_stub
    bind = ForemanBind(
        broker_settings=config_stub.FOREMAN  # Pass the FOREMAN specific settings
    )

    with context:
        assert bind._interpret_response(response) == expected
