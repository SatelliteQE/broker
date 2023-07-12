"""Module for Broker providers.

This module defines the `Provider` class, which is the base class for all Broker providers.
It provides useful methods for registering provider actions and must be inherited by all
Broker providers.

Attributes:
    PROVIDERS (dict): Dictionary of provider names and classes.
    PROVIDER_ACTIONS (dict): Dictionary of provider actions and their corresponding methods.
    PROVIDER_HELP (dict): Dictionary providing information to construct `broker providers --help`

Classes:
    Provider: Base class for all Broker providers.

Usage:
    To create a new Broker provider, create a new class that inherits from the `Provider` class
    and implements the required methods. For example:

    ```
    from broker.providers import Provider

    class MyProvider(Provider):
        def provider_help(self):
            # implementation here

        def get_inventory(self, **inventory_opts):
            # implementation here
    ```

Note: The `Provider` class should not be used directly.

"""
from abc import ABCMeta, abstractmethod
import inspect
from pathlib import Path

import dynaconf
from logzero import logger

from broker import exceptions
from broker.settings import settings

# populate a list of all provider module names
_provider_imports = [
    f.stem for f in Path(__file__).parent.glob("*.py") if f.is_file() and f.stem != "__init__"
]

# ProviderName: ProviderClassObject
PROVIDERS = {}
# action: (InterfaceClass, "method_name")
PROVIDER_ACTIONS = {}
# action: (InterfaceClass, "method_name")
PROVIDER_HELP = {}


class ProviderMeta(ABCMeta):
    """Metaclass that registers provider classes and actions."""

    def __new__(cls, name, bases, attrs):
        """Register provider classes and actions."""
        new_cls = super().__new__(cls, name, bases, attrs)
        if name != "Provider":
            PROVIDERS[name] = new_cls
            logger.debug(f"Registered provider {name}")
            for attr, obj in attrs.items():
                if attr == "provider_help":
                    # register the help options based on the function arguments
                    for name, param in inspect.signature(obj).parameters.items():
                        if name not in ("self", "kwargs"):
                            # {name: (cls, is_flag)}
                            PROVIDER_HELP[name] = (
                                new_cls,
                                isinstance(param.default, bool),
                            )
                            logger.debug(f"Registered help option {name} for provider {name}")
                elif hasattr(obj, "_as_action"):
                    for action in obj._as_action:
                        PROVIDER_ACTIONS[action] = (new_cls, attr)
                        logger.debug(f"Registered action {action} for provider {name}")
        return new_cls


class Provider(metaclass=ProviderMeta):
    """Abstract base class for all providers.

    This class should be subclassed by all provider implementations. It provides a
    metaclass that registers provider classes and actions.

    Attributes:
        _validators (list): A list of Dynaconf Validators specific to the provider.
        hidden (bool): A flag to hide the provider from the CLI.
        _checkout_options (list): A list of checkout options to add to each command.
        _execute_options (list): A list of execute options to add to each command.
        _fresh_settings (dynaconf.Dynaconf): A clone of the global settings object.
        _sensitive_attrs (list): A list of sensitive attributes that should not be logged.
    """

    # Populate with a list of Dynaconf Validators specific to your provider
    _validators = []
    # Used to hide the provider from the CLI
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
        """Load and validate provider settings.

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
                elif candidate.values()[0].get("default") or len(fresh_settings.instances) == 1:
                    instance = candidate
            self.instance, *_ = instance  # store the instance name on the provider
            fresh_settings.update(inst_vals := instance.values()[0])
            settings[section_name] = fresh_settings
            if not inst_vals.get("override_envars"):
                # if a provider instance doesn't want to override envars, load them
                settings.execute_loaders(loaders=[dynaconf.loaders.env_loader])
        new_validators = [v for v in self._validators if v not in settings.validators]
        logger.debug(f"Adding new validators: {[v.names[0] for v in new_validators]}")
        settings.validators.extend(new_validators)
        # use selective validation to only validate the instance settings
        try:
            settings.validators.validate(only=section_name)
        except dynaconf.ValidationError as err:
            raise exceptions.ConfigurationError(err) from err

    def _set_attributes(self, obj, attrs):
        obj.__dict__.update(attrs)

    def _get_params(self, arg_list, kwargs):
        return {k: v for k, v in kwargs.items() if k in arg_list}

    def construct_host(self, host_cls, provider_params, **kwargs):
        """Construct a host object from a host class and include relevent provider params."""
        host_inst = host_cls(**provider_params, **kwargs)
        host_attrs = self._get_params(self._construct_params)
        host_attrs["release"] = self._host_release
        self._set_attributes(host_inst, host_attrs)
        return host_inst

    @abstractmethod
    def provider_help(self):
        """Help options that will be added to the CLI.

        Anything other than 'self' and 'kwargs' will be added as a help option
        To specify a flag, set the default value to a boolean
        Everything else should default to None
        """

    @abstractmethod
    def get_inventory(self, **kwargs):
        """Pull inventory information from the provider."""

    @abstractmethod
    def extend(self):
        """Extend the reservation of a host. Not all providers support this."""

    @abstractmethod
    def release(self, host_obj):
        """Release/return a host to the provider. Often this is a deletion or removal."""

    def __repr__(self):
        """Return a string representation of the provider."""
        inner = ", ".join(
            f"{k}={'******' if k in self._sensitive_attrs and v else v}"
            for k, v in self.__dict__.items()
            if not k.startswith("_") and not callable(v)
        )
        return f"{self.__class__.__name__}({inner})"

    @staticmethod
    def auto_hide(cls):
        """Decorate a provider class to hide it from the CLI."""
        if not settings.get(cls.__name__.upper(), False):
            # import IPython; IPython.embed()
            cls.hidden = True
        return cls

    @staticmethod
    def register_action(*as_names):
        """Decorate a provider method to register it as a provider action.

        :param as_names: One or more action names to register the decorated function as
        """

        def decorator(func):
            func._as_action = as_names or [func.__name__]
            return func

        return decorator
