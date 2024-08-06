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


@pytest.fixture(scope="module")
def fake_inventory():
    return helpers.load_file("tests/data/fake_inventory.yaml")


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
    lock_file = helpers.FileLock(tmp_file)
    with lock_file:
        assert isinstance(lock_file.lock, Path)
        assert lock_file.lock.exists()
    assert not lock_file.lock.exists()


def test_lock_timeout(tmp_file):
    tmp_lock = Path(f"{tmp_file}.lock")
    tmp_lock.touch()
    with pytest.raises(exceptions.BrokerError) as exc:
        with helpers.FileLock(tmp_file, timeout=1):
            pass
    assert str(exc.value).startswith("Timeout while waiting for lock release: ")


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


def test_eval_filter_list_copy(fake_inventory):
    """Test that a python list copy operation returns all entries in the inventory"""
    filtered = helpers.eval_filter(fake_inventory, "@inv[:]")
    assert len(filtered) == 10


def test_eval_filter_list_last(fake_inventory):
    """Test that a neative list index returns the last host in the inventory"""
    filtered = helpers.eval_filter(fake_inventory, "@inv[-1]")
    assert len(filtered) == 1
    assert filtered[0]["hostname"] == "dhcp-369.test.example.com"


def test_eval_filter_list_slice(fake_inventory):
    """Test that a list slice returns the correct number of hosts"""
    filtered = helpers.eval_filter(fake_inventory, "@inv[1:3]")
    assert len(filtered) == 2
    assert filtered[0]["hostname"] == "dhcp-121.test.example.com"
    assert filtered[1]["hostname"] == "dhcp-113.test.example.com"


def test_eval_filter_attribute(fake_inventory):
    """Test that a filter can access an attribute from an inventory host"""
    filtered = helpers.eval_filter(fake_inventory, "'rhel8.7' in @inv.name")
    assert len(filtered) == 4


def test_eval_filter_chain(fake_inventory):
    """Test that a user can chain multiple filters together"""
    filtered = helpers.eval_filter(fake_inventory, "@inv[:3] | 'sat-jenkins' in @inv.name")
    assert len(filtered) == 1


def test_dict_from_paths_nested():
    source_dict = {
        "person": {
            "name": "John",
            "age": 30,
            "address": {"street": "123 Main St", "city": "Anytown", "state": "CA", "zip": "12345"},
        }
    }
    paths = {"person_name": "person/name", "person_zip": "person/address/zip"}
    result = helpers.dict_from_paths(source_dict, paths)
    assert result == {"person_name": "John", "person_zip": "12345"}


def test_kwargs_from_click_ctx():
    """Test that we can extract kwargs from a mixed-style click context object"""
    class ctx:
        args = ["--arg1", "value1", "--arg2=value2", "--some-flag"]

    kwargs = helpers.kwargs_from_click_ctx(ctx)
    assert kwargs == {"arg1": "value1", "arg2": "value2"}
