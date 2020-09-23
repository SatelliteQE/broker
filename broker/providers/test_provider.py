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
        # self.config = settings.TESTPROVIDER

        # Enabling the line above leads to problems to pickle in
        # tests/test_broker.py::test_mp_checkout
        # that resulted in deadlock. This was quite hard to find problem as the tracebacks and
        # errors I was getting were not helpful:
        # Traceback (most recent call last):
        #   File "/usr/lib64/python3.9/multiprocessing/queues.py", line 245, in _feed
        #     obj = _ForkingPickler.dumps(obj)
        #   File "/usr/lib64/python3.9/multiprocessing/reduction.py", line 51, in dumps
        #     cls(buf, protocol).dump(obj)
        # TypeError: cannot pickle 'module' object
        #
        # The root cause was tracked down with help of
        # https://stackoverflow.com/a/59832602/1950100


        # self.__dict__.update(kwargs)
        pass

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
