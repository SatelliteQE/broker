"""Main interface for the Broker API.

This module provides the main interface for the Broker API, which allows users to
manage cloud resources across multiple providers.

It defines the `Host` class, which represents a cloud resource, and the `Broker` class,
which provides methods for managing hosts.

The `Broker` class is decorated with `mp_decorator`, which enables multiprocessing for
certain methods. The `Host` class is defined in the `broker.hosts` module,
and the provider classes are defined in the `broker.providers` module.

Exceptions are defined in the `broker.exceptions` module,
and helper functions are defined in the `broker.helpers` module.

Note:
    This module (or parent directory) should be used as the main entry point for the Broker API.

"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager

from logzero import logger

from broker import exceptions, helpers
from broker.hosts import Host
from broker.providers import PROVIDER_ACTIONS, PROVIDERS, _provider_imports

# load all the provider class so they are registered
for _import in _provider_imports:
    __import__(f"broker.providers.{_import}", globals(), locals(), [], 0)


def _try_teardown(host_obj):
    """Try a host's teardown method and return an exception message if needed."""
    try:
        host_obj.teardown()
    except Exception as err:  # noqa: BLE001
        logger.debug(f"Tell Jake the exception was: {err}")
        return exceptions.HostError(host_obj, f"error during teardown:\n{err}")


class mp_decorator:
    """Decorator wrapping Broker methods to enable multiprocessing.

    The decorated method is expected to return an itearable.
    """

    MAX_WORKERS = None
    """ If set to integer, the count of workers will be limited to that amount.
     If set to None, the max workers count of the EXECUTOR will match the count of items."""

    EXECUTOR = ThreadPoolExecutor

    def __init__(self, func=None):
        self.func = func

    def __get__(self, instance, owner):
        """Support instance methods."""
        if not instance:
            return self.func

        def mp_split(*args, **kwargs):
            count = instance._kwargs.get("_count", None)
            if count is None:
                return self.func(instance, *args, **kwargs)

            results = []
            max_workers_count = self.MAX_WORKERS or count
            with self.EXECUTOR(max_workers=max_workers_count) as workers:
                completed_futures = as_completed(
                    workers.submit(self.func, instance, *args, **kwargs) for _ in range(count)
                )
                for f in completed_futures:
                    results.extend(f.result())
            return results

        return mp_split


