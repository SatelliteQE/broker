import os
import pytest
from dynaconf import ValidationError
from broker.exceptions import ConfigurationError
from broker.providers.test_provider import TestProvider


def test_default_settings():
    test_provider = TestProvider()
    assert test_provider.instance_name == "default"
    assert test_provider.foo == "bar"


def test_alternate_settings():
    test_provider = TestProvider(TestProvider="test2")
    assert test_provider.instance_name == "test2"
    assert test_provider.foo == "baz"


def test_validator_trigger():
    with pytest.raises(ConfigurationError) as err:
        TestProvider(TestProvider="bad")
    assert isinstance(err.value.args[0], ValidationError)


def test_nested_envar():
    """Set a value nested under an instance via environment variable
    then verify that the value makes it to the correct level.
    """
    os.environ["BROKER_TESTPROVIDER__INSTANCES__TEST2__foo"] = "bar"
    test_provider = TestProvider(TestProvider="test2")
    assert test_provider.instance_name == "test2"
    assert test_provider.foo == "baz"
    del os.environ["BROKER_TESTPROVIDER__INSTANCES__TEST2__foo"]


def test_default_envar():
    """Set a top-level instance value via environment variable
    then verify that the value is not overriden when the provider is selected by default.
    """
    os.environ["BROKER_TESTPROVIDER__foo"] = "envar"
    test_provider = TestProvider()
    assert test_provider.instance_name == "default"
    assert test_provider.foo == "envar"
    del os.environ["BROKER_TESTPROVIDER__foo"]


def test_nondefault_envar():
    """Set a top-level instance value via environment variable
    then verify that the value has been overriden when the provider is specified.
    """
    os.environ["BROKER_TESTPROVIDER__foo"] = "override me"
    test_provider = TestProvider(TestProvider="test1")
    assert test_provider.instance_name == "test1"
    assert test_provider.foo == "bar"
    del os.environ["BROKER_TESTPROVIDER__foo"]
