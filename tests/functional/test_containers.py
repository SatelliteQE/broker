from pathlib import Path
import pytest
from click.testing import CliRunner
from broker import Broker
from broker.commands import cli
from broker.providers.container import Container
from broker.settings import inventory_path

SCENARIO_DIR = Path("tests/data/cli_scenarios/containers")


@pytest.fixture(scope="module", autouse=True)
def skip_if_not_configured():
    try:
        Container()
    except Exception as err:
        pytest.skip(f"Container is not configured correctly: {err}")


@pytest.fixture(scope="module")
def temp_inventory():
    """Temporarily move the local inventory, then move it back when done"""
    backup_path = inventory_path.rename(f"{inventory_path.absolute()}.bak")
    yield
    CliRunner().invoke(
        cli, ["checkin", "--all", "--filter", "_broker_provider<Container"]
    )
    inventory_path.unlink()
    backup_path.rename(inventory_path)


# ----- CLI Scenario Tests -----

@pytest.mark.parametrize(
    "args_file", [f for f in SCENARIO_DIR.iterdir() if f.name.startswith("checkout_")]
)
def test_checkout_scenarios(args_file, temp_inventory):
    result = CliRunner().invoke(cli, ["checkout", "--args-file", args_file])
    assert result.exit_code == 0


@pytest.mark.parametrize(
    "args_file", [f for f in SCENARIO_DIR.iterdir() if f.name.startswith("execute_")]
)
def test_execute_scenarios(args_file):
    result = CliRunner().invoke(cli, ["execute", "--args-file", args_file])
    assert result.exit_code == 0


def test_inventory_sync():
    result = CliRunner().invoke(cli, ["inventory", "--sync", "Container"])
    assert result.exit_code == 0


def test_containerhosts_list():
    result = CliRunner().invoke(cli, ["providers", "Container", "--container-hosts"])
    assert result.exit_code == 0


def test_containerhost_query():
    result = CliRunner().invoke(
        cli, ["providers", "Container", "--container-host", "ubi8:latest"]
    )
    assert result.exit_code == 0


# ----- Broker API Tests -----

def test_container_e2e():
    with Broker(container_host="ubi8:latest") as c_host:
        assert c_host._cont_inst.top()['Processes']
        res = c_host.execute("hostname")
        assert res.stdout.strip() == c_host.hostname
        # Test that a file can be uploaded to the container
        c_host.session.sftp_write("broker_settings.yaml", "/root")
        res = c_host.execute("ls")
        assert "broker_settings.yaml" in res.stdout


def test_container_e2e_mp():
    with Broker(container_host="ubi8:latest", _count=7) as c_hosts:
        for c_host in c_hosts:
            assert c_host._cont_inst.top()['Processes']
            res = c_host.execute("hostname")
            assert res.stdout.strip() == c_host.hostname
            # Test that a file can be uploaded to the container
            c_host.session.sftp_write("broker_settings.yaml", "/tmp/fake/")
            res = c_host.execute("ls /tmp/fake")
            assert "broker_settings.yaml" in res.stdout
