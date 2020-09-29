import inspect
from abc import ABC, abstractmethod


class Provider:
    def __init__(self):
        self._construct_params = []

    def _host_release(self):
        raise NotImplementedError("_host_release has not been implemented")

    def _set_attributes(self, host_inst, broker_args=None):
        host_inst.__dict__.update(
            {
                "release": self._host_release,
                "_test_inst": self,
                "_broker_provider": "TestProvider",
                "_broker_args": broker_args,
            }
        )

    @abstractmethod
    def construct_host(self, provider_params, host_classes, **kwargs):
        host_params = provider_params.copy()
        host_params.update(kwargs)
        host_inst = host_classes[host_params["host_type"]](**host_params)
        self._set_attributes(host_inst, broker_args=kwargs)
        return host_inst

        host_inst = host_cls(**provider_params, **kwargs)
        host_attrs = self._get_params(self._construct_params)
        host_attrs["release"] = self._host_release
        self._set_attributes(host_inst, host_attrs)
        return host_inst

    def nick_help(self, **kwargs):
        raise NotImplementedError("nick_help has not been implemented")

    def get_inventory(self, **kwargs):
        raise NotImplementedError("get_inventory has not been implemented")

    def extend(self):
        raise NotImplementedError("extend has not been implemented")

    def release(self, host_obj):
        raise NotImplementedError("release has not been implemented")
