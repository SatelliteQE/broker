import inspect
from broker.settings import settings
from logzero import logger

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
    def __init__(self, **kwargs):
        super().__init__()
        self.config = settings.TESTPROVIDER.config_value

        # self.__dict__.update(kwargs)

    def _host_release(self):
        caller_host = inspect.stack()[1][0].f_locals["host"]
        self.release(caller_host)

    def nick_help(self, **kwargs):
        pass

    def construct_host(self, provider_params, host_classes, **kwargs):
        return super().construct_host(provider_params, host_classes, **kwargs)

    def test_action(self, **kwargs):
        action = kwargs.get("test_action")
        if action == "release":
            return "released", kwargs
        if action in HOST_PROPERTIES:
            return HOST_PROPERTIES
        return HOST_PROPERTIES["basic"]

    def release(self, host_obj):
        return self.test_action(test_action="release", **host_obj.to_dict())
