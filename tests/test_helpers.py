from pathlib import Path
import json
import pytest
from broker import helpers
from broker import exceptions

BROKER_ARGS_DATA = {
    "myarg": [
        {"baseurl": "http://my.mirror/rpm", "name": "base"},
        {"baseurl": "http://my.mirror/rpm", "name": "optional"},
    ],
    "my_second_arg": "foo",
}


@pytest.fixture
def tmp_file(tmp_path):
    return tmp_path / "test.json"


@pytest.fixture
def basic_origin():
    return helpers.find_origin()


@pytest.fixture
def request_origin(request):
    return helpers.find_origin()


def test_load_json_file():
    data = helpers.load_file("tests/data/broker_args.json")
    assert data == BROKER_ARGS_DATA


def test_load_yaml_file():
    data = helpers.load_file("tests/data/broker_args.yaml")
    assert data == BROKER_ARGS_DATA


def test_negative_load_file():
    data = helpers.load_file("this/doesnt/exist.something")
    assert not data


def test_resolve_file_args():
    broker_args = {
        "test_arg": "test_val",
        "args_file": "tests/data/broker_args.json",
        "complex_args": "tests/data/args_file.yaml",
    }
    new_args = helpers.resolve_file_args(broker_args)
    assert "args_file" not in new_args
    assert new_args["my_second_arg"] == "foo"
    assert new_args["complex_args"] == BROKER_ARGS_DATA["myarg"]
    assert new_args["test_arg"] == "test_val"


def test_emitter(tmp_file):
    assert not tmp_file.exists()
    helpers.emit.set_file(tmp_file)
    assert tmp_file.exists()
    helpers.emit(test="value", another=5)
    written = json.loads(tmp_file.read_text())
    assert written == {"test": "value", "another": 5}
    helpers.emit({"thing": 13})
    written = json.loads(tmp_file.read_text())
    assert written == {"test": "value", "another": 5, "thing": 13}


def test_lock_file_created(tmp_file):
    with helpers.FileLock(tmp_file) as tf:
        assert isinstance(tf, Path)
        assert Path(f"{tf}.lock").exists()


def test_lock_timeout(tmp_file):
    tmp_lock = Path(f"{tmp_file}.lock")
    tmp_lock.touch()
    with pytest.raises(exceptions.BrokerError) as exc:
        with helpers.FileLock(tmp_file, timeout=1):
            pass
    assert str(exc.value).startswith("Timeout while attempting to open")


def test_find_origin_simple():
    origin = helpers.find_origin()
    assert len(origin) == 2
    assert origin[0].startswith("test_find_origin")
    assert origin[1] is None


def test_find_origin_fixture_basic(basic_origin):
    assert basic_origin[0].startswith("basic_origin")


def test_find_origin_fixture(request_origin):
    """Test that we can get the request object information from the fixture"""
    assert "test_find_origin_fixture" in request_origin[0]


@pytest.mark.parametrize("set_envars", [("BUILD_URL", "fake")], indirect=True)
def test_find_origin_jenkins(set_envars):
    origin = helpers.find_origin()
    assert len(origin) == 2
    assert origin[1] == "fake"


def test_flatten_duplicate():
    data = {"rhel_compose_repositories": [{"name": "baseos"}, {"name": "appstream"}]}
    result = helpers.flatten_dict(data)
    assert len(result) == 2
