from logzero import logger
from broker.providers.ansible_tower import AnsibleTower
from broker.providers.test_provider import TestProvider
from broker.hosts import Host
from broker import helpers
from concurrent.futures import ProcessPoolExecutor, as_completed


PROVIDERS = {"AnsibleTower": AnsibleTower, "TestProvider": TestProvider}

PROVIDER_ACTIONS = {
    # action: (InterfaceClass, "method_name")
    "workflow": (AnsibleTower, "execute"),
    "job_template": (AnsibleTower, "execute"),
    "template": (AnsibleTower, None),  # needed for list-templates
    "test_action": (TestProvider, "test_action"),
    "inventory": (AnsibleTower, None)
}


class mp_decorator:
    """This decorator wraps VMBroker methods to enable multiprocessing

    The decorated method is expected to return an itearable.
    """

    # Note that this is a descriptor as the other option -- using nested function
    # like this:
    #
    # def mp_decorator(func)
    #   @wraps(func)
    #   def wrapper(func)
    #       return
    #
    #   return wrapper
    #
    # is not working with pickling that is necessary for the ProcessPoolExecutor of
    # concurrent.futures. I got errors like:
    # _pickle.PicklingError: Can't pickle ... it's not the same object as ...

    MAX_WORKERS = None
    """ If set to integer, the count of workers will be limited to that amount.
     If set to None, the max workers count of the EXECUTOR will match the count of items."""

    EXECUTOR = ProcessPoolExecutor

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


class VMBroker:
    def __init__(self, **kwargs):
        self._hosts = kwargs.pop("hosts", [])
        self.host_classes = {"host": Host}
        # if a nick was specified, pull in the resolved arguments
        if "nick" in kwargs:
            nick = kwargs.pop("nick")
            kwargs = helpers.merge_dicts(kwargs, helpers.resolve_nick(nick))
        if "host_classes" in kwargs:
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
        result = getattr(provider_inst, method)(**self._kwargs)
        logger.debug(result)
        if result and checkout:
            return provider_inst.construct_host(
                provider_params=result, host_classes=self.host_classes, **self._kwargs
            )
        else:
            return result

    @mp_decorator
    def _checkout(self, connect=False):
        """checkout one or more VMs

        :param connect: Boolean whether to establish host ssh connection

        :return: List of Host objects
        """
        hosts = []
        for action in self._provider_actions.keys():
            provider, method = PROVIDER_ACTIONS[action]
            logger.info(f"Using provider {provider.__name__} to checkout")
            host = self._act(provider, method, checkout=True)
            logger.debug(f"host={host} connect={connect}")
            if host:
                if connect:
                    host.connect()
                hosts.append(host)
                logger.info(f"{host.__class__.__name__}: {host.hostname}")
        return hosts

    def checkout(self, connect=False):
        """checkout one or more VMs

        :param connect: Boolean whether to establish host ssh connection

        :return: Host obj or list of Host objects
        """
        hosts = self._checkout(connect=connect)
        self._hosts.extend(hosts)
        helpers.update_inventory([host.to_dict() for host in hosts])
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

    def checkin(self, host=None):
        """checkin one or more VMs

        :param host: can be one of:
            None - Will use the contents of self._hosts
            A single host object
            A list of host objects
            A dictionary mapping host types to one or more host objects
        """
        if host is None:
            host = self._hosts
        logger.debug(host)
        if isinstance(host, dict):
            for _host in host.values():
                self.checkin(_host)
        elif isinstance(host, list):
            # reversing over a copy of the list to avoid skipping
            for _host in host[::-1]:
                self.checkin(_host)
        elif host:
            logger.info(f"Checking in {host.hostname or host.name}")
            host.close()
            host.release()
            self._hosts.remove(host)
            helpers.update_inventory(remove=host.hostname)

    def extend(self, host=None):
        """extend one or more VMs

        :param host: can be one of:
            None - Will use the contents of self._hosts
            A single host object
            A list of host objects
            A dictionary mapping host types to one or more host objects
        """
        if host is None:
            host = self._hosts
        logger.debug(host)
        if isinstance(host, dict):
            for _host in host.values():
                self.extend(_host)
        elif isinstance(host, list):
            # reversing over a copy of the list to avoid skipping
            for _host in host[::-1]:
                self.extend(_host)
        elif host:
            logger.info(f"Extending host {host.hostname}")
            provider = PROVIDERS[host._broker_provider]
            self._kwargs["target_vm"] = host.name
            logger.debug(f"Executing extend with provider {provider.__name__}")
            self._act(provider, "extend_vm", checkout=False)

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
            host["hostname"] or host["name"] for host in helpers.load_inventory()
        ]
        new_hosts = []
        remove_hosts = curr_inventory[:]
        for n_host in prov_inventory:
            name = n_host["hostname"] or n_host["name"]
            if name in curr_inventory:
                remove_hosts.remove(name)
            else:
                new_hosts.append(n_host)
        if new_hosts:
            msg = ", ".join([host["hostname"] or host["name"] for host in new_hosts])
            logger.info(f"Adding new hosts: {msg}")
            helpers.update_inventory(add=new_hosts)
        else:
            logger.info("No new hosts found")
        if remove_hosts:
            msg = ", ".join(remove_hosts)
            logger.info(f"Removing old hosts: {msg}")
            helpers.update_inventory(remove=remove_hosts)

    def reconstruct_host(self, host_export_data, connect=False):
        """reconstruct a host from export data"""
        logger.debug(f"reconstructing host with export: {host_export_data}")
        provider = PROVIDERS.get(host_export_data.get("_broker_provider"))
        if not provider:
            logger.warning(
                f"No provider found with name {host_export_data.get('_broker_provider')}"
            )
            return
        provider_inst = provider(**host_export_data)
        host = provider_inst.construct_host(
            provider_params=None, host_classes=self.host_classes, **host_export_data
        )
        if connect:
            host.connect()
        return host

    def from_inventory(self, connect=False, filter=None):
        """Reconstruct one or more hosts from the local inventory

        :param connect: Boolean - establish ssh connection

        :param filter: A broker-spec filter string
        """
        inv_hosts = helpers.load_inventory(filter=filter)
        return [self.reconstruct_host(inv_host, connect) for inv_host in inv_hosts]

    def __enter__(self):
        try:
            hosts = self.checkout(connect=True)
            if not hosts:
                raise Exception("No hosts created during checkout")
            return hosts
        except Exception as err:
            self.checkin()
            raise err

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.checkin()
