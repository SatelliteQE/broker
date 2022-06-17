import os
import pytest
from dynaconf import ValidationError
from broker.exceptions import ConfigurationError
from broker.providers.test_provider import TestProvider


def test_default_settings():
    test_provider = TestProvider()
    assert test_provider.instance_name == "default"
    assert test_provider.foo == "bar"
    assert test_provider.config == "something"


def test_alternate_settings():
    test_provider = TestProvider(TestProvider="test2")
    assert test_provider.instance_name == "test2"
    assert test_provider.foo == "baz"


def test_validator_trigger():
    with pytest.raises(ConfigurationError) as err:
        TestProvider(TestProvider="bad")
    assert isinstance(err.value.args[0], ValidationError)


def test_nested_envar(request):
    """Set a value nested under an instance via environment variable
    then verify that the value makes it to the correct level.
    """
    os.environ["BROKER_TESTPROVIDER__INSTANCES__TEST2__foo"] = "bar"
    @request.addfinalizer
    def _clean():
        del os.environ["BROKER_TESTPROVIDER__INSTANCES__TEST2__foo"]

    test_provider = TestProvider(TestProvider="test2")
    assert test_provider.instance_name == "test2"
    assert test_provider.foo == "baz"


def test_default_envar(request):
    """Set a top-level instance value via environment variable
    then verify that the value is not overriden when the provider is selected by default.
    """
    os.environ["BROKER_TESTPROVIDER__config_value"] = "envar"
    @request.addfinalizer
    def _clean():
        del os.environ["BROKER_TESTPROVIDER__config_value"]
        
    test_provider = TestProvider()
    assert test_provider.instance_name == "default"
    assert test_provider.config == "envar"


def test_nondefault_envar(request):
    """Set a top-level instance value via environment variable
    then verify that the value has been overriden when the provider is specified.
    """
    os.environ["BROKER_TESTPROVIDER__foo"] = "override me"
    @request.addfinalizer
    def _clean():
        del os.environ["BROKER_TESTPROVIDER__foo"]

    test_provider = TestProvider(TestProvider="test1")
    assert test_provider.instance_name == "test1"
    assert test_provider.foo == "bar"
