import os
from pathlib import PurePath
import subprocess
import time

import pexpect
import pytest

# Import both PodmanBind and DockerBind
from broker.binds.containers import PodmanBind, DockerBind


TESTDIR = PurePath(__file__).parent
TEST_SERVER_IMAGE = "ghcr.io/jacobcallahan/hussh/hussh-test-server:latest"


def pytest_sessionstart(session):
    """Put Broker into test mode."""
    os.environ["BROKER_TEST_MODE"] = "True"


@pytest.fixture
def set_envars(request):
    """Set and unset one or more environment variables"""
    if isinstance(request.param, list):
        for pair in request.param:
            os.environ[pair[0]] = pair[1]
        yield
        for pair in request.param:
            del os.environ[pair[0]]
    else:
        os.environ[request.param[0]] = request.param[1]
        yield
        del os.environ[request.param[0]]


@pytest.fixture(scope="session")
def container_client():
    """Provide a container client, preferring Podman, falling back to Docker."""
    client = None
    last_exception = None
    try:
        # Attempt to import and use podman first
        import podman
        client = PodmanBind(host="localhost")
        # Test if the client is functional
        client.client.images.list()
        return client
    except (ImportError, Exception) as e:
        # Store exception if Podman fails
        last_exception = e
        client = None # Ensure client is reset if podman failed

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
    raise ImportError(f"Neither podman nor docker library is installed or functional. Last error: {last_exception}")


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


@pytest.fixture(scope="session", autouse=True)
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
    result = subprocess.run(
        ["ssh-add", str(base_key)], capture_output=True, text=True, check=False
    )
    print(result.stdout)
    print(result.stderr)
    # The auth_key is password protected
    child = pexpect.spawn("ssh-add", [str(auth_key)])
    child.expect("Enter passphrase for .*: ")
    child.sendline("husshpuppy")
    yield
    # Kill the ssh-agent after the tests have run
    subprocess.run(["ssh-agent", "-k"], check=True)
