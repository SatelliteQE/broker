"""Test file for broker.config_manager module."""

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
