"""Test file for broker.config_manager module."""

import pytest

from ruamel.yaml import YAML
from broker.config_manager import ConfigManager, GH_CFG


yaml = YAML()


def test_basic_assertions(broker_settings_path):
    """Test ConfigManager class initialization and basic attributes."""
    TEST_CFG_DATA = yaml.load(broker_settings_path)
    cfg_mgr = ConfigManager(broker_settings_path)
    assert isinstance(cfg_mgr._cfg, dict)
    assert cfg_mgr._cfg == TEST_CFG_DATA
    assert cfg_mgr.interactive_mode is False


def test_import_config():
    """ConfigManager should be able to download examples settings."""
    cfg_mgr = ConfigManager()
    result = cfg_mgr._import_config(GH_CFG, is_url=True)
    assert isinstance(result, str)
    converted = yaml.load(result)
    assert isinstance(converted, dict)


def test_get_e2e(broker_settings_path):
    """We should be able to get config chunks."""
    TEST_CFG_DATA = yaml.load(broker_settings_path)
    cfg_mgr = ConfigManager(broker_settings_path)
    whole_cfg = cfg_mgr.get()
    assert isinstance(whole_cfg, dict)
    assert whole_cfg == TEST_CFG_DATA
    # get a speficic chunk
    logging_cfg = cfg_mgr.get("logging")
    assert logging_cfg == TEST_CFG_DATA["logging"]
    # get a nested chunk
    test_nick = cfg_mgr.get("nicks.test_nick")
    assert test_nick == TEST_CFG_DATA["nicks"]["test_nick"]


def test_update_e2e(broker_settings_path):
    """We should be able to update config chunks."""
    TEST_CFG_DATA = yaml.load(broker_settings_path)
    cfg_mgr = ConfigManager(broker_settings_path)
    # change logging level
    cfg_mgr.update("logging.console_level", "debug")
    assert cfg_mgr.get("logging.console_level") == "debug"
    # ensure a backup was created
    assert broker_settings_path.with_suffix(".bak").exists()
    # restore original config and make sure the value is reverted
    cfg_mgr.restore()
    # load a new instance of ConfigManager to ensure the change was reverted
    cfg_mgr = ConfigManager(broker_settings_path)
    assert cfg_mgr.get("logging.console_level") == TEST_CFG_DATA["logging"]["console_level"]


def test_nicks(broker_settings_path):
    """Specifically test the nick functionality."""
    TEST_CFG_DATA = yaml.load(broker_settings_path)
    cfg_mgr = ConfigManager(broker_settings_path)
    nick_list = cfg_mgr.nicks()
    assert "test_nick" in nick_list
    test_nick = cfg_mgr.nicks("test_nick")
    assert test_nick == TEST_CFG_DATA["nicks"]["test_nick"]


def test_init_config_file_with_interrupt_resume(broker_settings_path, monkeypatch):
    """Test that init_config_file retries prompt when InterruptResumeError is raised."""
    from broker import exceptions

    # Create a config manager with existing settings file
    cfg_mgr = ConfigManager(broker_settings_path)
    cfg_mgr.interactive_mode = True

    # Track how many times the prompt is called
    call_count = 0

    def mock_prompt(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: simulate keyboard interrupt with resume
            raise exceptions.InterruptResumeError()
        else:
            # Second call: user chooses not to overwrite
            return "n"

    # Patch click.prompt to use our mock
    monkeypatch.setattr("click.prompt", mock_prompt)

    # Call init_config_file - should retry after InterruptResumeError
    cfg_mgr.init_config_file()

    # Verify the prompt was called twice (once failed, once succeeded)
    assert call_count == 2


@pytest.mark.parametrize("extension", ["yaml", "yml"])
def test_directory_nicks(tmp_path, monkeypatch, extension):
    """CLI lookup and Broker resolution share directory nicks and conflict rules."""
    from broker import exceptions, helpers, settings

    monkeypatch.setattr(settings, "BROKER_DIRECTORY", tmp_path)
    config = tmp_path / "broker_settings.yaml"
    config.write_text("nicks:\n  shared:\n    workflow: main\n")
    directory = tmp_path / "conf" / "nicks"
    directory.mkdir(parents=True)
    (directory / f"first.{extension}").write_text(
        "unique:\n  workflow: unique\nshared:\n  workflow: first\n"
        "duplicate:\n  workflow: first\n"
    )
    nested = directory / "nested"
    nested.mkdir()
    (nested / "first.yaml").write_text("duplicate:\n  workflow: nested\n")
    (directory / ".hidden.yaml").write_text("hidden:\n  workflow: hidden\n")
    manager = ConfigManager(config)
    assert set(manager.nicks()) == {
        "unique", "shared", "first/shared", "first/duplicate", "nested/first/duplicate"
    }
    assert manager.nicks("shared") == {"workflow": "main"}
    assert manager.nicks("first/shared") == {"workflow": "first"}
    custom = settings.create_settings(
        config_dict={"NICKS": {"shared": {"workflow": "custom"}}},
        config_file=config,
        skip_validation=True,
    )
    assert helpers.resolve_nick("shared", custom) == {"workflow": "custom"}
    assert helpers.resolve_nick("unique", custom) == {"workflow": "unique"}
    assert helpers.resolve_nick("nested/first/duplicate", custom) == {"workflow": "nested"}
    with pytest.raises(exceptions.UserError, match="Unknown nick: missing"):
        manager.nicks("missing")


def test_missing_nicks_directory(tmp_path, monkeypatch):
    """A configuration without nicknames works without creating directories."""
    from broker import settings

    monkeypatch.setattr(settings, "BROKER_DIRECTORY", tmp_path)
    config = tmp_path / "broker_settings.yaml"
    config.write_text("{}\n")
    assert ConfigManager(config).nicks() == []
    assert not (tmp_path / "conf").exists()
