from abc import ABCMeta, abstractmethod
import dynaconf
from pathlib import Path

from broker import exceptions
from broker.settings import settings
from logzero import logger


# populate a list of all provider module names
_provider_imports = [
    f.stem
    for f in Path(__file__).parent.glob("*.py")
    if f.is_file() and f.stem != "__init__"
]

# ProviderName: ProviderClassObject
PROVIDERS = {}
# action: (InterfaceClass, "method_name")
PROVIDER_ACTIONS = {}


class ProviderMeta(ABCMeta):
    """Metaclass that registers provider classes and actions"""

    def __new__(cls, name, bases, attrs):
        """Register provider classes and actions"""
        new_cls = super().__new__(cls, name, bases, attrs)
        if name != "Provider":
            PROVIDERS[name] = new_cls
            logger.debug(f"Registered provider {name}")
            for attr in attrs.values():
                if hasattr(attr, "_as_action"):
                    for action in attr._as_action:
                        PROVIDER_ACTIONS[action] = (new_cls, attr.__name__)
                        logger.debug(f"Registered action {action} for provider {name}")
        return new_cls


class Provider(metaclass=ProviderMeta):
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
        self.instance = kwargs.pop(f"{cls_name}", None)
        self._validate_settings(self.instance)

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

    @abstractmethod
    def nick_help(self):
        pass

    @abstractmethod
    def get_inventory(self, **kwargs):
        pass

    @abstractmethod
    def extend(self):
        pass

    @abstractmethod
    def release(self, host_obj):
        pass

    def __repr__(self):
        inner = ", ".join(
            f"{k}={'******' if k in self._sensitive_attrs and v else v}"
            for k, v in self.__dict__.items()
            if not k.startswith("_") and not callable(v)
        )
        return f"{self.__class__.__name__}({inner})"

    @staticmethod
    def register_action(*as_names):
        """Decorator to register a provider action

        :param as_names: One or more action names to register the decorated function as
        """

        def decorator(func):
            func._as_action = as_names or [func.__name__]
            return func

        return decorator
