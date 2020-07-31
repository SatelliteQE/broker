from logzero import logger
from broker.providers.ansible_tower import AnsibleTower
from broker.providers.test_provider import TestProvider
from broker.hosts import Host
from broker import helpers


PROVIDERS = {"AnsibleTower": AnsibleTower, "TestProvider": TestProvider}

PROVIDER_ACTIONS = {
    # action: (InterfaceClass, "method_name")
    "workflow": (AnsibleTower, "exec_workflow"),
    "test_action": (TestProvider, "test_action"),
}

HOST_CLASSES = {"host": Host}


class VMBroker:
    def __init__(self, **kwargs):
        self._hosts = kwargs.pop("hosts", [])
        # if a nick was specified, pull in the resolved arguments
        if "nick" in kwargs:
            nick = kwargs.pop("nick")
            kwargs = helpers.merge_dicts(kwargs, helpers.resolve_nick(nick))
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
                provider_params=result, host_classes=HOST_CLASSES, **self._kwargs
            )
        else:
            return result

    def checkout(self, connect=False):
        """checkout one or more VMs

        :param connect: Boolean whether to establish host ssh connection

        :return: Host obj or list of Host objects
        """
        for action, arg in self._provider_actions.items():
            provider, method = PROVIDER_ACTIONS[action]
            logger.info(f"Using provider {provider.__name__} to checkout")
            host = self._act(provider, method, checkout=True)
            if host:
                if connect:
                    host.connect()
                self._hosts.append(host)
                logger.info(f"{host.__class__.__name__}: {host.hostname}")
                helpers.update_inventory(add=host.to_dict())
        return self._hosts if not len(self._hosts) == 1 else self._hosts[0]

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
            for action, arg in self._provider_actions.items():
                provider, _ = PROVIDER_ACTIONS[action]
                logger.info(f"Querying provider {provider.__name__}")
                self._act(provider, "nick_help", checkout=False)
        elif self._kwargs.get("provider"):
            provider = PROVIDERS[self._kwargs["provider"]]
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
            logger.info(f"Checking in {host.hostname}")
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
        additional_arg = None
        if ":" in provider:
            provider, additional_arg = provider.split(":")
        logger.info(f"Pulling remote inventory from {provider}")
        prov_inventory = PROVIDERS[provider]().get_inventory(additional_arg)
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

    @staticmethod
    def reconstruct_host(host_export_data):
        """reconstruct a host from export data"""
        logger.debug(f"reconstructing host with export: {host_export_data}")
        provider = PROVIDERS.get(host_export_data.get("_broker_provider"))
        if not provider:
            logger.warning(
                f"No provider found with name {host_export_data.get('_broker_provider')}"
            )
            return
        provider_inst = provider(**host_export_data)
        return provider_inst.construct_host(
            provider_params=None, host_classes=HOST_CLASSES, **host_export_data
        )

    def __enter__(self):
        try:
            return self.checkout(connect=True)
        except Exception as err:
            self.checkin()
            raise err

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.checkin()
