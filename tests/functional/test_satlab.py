from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from click.testing import CliRunner

from broker import Broker
from broker.commands import cli
from broker.hosts import Host
from broker.providers.ansible_tower import AnsibleTower
from broker.settings import inventory_path
from broker.settings import settings_path as SETTINGS_PATH

SCENARIO_DIR = Path("tests/data/cli_scenarios/satlab")


@pytest.fixture(scope="module", autouse=True)
def skip_if_not_configured():
    try:
        AnsibleTower()
    except Exception as err:
        pytest.skip(f"AnsibleTower is not configured correctly: {err}")


@pytest.fixture(scope="module")
def temp_inventory():
    """Temporarily move the local inventory, then move it back when done"""
    backup_path = inventory_path.rename(f"{inventory_path.absolute()}.bak")
    yield
    CliRunner().invoke(cli, ["checkin", "--all", "--filter", "_broker_provider<AnsibleTower"])
    inventory_path.unlink()
    backup_path.rename(inventory_path)


@pytest.mark.parametrize(
    "args_file", [f for f in SCENARIO_DIR.iterdir() if f.name.startswith("checkout_")], ids=lambda f: f.name.split(".")[0]
)
def test_checkout_scenarios(args_file, temp_inventory):
    result = CliRunner().invoke(cli, ["checkout", "--args-file", args_file])
    assert result.exit_code == 0


@pytest.mark.parametrize(
    "args_file", [f for f in SCENARIO_DIR.iterdir() if f.name.startswith("execute_")], ids=lambda f: f.name.split(".")[0]
)
def test_execute_scenarios(args_file):
    result = CliRunner().invoke(cli, ["execute", "--args-file", args_file])
    assert result.exit_code == 0


def test_inventory_sync():
    result = CliRunner().invoke(cli, ["inventory", "--sync", "AnsibleTower"])
    assert result.exit_code == 0


def test_workflows_list():
    result = CliRunner().invoke(cli, ["providers", "AnsibleTower", "--workflows"])
    assert result.exit_code == 0


def test_workflow_query():
    result = CliRunner().invoke(cli, ["providers", "AnsibleTower", "--workflow", "list-templates"])
    assert result.exit_code == 0


# ----- Broker API Tests -----


def test_tower_host():
    with Broker(workflow="deploy-rhel") as r_host:
        res = r_host.execute("hostname")
        assert res.stdout.strip() == r_host.hostname
        remote_dir = "/tmp/fake"
        r_host.session.sftp_write(str(SETTINGS_PATH), f"{remote_dir}/", ensure_dir=True)
        res = r_host.execute(f"ls {remote_dir}")
        assert SETTINGS_PATH.name in res.stdout
        with NamedTemporaryFile() as tmp:
            r_host.session.sftp_read(f"{remote_dir}/{SETTINGS_PATH.name}", tmp.file.name)
            data = r_host.session.sftp_read(
                f"{remote_dir}/{SETTINGS_PATH.name}", return_data=True
            )
            assert (
                SETTINGS_PATH.read_bytes() == Path(tmp.file.name).read_bytes()
            ), "Local file is different from the received one"
            assert (
                SETTINGS_PATH.read_bytes() == data
            ), "Local file is different from the received one (return_data=True)"
            assert data == Path(tmp.file.name).read_bytes(), "Received files do not match"
        # test the tail_file context manager
        tailed_file = f"{remote_dir}/tail_me.txt"
        r_host.execute(f"echo 'hello world' > {tailed_file}")
        with r_host.session.tail_file(tailed_file) as tf:
            r_host.execute(f"echo 'this is a new line' >> {tailed_file}")
        assert "this is a new line" in tf.contents
        assert "hello world" not in tf.contents


def test_tower_host_mp():
    with Broker(workflow="deploy-rhel", _count=3) as r_hosts:
        for r_host in r_hosts:
            res = r_host.execute("hostname")
            assert res.stdout.strip() == r_host.hostname
            remote_dir = "/tmp/fake"
            r_host.session.sftp_write(str(SETTINGS_PATH), f"{remote_dir}/", ensure_dir=True)
            res = r_host.execute(f"ls {remote_dir}")
            assert SETTINGS_PATH.name in res.stdout
            with NamedTemporaryFile() as tmp:
                r_host.session.sftp_read(f"{remote_dir}/{SETTINGS_PATH.name}", tmp.file.name)
                data = r_host.session.sftp_read(
                    f"{remote_dir}/{SETTINGS_PATH.name}", return_data=True
                )
                assert (
                    SETTINGS_PATH.read_bytes() == Path(tmp.file.name).read_bytes()
                ), "Local file is different from the received one"
                assert (
                    SETTINGS_PATH.read_bytes() == data
                ), "Local file is different from the received one (return_data=True)"
                assert data == Path(tmp.file.name).read_bytes(), "Received files do not match"
        # test remote copy from one host to another
        r_hosts[0].session.remote_copy(
            source=f"{remote_dir}/{SETTINGS_PATH.name}",
            dest_host=r_hosts[1],
            dest_path=f"/root/{SETTINGS_PATH.name}",
        )
        res = r_hosts[1].execute(f"ls /root")
        assert SETTINGS_PATH.name in res.stdout


def test_tower_provider_labels():
    """Assert labels being created on AAP and OSP metadata 
    being attached accordingly
    """
    with Broker(workflow="deploy-rhel", provider_labels={"l1": "v1", "l2": ""}) as r_host:
        # check provider labels in the resulting host object
        assert r_host.provider_labels.get("l1") == "v1"
        assert r_host.provider_labels.get("l2") == ""
        # assert the AAP labels got created on the provider
        aap_labels = [l.name for l in r_host._prov_inst._v2.labels.get().results]
        assert "l1=v1" in aap_labels
        assert "l2" in aap_labels
