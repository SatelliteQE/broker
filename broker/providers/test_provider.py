import inspect
from dynaconf import Validator
from broker.settings import settings
from broker.providers import Provider


HOST_PROPERTIES = {
    "basic": {
        "hostname": "test.host.example.com",
        "OS": "FakeOS",
        "extra": True,
        "host_type": "host",
    }
}


class TestProvider(Provider):
    __test__ = False  # don't use for testing
    hidden = True  # hide from click command generation
    _validators = [Validator("TestProvider.foo", must_exist=True)]

    def __init__(self, **kwargs):
        self.instance_name = kwargs.pop("TestProvider", "default")
        self._validate_settings(self.instance_name)
        self.config = settings.TESTPROVIDER.config_value
        self.foo = settings.TESTPROVIDER.foo

    def _host_release(self):
        caller_host = inspect.stack()[1][0].f_locals["host"]
        self.release(caller_host)

    def _set_attributes(self, host_inst, broker_args=None):
        host_inst.__dict__.update(
            {
                "release": self._host_release,
                "_test_inst": self,
                "_broker_provider": "TestProvider",
                "_broker_args": broker_args,
            }
        )

    def construct_host(self, provider_params, host_classes, **kwargs):
        host_params = provider_params.copy()
        host_params.update(kwargs)
        host_inst = host_classes[host_params["host_type"]](**host_params)
        self._set_attributes(host_inst, broker_args=kwargs)
        return host_inst

    def test_action(self, **kwargs):
        action = kwargs.get("test_action")
        if action == "release":
            return "released", kwargs
        if action in HOST_PROPERTIES:
            return HOST_PROPERTIES
        return HOST_PROPERTIES["basic"]

    def release(self, host_obj):
        return self.test_action(test_action="release", **host_obj.to_dict())
