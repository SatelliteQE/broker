import pickle
import dynaconf

from broker import exceptions
from broker.helpers import PickleSafe
from broker.settings import settings
from logzero import logger


class Provider(PickleSafe):
    # Populate with a list of Dynaconf Validators specific to your provider
    _validators = []
    # Set to true if you don't want your provider shown in the CLI
    hidden = False
    # Populate these to add your checkout and execute options to each command
    # _checkout_options = [click.option("--workflow", type=str, help="Help text")]
    _checkout_options = []
    _execute_options = []
    _fresh_settings = settings.dynaconf_clone()
    _sensitive_attrs = []

    def __init__(self, **kwargs):
        self._construct_params = []
        cls_name = self.__class__.__name__
        logger.debug(f"{cls_name} provider instantiated with {kwargs=}")
        instance_name = kwargs.pop(f"{cls_name}", None)
        self._validate_settings(instance_name)

    def _validate_settings(self, instance_name=None):
        """Load and validate provider settings

        Each provider's settings can include an instances list with specific instance
        details.
        One instance should have a "default" key set to True, if instances are defined.
        General provider settings should live on the top level for that provider.

        :param instance_name: A string matching an instance name
        """
        section_name = self.__class__.__name__.upper()
        # if the provider has instances, load the instance settings
        if self._fresh_settings.get(section_name).get("instances"):
            fresh_settings = self._fresh_settings.get(section_name).copy()
            instance_name = instance_name or getattr(self, "instance", None)
            # iterate through the instances and find the one that matches the instance_name
            # if no instance matches, use the default instance
            for candidate in fresh_settings.instances:
                logger.debug("Checking %s against %s", instance_name, candidate)
                if instance_name in candidate:
                    instance = candidate
                    break
                elif (
                    candidate.values()[0].get("default")
                    or len(fresh_settings.instances) == 1
                ):
                    instance = candidate
            self.instance, *_ = instance  # store the instance name on the provider
            fresh_settings.update((inst_vals := instance.values()[0]))
            settings[section_name] = fresh_settings
            if not inst_vals.get("override_envars"):
                # if a provider instance doesn't want to override envars, load them
                settings.execute_loaders(loaders=[dynaconf.loaders.env_loader])

        settings.validators.extend([v for v in self._validators if v not in settings.validators])
        # use selective validation to only validate the instance settings
        try:
            settings.validators.validate(only=section_name)
        except dynaconf.ValidationError as err:
            raise exceptions.ConfigurationError(err)

    def _host_release(self):
        raise exceptions.NotImplementedError("_host_release has not been implemented")

    def _set_attributes(self, obj, attrs):
        obj.__dict__.update(attrs)

    def _get_params(arg_list, kwargs):
        return {k: v for k, v in kwargs.items() if k in arg_list}

    def construct_host(self, host_cls, provider_params, **kwargs):
        host_inst = host_cls(**provider_params, **kwargs)
        host_attrs = self._get_params(self._construct_params)
        host_attrs["release"] = self._host_release
        self._set_attributes(host_inst, host_attrs)
        return host_inst

    def nick_help(self):
        raise exceptions.NotImplementedError("nick_help has not been implemented")

    def get_inventory(self, **kwargs):
        raise exceptions.NotImplementedError("get_inventory has not been implemented")

    def extend(self):
        raise exceptions.NotImplementedError("extend has not been implemented")

    def release(self, host_obj):
        raise exceptions.NotImplementedError("release has not been implemented")

    def __repr__(self):
        inner = ", ".join(
            f"{k}={'******' if k in self._sensitive_attrs and v else v}"
            for k, v in self.__dict__.items()
            if not k.startswith("_") and not callable(v)
        )
        return f"{self.__class__.__name__}({inner})"

    def __getstate__(self):
        """If a session is active, remove it for pickle compatability"""
        self._purify()
        return self.__dict__

    def _purify(self):
        """Strip all unpickleable attributes from a Host before pickling"""
        for key, obj in self.__dict__.items():
            try:
                pickle.dumps(obj)
            except (pickle.PicklingError, AttributeError):
                self.__dict__[key] = None
            except RecursionError:
                logger.warning(f"Recursion limit reached on {obj=}")
