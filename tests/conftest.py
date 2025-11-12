import os
from pathlib import PurePath
import subprocess
import time

import pexpect
import pytest
from pathlib import Path
from broker import settings

# Import both PodmanBind and DockerBind
from broker.binds.containers import PodmanBind, DockerBind


TESTDIR = PurePath(__file__).parent
TEST_SERVER_IMAGE = "ghcr.io/jacobcallahan/hussh/hussh-test-server:latest"


def pytest_sessionstart(session):
    """For things that need to happen before Broker is loaded."""
    os.environ["BROKER_NO_GLOBAL_CONFIG"] = "True"
    
    # Set up logging for the test session
    from broker.logging import setup_logging
    setup_logging(
        console_level="warning",
        file_level="debug",
        log_path="logs/broker_tests.log",
        structured=False,
    )


@pytest.fixture(scope="session")
def broker_data_dir():
    """Return path to the test data directory"""
    return Path(os.path.dirname(__file__)) / "data"


@pytest.fixture(scope="session", autouse=True)
def set_broker_dir(broker_data_dir):
    """Set the Broker directory to the test data directory"""
    settings.BROKER_DIRECTORY = broker_data_dir
    settings.inventory_path = broker_data_dir / "inventory.yaml"


@pytest.fixture(scope="session")
def broker_settings_path(broker_data_dir):
    """Return path to test settings file - for tests that need the actual file"""
    return broker_data_dir / "broker_settings.yaml"


@pytest.fixture(scope="session")
def broker_settings():
    """Create and return a broker settings object for testing with minimal test configuration"""
    test_config = {
        "SSH": {
            "backend": "hussh",
            "host_username": "root",
            "host_password": "toor",
            "host_ssh_port": 22,
            "host_ssh_key_filename": "tests/data/ssh/test_key",
            "host_ipv6": False,
            "host_ipv4_fallback": True,
        },
        "LOGGING": {
            "console_level": "info", 
            "file_level": "debug",
        },
        "FOREMAN": {
            "foreman_url": "https://test.fore.man",
            "foreman_username": "admin",
            "foreman_password": "secret",
            "organization": "ORG",
            "location": "LOC",
            "verify": "./ca.crt",
            "name_prefix": "broker",
        },
        "CONTAINER": {
            "host_username": "username",
            "host_password": "password",
            "host_port": None,
            "network": None,
            "default": True,
            "runtime": "podman",
            "results_limit": 50,
            "auto_map_ports": False,
        },
        "TESTPROVIDER": {
            "instances": {
                "test1": {
                    "foo": "bar",
                    "default": True,
                },
                "test2": {
                    "foo": "baz",
                    "override_envars": True,
                },
                "bad": {
                    "nothing": False,
                    # Note: deliberately missing 'foo' to trigger validation error
                },
            },
            "config_value": "something",
        },
        "NICKS": {
            "test_nick": {
                "test_action": "fake",
                "arg1": "abc",
                "arg2": 123,
                "arg3": True,
            },
        },
    }
    return settings.create_settings(config_dict=test_config)


@pytest.fixture(scope="session")
def container_client():
    """Provide a container client, preferring Podman, falling back to Docker."""
    client = None
    last_exception = None

    # Try podman with default connection first
    try:
        import podman
        client = PodmanBind()  # Don't specify host to use default connection
        # Test if the client is functional
        client.client.images.list()
        return client
    except (ImportError, Exception) as e:
        last_exception = e
        client = None

    # Try podman with localhost
    try:
        import podman

        client = PodmanBind(host="localhost")
        # Test if the client is functional
        client.client.images.list()
        return client
    except (ImportError, Exception) as e:
        # Store exception if Podman fails
        last_exception = e
        client = None  # Ensure client is reset if podman failed

    try:
        # Fallback to docker if podman is not available or functional
        import docker

        client = DockerBind(host="localhost")
        # Test if the client is functional
        client.client.images.list()
        return client
    except (ImportError, Exception) as e:
        # Store exception if Docker also fails
        last_exception = e

    # Raise an error if neither is functional
    raise ImportError(
        f"Neither podman nor docker library is installed or functional. Last error: {last_exception}"
    )


@pytest.fixture(scope="session")
def ensure_test_server_image(container_client):
    """Ensure that the test server image is available using the selected client."""
    # Use the client provided by the container_client fixture
    client = container_client
    try:
        # Use the client's method to get the image
        client.client.images.get(TEST_SERVER_IMAGE)
    except Exception:  # Handle potential exceptions from either podman or docker
        client.pull_image(TEST_SERVER_IMAGE)


@pytest.fixture(scope="session")  # Removed autouse=True
def run_test_server(ensure_test_server_image, container_client):
    """Run a test server in a container using the selected client."""
    # Use the client provided by the container_client fixture
    client = container_client
    managed = False

    try:  # check to see if the container is already running
        # Use the client's method to get the container
        container = client.client.containers.get("hussh-test-server")
        if container.status != "running":
            container.start()
            time.sleep(5)  # give the server time to start
    except Exception:  # if not, start it (handle potential exceptions from either podman or docker)
        # Use the client's method to run the container
        container = client.client.containers.run(
            TEST_SERVER_IMAGE,
            detach=True,
            ports={"22/tcp": 8022},
            name="hussh-test-server",
        )
        managed = True
        time.sleep(5)  # give the server time to start

    yield container

    if managed:
        container.stop()
        container.remove()


@pytest.fixture(scope="session")
def run_second_server(ensure_test_server_image, container_client):
    """Run a second test server in a container using the selected client."""
    # Use the client provided by the container_client fixture
    client = container_client

    try:  # check to see if the container is already running
        # Use the client's method to get the container
        container = client.client.containers.get("hussh-test-server2")
    except Exception:  # if not, start it (handle potential exceptions from either podman or docker)
        # Use the client's method to run the container
        container = client.client.containers.run(
            TEST_SERVER_IMAGE,
            detach=True,
            ports={"22/tcp": 8023},
            name="hussh-test-server2",
        )
        time.sleep(5)  # give the server time to start

    yield container

    container.stop()
    container.remove()


@pytest.fixture(scope="session")
def setup_agent_auth():
    # Define the key paths
    base_key = TESTDIR / "data/ssh/test_key"
    auth_key = TESTDIR / "data/ssh/auth_test_key"

    # Start the ssh-agent and get the environment variables
    output = subprocess.check_output(["ssh-agent", "-s"])
    env = dict(line.split("=", 1) for line in output.decode().splitlines() if "=" in line)

    # Set the SSH_AUTH_SOCK and SSH_AGENT_PID environment variables
    os.environ["SSH_AUTH_SOCK"] = env["SSH_AUTH_SOCK"]
    os.environ["SSH_AGENT_PID"] = env["SSH_AGENT_PID"]

    # Add the keys to the ssh-agent
    result = subprocess.run(["ssh-add", str(base_key)], capture_output=True, text=True, check=False)
    print(result.stdout)
    print(result.stderr)
    # The auth_key is password protected
    child = pexpect.spawn("ssh-add", [str(auth_key)])
    child.expect("Enter passphrase for .*: ")
    child.sendline("husshpuppy")
    yield
    # Kill the ssh-agent after the tests have run
    subprocess.run(["ssh-agent", "-k"], check=True)


@pytest.fixture
def set_envars(monkeypatch, request):
    """Set environment variables for a test and clean up afterward"""
    for envar, value in request.param:
        monkeypatch.setenv(envar, value)
    yield
