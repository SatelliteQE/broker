from pathlib import Path
from tempfile import NamedTemporaryFile
import pytest
from click.testing import CliRunner
from broker import Broker
from broker.commands import cli
from broker.providers.container import Container
from broker.settings import inventory_path, settings_path

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
        assert c_host._cont_inst.top()["Processes"]
        res = c_host.execute("hostname")
        assert res.stdout.strip() == c_host.hostname
        remote_dir = "/tmp/fake"
        c_host.session.sftp_write(settings_path, f"{remote_dir}/")
        res = c_host.execute(f"ls {remote_dir}")
        assert str(settings_path.name) in res.stdout
        with NamedTemporaryFile() as tmp:
            c_host.session.sftp_read(f"{remote_dir}/{settings_path.name}", tmp.file.name)
            data = c_host.session.sftp_read(
                f"{remote_dir}/{settings_path.name}", return_data=True
            )
            assert (
                settings_path.read_bytes() == Path(tmp.file.name).read_bytes()
            ), "Local file is different from the received one"
            assert (
                settings_path.read_bytes() == data
            ), "Local file is different from the received one (return_data=True)"
            assert (
                data == Path(tmp.file.name).read_bytes()
            ), "Received files do not match"
        # test the tail_file context manager
        tailed_file = f"{remote_dir}/tail_me.txt"
        c_host.execute(f"echo 'hello world' > {tailed_file}")
        with c_host.session.tail_file(tailed_file) as tf:
            c_host.execute(f"echo 'this is a new line' >> {tailed_file}")
        assert "this is a new line" in tf.stdout
        assert "hello world" not in tf.stdout


def test_container_e2e_mp():
    with Broker(container_host="ubi8:latest", _count=7) as c_hosts:
        for c_host in c_hosts:
            assert c_host._cont_inst.top()["Processes"]
            res = c_host.execute("hostname")
            assert res.stdout.strip() == c_host.hostname
            remote_dir = "/tmp/fake"
            c_host.session.sftp_write(settings_path, f"{remote_dir}/")
            res = c_host.execute(f"ls {remote_dir}")
            assert str(settings_path.name) in res.stdout
            with NamedTemporaryFile() as tmp:
                c_host.session.sftp_read(f"{remote_dir}/{settings_path.name}", tmp.file.name)
                data = c_host.session.sftp_read(
                    f"{remote_dir}/{settings_path.name}", return_data=True
                )
                assert (
                    settings_path.read_bytes() == Path(tmp.file.name).read_bytes()
                ), "Local file is different from the received one"
                assert (
                    settings_path.read_bytes() == data
                ), "Local file is different from the received one (return_data=True)"
                assert (
                    data == Path(tmp.file.name).read_bytes()
                ), "Received files do not match"


def test_broker_multi_manager():
    with Broker.multi_manager(
        ubi7={"container_host": "ubi7:latest"},
        ubi8={"container_host": "ubi8:latest", "_count": 2},
        ubi9={"container_host": "ubi9:latest"},
    ) as multi_hosts:
        assert "ubi7" in multi_hosts and "ubi8" in multi_hosts and "ubi9" in multi_hosts
        assert len(multi_hosts["ubi8"]) == 2
        assert multi_hosts["ubi7"][0]._cont_inst.top()["Processes"]
        assert (
            multi_hosts["ubi8"][1].execute("hostname").stdout.strip()
            == multi_hosts["ubi8"][1].hostname
        )
