import inspect
from broker import helpers
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
    _validators = [Validator("TESTPROVIDER.foo", must_exist=True)]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.config = settings.TESTPROVIDER.config_value
        self.foo = settings.TESTPROVIDER.foo

    def _host_release(self):
        caller_host = inspect.stack()[1][0].f_locals["host"]
        self.release(caller_host)

    def _set_attributes(self, host_inst, broker_args=None):
        host_inst.__dict__.update(
            {
                "release": self._host_release,
                "_prov_inst": self,
                "_broker_provider": "TestProvider",
                "_broker_provider_instance": self.instance,
                "_broker_args": broker_args,
            }
        )

    def construct_host(self, provider_params, host_classes, **kwargs):
        if provider_params:
            host_params = provider_params.copy()
            host_params.update(kwargs)
            host_inst = host_classes[host_params["host_type"]](**host_params)
        else:  # if we are reconstructing the host from the inventory
            host_inst = host_classes[kwargs.get("type", "host")](**kwargs)
        self._set_attributes(host_inst, broker_args=kwargs)
        return host_inst

    @Provider.register_action()
    def test_action(self, **kwargs):
        action = kwargs.get("test_action")
        if action == "release":
            return "released", kwargs
        if action in HOST_PROPERTIES:
            return HOST_PROPERTIES
        return HOST_PROPERTIES["basic"]

    def release(self, host_obj):
        return self.test_action(test_action="release", **host_obj.to_dict())

    def extend(self):
        pass

    def get_inventory(self, *args, **kwargs):
        return helpers.load_inventory(
            filter=f'@inv._broker_provider == "{self.__class__.__name__}"'
        )

    def provider_help(self):
        pass
