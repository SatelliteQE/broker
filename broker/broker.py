from logzero import logger
from broker.providers.ansible_tower import AnsibleTower
from broker.hosts import Host

PROVIDERS = {
    # action: (InterfaceClass, "method_name")
    "workflow": (AnsibleTower, "exec_workflow")
}

HOST_CLASSES = {"host": Host}


class VMBroker:
    def __init__(self, **kwargs):
        self._hosts = kwargs.get("hosts", [])
        self._provider_actions = {}
        for key, action in PROVIDERS.items():
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

    def checkout(self):
        """checkout one or more VMs"""
        for action, arg in self._provider_actions.items():
            provider, method = PROVIDERS[action]
            logger.info(f"Using provider {provider.__name__} to checkout")
            host = self._act(provider, method, checkout=True)
            if host:
                self._hosts.append(host)

    def checkin(self, host=None):
        """checkin one or more VMs"""
        if host is None:
            host = self._hosts
        if isinstance(host, dict):
            for hosts in host.values():
                self.checkin(hosts)
        elif isinstance(host, list):
            for _host in host:
                self.checkin(hosts)
        elif host:
            host.release()

    def __enter__(self):
        try:
            pass
        except Exception as err:
            # close host connections
            raise Exception

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.checkin(self._hosts)
