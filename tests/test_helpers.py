import json
from broker import helpers

BROKER_ARGS_DATA = {
    "myarg": [
        {"baseurl": "http://my.mirror/rpm", "name": "base"},
        {"baseurl": "http://my.mirror/rpm", "name": "optional"},
    ],
    "my_second_arg": "foo",
}


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
        "complex_args": "tests/data/args_file.yaml"
    }
    new_args = helpers.resolve_file_args(broker_args)
    assert "args_file" not in new_args
    assert new_args["my_second_arg"] == "foo"
    assert new_args["complex_args"] == BROKER_ARGS_DATA["myarg"]
    assert new_args["test_arg"] == "test_val"


def test_emitter(tmp_path):
    out_file = tmp_path / "output.json"
    assert not out_file.exists()
    helpers.emit.set_file(out_file)
    assert out_file.exists()
    helpers.emit(test="value", another=5)
    written = json.loads(out_file.read_text())
    assert written == {"test": "value", "another": 5}
    helpers.emit({"thing": 13})
    written = json.loads(out_file.read_text())
    assert written == {"test": "value", "another": 5, "thing": 13}
