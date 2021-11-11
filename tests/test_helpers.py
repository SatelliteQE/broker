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
