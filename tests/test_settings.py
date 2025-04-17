import os
import sys
import pytest
from dynaconf import ValidationError
from broker.exceptions import ConfigurationError
from broker.providers.test_provider import TestProvider


def test_default_settings(broker_settings):
    test_provider = TestProvider(broker_settings=broker_settings)
    assert test_provider.instance == "test1"
    assert test_provider.foo == "bar"


def test_alternate_settings(broker_settings):
    test_provider = TestProvider(TestProvider="test2", broker_settings=broker_settings)
    assert test_provider.instance == "test2"
    assert test_provider.foo == "baz"


def test_validator_trigger(broker_settings):
    with pytest.raises(ConfigurationError) as err:
        TestProvider(TestProvider="bad", broker_settings=broker_settings)
    assert isinstance(err.value.args[0], ValidationError)


@pytest.mark.parametrize(
    "set_envars", [[("BROKER_TESTPROVIDER__INSTANCES__TEST2__foo", "bar")]], indirect=True
)
def test_nested_envar(set_envars, broker_settings):
    """Set a value nested under an instance via environment variable
    then verify that the value makes it to the correct level.
    """
    test_provider = TestProvider(TestProvider="test2", broker_settings=broker_settings)
    assert test_provider.instance == "test2"
    assert test_provider.foo == "baz"


@pytest.mark.parametrize("set_envars", [[("BROKER_TESTPROVIDER__foo", "envar")]], indirect=True)
def test_default_envar(set_envars, broker_settings):
    """Set a top-level instance value via environment variable
    then verify that the value is not overriden when the provider is selected by default.
    """
    test_provider = TestProvider(broker_settings=broker_settings)
    assert test_provider.instance == "test1"
    assert test_provider.foo == "envar"


@pytest.mark.parametrize(
    "set_envars", [[("BROKER_TESTPROVIDER__foo", "override me")]], indirect=True
)
def test_nondefault_envar(set_envars, broker_settings):
    """Set a top-level instance value via environment variable
    then verify that the value has been overriden when the provider is specified.
    """
    test_provider = TestProvider(TestProvider="test2", broker_settings=broker_settings)
    assert test_provider.instance == "test2"
    assert test_provider.foo == "baz"


@pytest.mark.parametrize("set_envars", [[("VAULT_ENABLED_FOR_DYNACONF", "1")]], indirect=True)
def test_purge_vault_envars(set_envars, broker_settings_path):
    """Set dynaconf vault envars and verify that they have no effect"""
    sys.modules.pop("broker.settings")
    from broker.settings import create_settings

    # Create settings with test path, no migrations
    test_settings = create_settings(config_file=broker_settings_path, perform_migrations=False)

    assert not test_settings.VAULT_ENABLED_FOR_DYNACONF
    assert os.environ["VAULT_ENABLED_FOR_DYNACONF"] == "1"
