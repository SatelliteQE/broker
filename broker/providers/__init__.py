import inspect

from broker.settings import settings


class Provider:
    # Populate with a list of Dynaconf Validators specific to your provider
    _validators = []
    # Set to true if you don't want your provider shown in the CLI
    hidden = False

    def __init__(self):
        self._construct_params = []

    def _validate_settings(self, instance_name=None):
        """Load and validate provider settings

        Each provider's settings must include an instances list with specific instance
        details.
        General provider settings should live on the top level for that provider.
        One instance should have a "default" key set to True

        :param instance_name: A string matching an instance name
        """
        section_name = self.__class__.__name__.upper()
        # make sure each instance isn't loading values from another
        fresh_settings = settings.get_fresh(section_name)
        instance, default = None, False
        for candidate in fresh_settings.instances:
            if instance_name in candidate:
                instance = candidate
                default = False
            elif candidate.values()[0].get("default"):
                instance = candidate
                default = True
        fresh_settings.update(instance.values()[0])
        if default:
            # if a default provider is selected, defer to loaded environment variables
            # settings[section_name] = fresh_settings
            # settings.execute_loaders(loaders=[dynaconf.loaders.env_loader])
            # ideal solution above. However, need to workaround until
            # https://github.com/rochacbruno/dynaconf/issues/511
            settings.execute_loaders()
            for key in fresh_settings.keys():
                if key in settings[section_name]:
                    fresh_settings[key] = settings[section_name][key]
            settings[section_name] = fresh_settings
        else:
            settings[section_name] = fresh_settings

        # temporary workaround for https://github.com/rochacbruno/dynaconf/issues/508
        # remove the current valiators, add ours, and validate
        # then add the other validators back in and move on
        current_validators = settings.validators[:]
        settings.validators.clear()
        settings.validators.extend(self._validators)
        settings.validators.validate()
        settings.validators.extend(current_validators)

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
