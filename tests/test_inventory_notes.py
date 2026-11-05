from click.testing import CliRunner

from broker import helpers
from broker.commands import cli


def test_inventory_note_sets_and_clears_note(tmp_path, monkeypatch):
    inventory_path = tmp_path / "inventory.yaml"
    original = {
        "hostname": "host.example.com",
        "name": "host",
        "_broker_provider": "TestProvider",
        "_broker_args": {"workflow": "deploy", "other": "preserve"},
    }
    helpers.yaml.dump([original], inventory_path)
    monkeypatch.setattr("broker.settings.inventory_path", inventory_path)

    result = CliRunner().invoke(cli, ["inventory-note", "host.example.com", "ADHOC"])
    assert result.exit_code == 0, result.output
    updated = helpers.load_file(inventory_path)[0]
    assert updated["_broker_args"]["description"] == "ADHOC"
    assert updated["_broker_args"]["other"] == "preserve"

    result = CliRunner().invoke(cli, ["inventory-note", "host.example.com", "--clear"])
    assert result.exit_code == 0, result.output
    assert "description" not in helpers.load_file(inventory_path)[0]["_broker_args"]


def test_inventory_note_accepts_local_id(tmp_path, monkeypatch):
    inventory_path = tmp_path / "inventory.yaml"
    helpers.yaml.dump([{"hostname": "host.example.com", "_broker_args": {}}], inventory_path)
    monkeypatch.setattr("broker.settings.inventory_path", inventory_path)

    result = CliRunner().invoke(cli, ["inventory-note", "0", "ADHOC"])
    assert result.exit_code == 0, result.output
    assert helpers.load_file(inventory_path)[0]["_broker_args"]["description"] == "ADHOC"


def test_inventory_note_appends(tmp_path, monkeypatch):
    inventory_path = tmp_path / "inventory.yaml"
    helpers.yaml.dump(
        [{"hostname": "host.example.com", "_broker_args": {"description": "first"}}],
        inventory_path,
    )
    monkeypatch.setattr("broker.settings.inventory_path", inventory_path)

    result = CliRunner().invoke(cli, ["inventory-note", "0", "--append", "second"])
    assert result.exit_code == 0, result.output
    assert helpers.load_file(inventory_path)[0]["_broker_args"]["description"] == "first\nsecond"


def test_inventory_note_updates_multiple_hosts(tmp_path, monkeypatch):
    inventory_path = tmp_path / "inventory.yaml"
    helpers.yaml.dump(
        [
            {"hostname": "host1.example.com", "_broker_args": {}},
            {"hostname": "host2.example.com", "_broker_args": {}},
            {"hostname": "host3.example.com", "_broker_args": {}},
        ],
        inventory_path,
    )
    monkeypatch.setattr("broker.settings.inventory_path", inventory_path)

    result = CliRunner().invoke(
        cli, ["inventory-note", "0, host2.example.com,2", "ADHOC"]
    )
    assert result.exit_code == 0, result.output
    inventory = helpers.load_file(inventory_path)
    assert [entry["_broker_args"]["description"] for entry in inventory] == [
        "ADHOC",
        "ADHOC",
        "ADHOC",
    ]


def test_inventory_fields_include_description_by_default():
    from broker import settings

    configured = settings.create_settings(skip_validation=True)
    assert configured.inventory_fields["Notes"] == "_broker_args.description"


def test_inventory_fields_do_not_modify_existing_configuration():
    from broker import settings

    custom_fields = {"OnlyHost": "name", "Custom": "value"}
    configured = settings.create_settings(
        config_dict={"inventory_fields": custom_fields},
        skip_validation=True,
    )
    assert configured.inventory_fields["OnlyHost"] == "name"
    assert configured.inventory_fields["Custom"] == "value"