class Broker:
    """Main Broker class to be used as the primary interface for the Broker API."""

    # map exceptions for easier access when used as a library
    BrokerError = exceptions.BrokerError
    AuthenticationError = exceptions.AuthenticationError
    PermissionError = exceptions.PermissionError
    ProviderError = exceptions.ProviderError
    ConfigurationError = exceptions.ConfigurationError
    NotImplementedError = exceptions.NotImplementedError

    def __init__(self, **kwargs):
        kwargs = helpers.resolve_file_args(kwargs)
        logger.debug(f"Broker instantiated with {kwargs=}")
        self._hosts = kwargs.pop("hosts", [])
        self.host_classes = {"host": Host}
        # if a nick was specified, pull in the resolved arguments
        if "nick" in kwargs:
            nick = kwargs.pop("nick")
            kwargs = helpers.merge_dicts(kwargs, helpers.resolve_nick(nick))
            logger.debug(f"kwargs after nick resolution {kwargs=}")
        # Allow users to more simply pass a host class instead of a dict
        if "host_class" in kwargs:
            self.host_classes["host"] = kwargs.pop("host_class")
        elif "host_classes" in kwargs:
            self.host_classes.update(kwargs.pop("host_classes"))
        # determine the provider actions based on kwarg parameters
        self._provider_actions = {}
        self._update_provider_actions(kwargs)
        self._kwargs = kwargs

    def _act(self, provider, method, checkout=False):
        """Perform a general action against a provider's method."""
        logger.debug(f"Resolving action {method} on provider {provider}.")
        provider_inst = provider(**self._kwargs)
        helpers.emit(
            {
                "provider": provider_inst.__class__.__name__,
                "action": method,
                "arguments": self._kwargs,
            }
        )
        method_obj = getattr(provider_inst, method)
        logger.debug(f"On {provider_inst=} executing {method_obj=} with params {self._kwargs=}.")
        result = method_obj(**self._kwargs)
        logger.debug(f"Action {result=}")
        if result and checkout:
            return provider_inst.construct_host(
                provider_params=result, host_classes=self.host_classes, **self._kwargs
            )
        else:
            return result

    def _update_provider_actions(self, kwargs):
        if not self._provider_actions:
            for key, action in PROVIDER_ACTIONS.items():
                if key in kwargs:
                    self._provider_actions[key] = action

    @mp_decorator
    def _checkout(self):
        """Checkout one or more VMs.

        :return: List of Host objects
        """
        hosts = []
        logger.debug(f"Doing _checkout(): {self._provider_actions=}")
        if not self._provider_actions:
            raise self.BrokerError("Could not determine an appropriate provider")
        for action in self._provider_actions:
            provider, method = PROVIDER_ACTIONS[action]
            logger.info(f"Using provider {provider.__name__} to checkout")
            try:
                host = self._act(provider, method, checkout=True)
            except exceptions.ProviderError as err:
                host = err
            hosts.append(host)
            if host and not isinstance(host, exceptions.ProviderError):
                logger.info(f"{host.__class__.__name__}: {host.hostname}")
        return hosts

    def checkout(self):
        """Checkout one or more VMs.

        :return: Host obj or list of Host objects
        """
        hosts = self._checkout()
        err = None
        for host in hosts[:]:
            if isinstance(host, exceptions.ProviderError):
                err = host
                hosts.remove(host)
        helpers.emit(hosts=[host.to_dict() for host in hosts])
        self._hosts.extend(hosts)
        helpers.update_inventory([host.to_dict() for host in hosts])
        if err:
            raise self.BrokerError(f"Error during checkout from {self}") from err
        return hosts if len(hosts) != 1 else hosts[0]

    def execute(self, **kwargs):
        """Execute a provider action.

        :return: Any results given back by the provider
        """
        self._update_provider_actions(kwargs)
        self._kwargs.update(kwargs)
        if not self._provider_actions:
            raise self.BrokerError("Could not determine an appropriate provider")
        for action in self._provider_actions:
            provider, method = PROVIDER_ACTIONS[action]
        logger.info(f"Using provider {provider.__name__} for execution")
        return self._act(provider, method)

    def provider_help(self, provider_name):
        """Use a provider's provider_help method to get argument information."""
        provider = PROVIDERS[provider_name]
        logger.info(f"Querying provider {provider.__name__}")
        self._act(provider, "provider_help", checkout=False)

    def _checkin(self, host):
        logger.info(f"Checking in {host.hostname or host.name}")
        host.close()
        try:
            host.release()
        except Exception as err:
            logger.warning(f"Encountered exception during checkin: {err}")
            raise
            # pass
        return host

    def checkin(self, sequential=False, host=None, in_context=False):
        """Checkin one or more VMs.

        :param host: can be one of:
            None - Will use the contents of self._hosts
            A single host object
            A list of host objects
            A dictionary mapping host types to one or more host objects

        :param sequential: boolean whether to run checkins sequentially
        :param in_context: Whether checkin is part of context manager
        """
        # default to hosts listed on the instance
        hosts = host or self._hosts
        logger.debug(
            f"Checkin called with: {hosts}, "
            f'running {"sequential" if sequential else "concurrent"}'
        )
        # normalize the type since the function accepts multiple types
        if isinstance(hosts, dict):
            # flatten the lists of hosts from the values of the dict
            hosts = [host for host_list in hosts.values() for host in host_list]
        else:
            if not isinstance(hosts, list):
                hosts = [hosts]
            if in_context:
                hosts = [
                    host for host in hosts if not getattr(host, "_skip_context_checkin", False)
                ]
        if not hosts:
            logger.debug("Checkin called with no hosts, taking no action")
            return

        with ThreadPoolExecutor(max_workers=1 if sequential else None) as workers:
            completed_checkins = as_completed(
                # reversing over a copy of the list to avoid skipping
                workers.submit(self._checkin, _host)
                for _host in hosts[::-1]
            )
            for completed in completed_checkins:
                _host = completed.result()
                self._hosts = [h for h in self._hosts if h.to_dict() != _host.to_dict()]
                logger.debug(f"Completed checkin process for {_host.hostname or _host.name}")
        helpers.update_inventory(remove=[h.hostname for h in hosts])

    def _extend(self, host):
        """Extend a single VM."""
        logger.info(f"Extending host {host.hostname}")
        provider = PROVIDERS[host._broker_provider]
        self._kwargs["target_vm"] = host
        self._act(provider, "extend", checkout=False)
        return host

    def extend(self, sequential=False, host=None):
        """Extend one or more VMs.

        :param host: can be one of:
            None - Will use the contents of self._hosts
            A single host object
            A list of host objects
            A dictionary mapping host types to one or more host objects

        :param sequential: boolean whether to run checkins sequentially
        """
        # default to hosts listed on the instance
        hosts = host or self._hosts
        logger.debug(
            f"Extend called with: {hosts}, "
            f'running {"sequential" if sequential else "concurrent"}'
        )
        # normalize the type since the function accepts multiple types
        if isinstance(hosts, dict):
            # flatten the lists of hosts from the values of the dict
            hosts = [host for host_list in hosts.values() for host in host_list]
        if not isinstance(hosts, list):
            hosts = [hosts]

        if not hosts:
            logger.debug("Extend called with no hosts, taking no action")
            return

        with ThreadPoolExecutor(max_workers=1 if sequential else len(hosts)) as workers:
            completed_extends = as_completed(workers.submit(self._extend, _host) for _host in hosts)
            for completed in completed_extends:
                _host = completed.result()
                logger.info(f"Completed extend for {_host.hostname or _host.name}")

    @staticmethod
    def sync_inventory(provider):
        """Acquire a list of hosts from a provider and update our inventory."""
        additional_arg, instance = None, {}
        if "::" in provider:
            provider, instance = provider.split("::")
        if ":" in provider:
            provider, additional_arg = provider.split(":")
        logger.info(
            f"Pulling remote inventory from {f'{instance } ' if instance else ''}{provider}"
        )
        if instance:
            instance = {provider: instance}
        prov_inventory = PROVIDERS[provider](**instance).get_inventory(additional_arg)
        curr_inventory = [
            hostname if (hostname := host.get("hostname")) else host.get("name")
            for host in helpers.load_inventory(filter=f'@inv._broker_provider == "{provider}"')
        ]
        helpers.update_inventory(add=prov_inventory, remove=curr_inventory)

    def reconstruct_host(self, host_export_data):
        """Reconstruct a host from export data."""
        logger.debug(f"reconstructing host with export: {host_export_data}")
        provider = PROVIDERS.get(host_export_data.get("_broker_provider"))
        if not provider:
            logger.warning(
                f"No provider found with name {host_export_data.get('_broker_provider')}"
            )
            return
        if provider_instance := host_export_data.get("_broker_provider_instance"):
            host_export_data[provider.__name__] = provider_instance
        provider_inst = provider(**host_export_data)
        host = provider_inst.construct_host(
            provider_params=None, host_classes=self.host_classes, **host_export_data
        )
        return host

    def from_inventory(self, filter=None):
        """Reconstruct one or more hosts from the local inventory.

        :param filter: A broker-spec filter string
        """
        inv_hosts = helpers.load_inventory(filter=filter)
        return [self.reconstruct_host(inv_host) for inv_host in inv_hosts]

    @classmethod
    @contextmanager
    def multi_manager(cls, **multi_dict):
        """Allow a user to check out multiple hosts at once.

        Given a mapping of names to Broker argument dictionaries:
            create multiple Broker instances, check them out in parallel, yield, then checkin.

        Example:
            with Broker.multi_mode(
                rhel7={
                    "host_class": ContentHost,
                    "workflow": "deploy_base_rhel",
                    "deploy_rhel_version": "7",
                },
                rhel8={
                    "host_class": ContentHost,
                    "workflow": "deploy_base_rhel",
                    "deploy_rhel_version": "8",
                }
            ) as host_dict:
                pass

        All are checked out at the same time. The user is presented with the hosts in
        a dictionary by argument name e.g. host_dict["rhel7"] is a ContentHost object
        """
        # create all the broker instances and perform checkouts in parallel
        broker_instances = {name: cls(**kwargs) for name, kwargs in multi_dict.items()}
        with ThreadPoolExecutor(max_workers=len(broker_instances)) as workers:
            completed_checkouts = as_completed(
                workers.submit(broker.checkout) for broker in broker_instances.values()
            )
            for completed in completed_checkouts:
                completed.result()
        all_hosts = []
        for broker_inst in broker_instances.values():
            all_hosts.extend(broker_inst._hosts)
        # run setup on all hosts in parallel
        with ThreadPoolExecutor(max_workers=len(all_hosts)) as workers:
            completed_setups = as_completed(workers.submit(host.setup) for host in all_hosts)
            for completed in completed_setups:
                completed.result()
        # yield control to the user
        yield {name: broker._hosts for name, broker in broker_instances.items()}
        # teardown all hosts in parallel
        with ThreadPoolExecutor(max_workers=len(all_hosts)) as workers:
            completed_teardowns = as_completed(workers.submit(host.teardown) for host in all_hosts)
            for completed in completed_teardowns:
                completed.result()
        # checkin all hosts in parallel
        with ThreadPoolExecutor(max_workers=len(broker_instances)) as workers:
            completed_checkins = as_completed(
                workers.submit(broker.checkin) for broker in broker_instances.values()
            )
            for completed in completed_checkins:
                completed.result()

    def __repr__(self):
        """Return a string representation of the Broker instance."""
        inner = ", ".join(
            f"{k}={v}"
            for k, v in self.__dict__.items()
            if not k.startswith("_") and not callable(v)
        )
        return f"{self.__class__.__name__}({inner})"

    def __enter__(self):
        """Checkout hosts and return them to the user."""
        try:
            hosts = self.checkout()
            if not hosts:
                raise Exception("No hosts created during checkout")
            if isinstance(hosts, list):
                [host.setup() for host in hosts]
            else:
                hosts.setup()
            return hosts
        except Exception as err:
            for host in self._hosts:
                _try_teardown(host)
            self.checkin()
            raise err

    def __exit__(self, exc_type, exc_value, exc_traceback):
        """Teardown and checkin hosts."""
        last_exception = None
        for host in self._hosts:
            last_exception = _try_teardown(host)
        self.checkin(in_context=True)
        if last_exception:
            raise last_exception
