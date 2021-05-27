# from functools import cached_property
from logzero import logger
from broker import session
from broker.exceptions import NotImplementedError
from broker.settings import settings


class Host:

    default_timeout = 0  # timeout in ms, 0 is infinite

    def __init__(self, hostname, name=None, from_dict=False, **kwargs):
        # Allow the class to construct itself from kwargs
        if from_dict:
            self.__dict__.update(kwargs)
        else:
            self.hostname = hostname
            self.name = name
        self.username = kwargs.get("username", settings.HOST_USERNAME)
        self.password = kwargs.get("pwassword", settings.HOST_PASSWORD)
        self.session = None

    def __getstate__(self):
        """If a session is active, remove it for pickle compatability"""
        if self.session:
            self.__connected = True
            self.session = None
        return self.__dict__

    def __setstate__(self, pickle_dict):
        """If a session was active pre-pickle, reconnect post-pickle"""
        self.__dict__ = pickle_dict
        if self.__dict__.pop('__connected', False):
            self.connect()

    def connect(self, username=None, password=None):
        username = username or self.username
        password = password or self.password
        self.session = session.Session(
            hostname=self.hostname, username=username, password=password
        )

    def close(self):
        if isinstance(self.session, session.Session):
            self.session.session.disconnect()

    def release(self):
        raise NotImplementedError("release has not been implemented for this provider")

    # @cached_property
    def hostname(self):
        return self.session.execute("hostname").strip()

    # @cached_property
    def _pkg_mgr(self):
        for mgr in ["yum", "dnf", "zypper"]:
            if f"no {mgr} in" in self.execute(f"which {mgr}"):
                return mgr
        return None

    def execute(self, command, timeout=None):
        timeout = timeout or self.default_timeout
        logger.debug(f"{self.hostname} executing command: {command}")
        res = self.session.run(command, timeout=timeout)
        logger.debug(f"{self.hostname} command result:\n{res}")
        return res

    def to_dict(self):
        return {
            "hostname": self.hostname,
            "name": getattr(self, "name", None),
            "_broker_provider": self._broker_provider,
            "type": "host",
            "_broker_args": self._broker_args,
        }

    def setup(self):
        """Automatically ran when entering a VMBroker context manager"""
        pass

    def teardown(self):
        """Automatically ran when exiting a VMBroker context manager"""
        pass

    @classmethod
    def from_dict(cls, arg_dict):
        return cls(**arg_dict, from_dict=True)
