from logzero import logger
from broker.providers.ansible_tower import AnsibleTower
from broker.providers.container import Container
from broker.providers.test_provider import TestProvider
from broker.hosts import Host
from broker import exceptions, helpers
from concurrent.futures import ThreadPoolExecutor, as_completed


PROVIDERS = {
    "AnsibleTower": AnsibleTower,
    "Container": Container,
    "TestProvider": TestProvider,
}

PROVIDER_ACTIONS = {
    # action: (InterfaceClass, "method_name")
    "workflow": (AnsibleTower, "execute"),
    "job_template": (AnsibleTower, "execute"),
    "template": (AnsibleTower, None),  # needed for list-templates
    "inventory": (AnsibleTower, None),
    "container_host": (Container, "run_container"),
    "container_app": (Container, "execute"),
    "test_action": (TestProvider, "test_action"),
}


def _try_teardown(host_obj):
    """Try a host's teardown method and return an exception message if needed"""
    try:
        host_obj.teardown()
    except Exception as err:
        return exceptions.HostError(host_obj, f"error during teardown:\n{err}")


class mp_decorator:
    """This decorator wraps Broker methods to enable multiprocessing

    The decorated method is expected to return an itearable.
    """
    MAX_WORKERS = None
    """ If set to integer, the count of workers will be limited to that amount.
     If set to None, the max workers count of the EXECUTOR will match the count of items."""

    EXECUTOR = ThreadPoolExecutor

    def __init__(self, func=None):
        self.func = func

    def __get__(self, instance, owner):
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
                    workers.submit(self.func, instance, *args, **kwargs)
                    for _ in range(count)
                )
                for f in completed_futures:
                    results.extend(f.result())
            return results

        return mp_split


class Broker:
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
        for key, action in PROVIDER_ACTIONS.items():
            if key in kwargs:
                self._provider_actions[key] = action
        self._kwargs = kwargs

    def _act(self, provider, method, checkout=False):
        """Perform a general action against a provider's method"""
        provider_inst = provider(**self._kwargs)
        helpers.emit(
            {
                "provider": provider_inst.__class__.__name__,
                "action": method,
                "arguments": self._kwargs,
            }
        )
        result = getattr(provider_inst, method)(**self._kwargs)
        logger.debug(result)
        if result and checkout:
            return provider_inst.construct_host(
                provider_params=result, host_classes=self.host_classes, **self._kwargs
            )
        else:
            return result

    @mp_decorator
    def _checkout(self):
        """checkout one or more VMs

        :return: List of Host objects
        """
        hosts = []
        if not self._provider_actions:
            raise self.BrokerError("Could not determine an appropriate provider")
        for action in self._provider_actions.keys():
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
        """checkout one or more VMs

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
            raise err
        return hosts if not len(hosts) == 1 else hosts[0]

    def execute(self, **kwargs):
        """execute a provider action

        :return: Any The results given back by the provider
        """
        if not self._provider_actions:
            for key, action in PROVIDER_ACTIONS.items():
                if key in kwargs:
                    self._provider_actions[key] = action
        self._kwargs.update(kwargs)
        if not self._provider_actions:
            raise self.BrokerError("Could not determine an appropriate provider")
        for action, arg in self._provider_actions.items():
            provider, method = PROVIDER_ACTIONS[action]
        logger.info(f"Using provider {provider.__name__} for execution")
        return self._act(provider, method)

    def nick_help(self):
        """Use a provider's nick_help method to get argument information"""
        if self._provider_actions:
            provider, _ = PROVIDER_ACTIONS[[*self._provider_actions.keys()][0]]
            logger.info(f"Querying provider {provider.__name__}")
            self._act(provider, "nick_help", checkout=False)

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

    def checkin(self, sequential=False, host=None):
        """checkin one or more VMs

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
            f"Checkin called with: {hosts}, "
            f'running {"sequential" if sequential else "concurrent"}'
        )
        # normalize the type since the function accepts multiple types
        if isinstance(hosts, dict):
            # flatten the lists of hosts from the values of the dict
            hosts = [host for host_list in hosts.values() for host in host_list]
        if not isinstance(hosts, list):
            hosts = [hosts]

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
                self._hosts = [
                    h for h in self._hosts if not (h.to_dict() == _host.to_dict())
                ]
                logger.debug(
                    f"Completed checkin process for {_host.hostname or _host.name}"
                )
        helpers.update_inventory(remove=[h.hostname for h in hosts])

    def _extend(self, host):
        """extend a single VM"""
        logger.info(f"Extending host {host.hostname}")
        provider = PROVIDERS[host._broker_provider]
        self._kwargs["target_vm"] = host
        self._act(provider, "extend_vm", checkout=False)
        return host

    def extend(self, sequential=False, host=None):
        """extend one or more VMs

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

        with ThreadPoolExecutor(
            max_workers=1 if sequential else len(hosts)
        ) as workers:
            completed_extends = as_completed(
                workers.submit(self._extend, _host) for _host in hosts
            )
            for completed in completed_extends:
                _host = completed.result()
                logger.info(f"Completed extend for {_host.hostname or _host.name}")

    @staticmethod
    def sync_inventory(provider):
        """Acquire a list of hosts from a provider and update our inventory"""
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
            host.get("hostname", host.get("name"))
            for host in helpers.load_inventory()
            if host["_broker_provider"] == provider
        ]
        helpers.update_inventory(add=prov_inventory, remove=curr_inventory)

    def reconstruct_host(self, host_export_data):
        """reconstruct a host from export data"""
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
        """Reconstruct one or more hosts from the local inventory

        :param filter: A broker-spec filter string
        """
        inv_hosts = helpers.load_inventory(filter=filter)
        return [self.reconstruct_host(inv_host) for inv_host in inv_hosts]

    def __repr__(self):
        inner = ", ".join(
            f"{k}={v}"
            for k, v in self.__dict__.items()
            if not k.startswith("_") and not callable(v)
        )
        return f"{self.__class__.__name__}({inner})"

    def __enter__(self):
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
        last_exception = None
        for host in self._hosts:
            last_exception = _try_teardown(host)
        self.checkin()
        if last_exception:
            raise last_exception
