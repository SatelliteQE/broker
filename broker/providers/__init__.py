import inspect


class Provider:
    hidden = False

    def __init__(self):
        self._construct_params = []

    def _host_release(self):
        raise NotImplementedError("_host_release has not been implemented")

    def _set_attributes(self, obj, attrs):
        obj.__dict__.update(attrs)

    def _get_params(arg_list, kwargs):
        return {k: v for k, v in kwargs.items() if k in arg_list}

    def construct_host(self, host_cls, provider_params, **kwargs):
        hostname = provider_params["hostname"]
        host_inst = host_cls(**provider_params, **kwargs)
        host_attrs = self._get_params(self._construct_params)
        host_attrs["release"] = self._host_release
        self._set_attributes(host_inst, host_attrs)
        return host_inst

    def nick_help(self):
        raise NotImplementedError("nick_help has not been implemented")

    def get_inventory(self, **kwargs):
        raise NotImplementedError("get_inventory has not been implemented")

    def extend(self):
        raise NotImplementedError("extend has not been implemented")

    def release(self, host_obj):
        raise NotImplementedError("release has not been implemented")
