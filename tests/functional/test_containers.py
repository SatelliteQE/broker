from pathlib import Path
from tempfile import NamedTemporaryFile
import pytest
from click.testing import CliRunner
from broker import Broker
from broker.commands import cli
from broker.providers.container import Container
from broker.settings import settings_path as SETTINGS_PATH

SCENARIO_DIR = Path("tests/data/cli_scenarios/containers")


@pytest.fixture(scope="module", autouse=True)
def skip_if_not_configured():
    try:
        Container()
    except Exception as err:
        pytest.skip(f"Container is not configured correctly: {err}")


@pytest.fixture(scope="module")
def checkin_containers():
    """Checkin all containers checkout out by the tests."""
    yield
    CliRunner().invoke(cli, ["checkin", "--all", "--filter", "_broker_provider<Container"])


# ----- CLI Scenario Tests -----


@pytest.mark.parametrize(
    "args_file", [f for f in SCENARIO_DIR.iterdir() if f.name.startswith("checkout_")], ids=lambda f: f.name.split(".")[0]
)
def test_checkout_scenarios(args_file, checkin_containers):
    result = CliRunner().invoke(cli, ["checkout", "--args-file", args_file])
    assert result.exit_code == 0


@pytest.mark.parametrize(
    "args_file", [f for f in SCENARIO_DIR.iterdir() if f.name.startswith("execute_")], ids=lambda f: f.name.split(".")[0]
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
    result = CliRunner().invoke(cli, ["providers", "Container", "--container-host", "ubi8:latest"])
    assert result.exit_code == 0


# ----- Broker API Tests -----


def test_container_e2e():
    with Broker(container_host="ubi8:latest", provider_labels={"l1": "v1", "l2": None}) as c_host:
        assert c_host._cont_inst.top()["Processes"]
        res = c_host.execute("hostname")
        assert res.stdout.strip() == c_host.hostname
        remote_dir = "/tmp/fake"
        c_host.session.sftp_write(str(SETTINGS_PATH), f"{remote_dir}/")
        res = c_host.execute(f"ls {remote_dir}")
        assert SETTINGS_PATH.name in res.stdout
        with NamedTemporaryFile() as tmp:
            c_host.session.sftp_read(f"{remote_dir}/{SETTINGS_PATH.name}", tmp.file.name)
            data = c_host.session.sftp_read(
                f"{remote_dir}/{SETTINGS_PATH.name}", return_data=True
            )
            assert (
                SETTINGS_PATH.read_bytes() == Path(tmp.file.name).read_bytes()
            ), "Local file is different from the received one"
            assert (
                SETTINGS_PATH.read_bytes() == data
            ), "Local file is different from the received one (return_data=True)"
            assert data == Path(tmp.file.name).read_bytes(), "Received files do not match"
        # assert labels
        assert c_host._cont_inst.labels.get("broker.l1") == "v1"
        assert c_host._cont_inst.labels.get("broker.l2") == ""
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
            c_host.session.sftp_write(str(SETTINGS_PATH), f"{remote_dir}/")
            res = c_host.execute(f"ls {remote_dir}")
            assert SETTINGS_PATH.name in res.stdout
            with NamedTemporaryFile() as tmp:
                c_host.session.sftp_read(f"{remote_dir}/{SETTINGS_PATH.name}", tmp.file.name)
                data = c_host.session.sftp_read(
                    f"{remote_dir}/{SETTINGS_PATH.name}", return_data=True
                )
                assert (
                    SETTINGS_PATH.read_bytes() == Path(tmp.file.name).read_bytes()
                ), "Local file is different from the received one"
                assert (
                    SETTINGS_PATH.read_bytes() == data
                ), "Local file is different from the received one (return_data=True)"
                assert data == Path(tmp.file.name).read_bytes(), "Received files do not match"


def test_broker_multi_manager():
    with Broker.multi_manager(
        ubi7={"container_host": "localhost/ubi7:latest"},
        ubi8={"container_host": "localhost/ubi8:latest", "_count": 2},
        ubi9={"container_host": "localhost/ubi9:latest"},
    ) as multi_hosts:
        assert "ubi7" in multi_hosts
        assert "ubi8" in multi_hosts
        assert "ubi9" in multi_hosts
        assert len(multi_hosts["ubi8"]) == 2
        assert multi_hosts["ubi7"][0]._cont_inst.top()["Processes"]
        assert (
            multi_hosts["ubi8"][1].execute("hostname").stdout.strip()
            == multi_hosts["ubi8"][1].hostname
        )


def test_custom_hostname():
    with Broker(container_host="ubi8:latest", hostname="my.custom.hostname") as chost:
        assert chost.hostname == "my.custom.hostname"
        assert chost.execute("hostname").strip() == "my.custom.hostname"
