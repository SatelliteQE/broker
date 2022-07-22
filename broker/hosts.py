# from functools import cached_property
import pickle
from logzero import logger
from broker.exceptions import NotImplementedError
from broker.session import Session
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
        self.password = kwargs.get("password", settings.HOST_PASSWORD)
        self.timeout = kwargs.get(
            "connection_timeout", settings.HOST_CONNECTION_TIMEOUT
        )
        self.port = kwargs.get("port", settings.HOST_SSH_PORT)
        self.key_filename = kwargs.get("key_filename", settings.HOST_SSH_KEY_FILENAME)
        self._session = None

    def __del__(self):
        """Try to close the connection on garbage collection of the host instance"""
        self.close()
        # object.__del__ DNE, so I don't have to call it here.
        # If host inherits from a different class with __del__, it should get called through super

    @property
    def session(self):
        # This attribute may be missing after pickling
        if not isinstance(getattr(self, "_session", None), Session):
            self.connect()
        return self._session

    def __getstate__(self):
        """If a session is active, remove it for pickle compatability"""
        self.close()
        try:
            self._purify()
        except RecursionError:
                logger.warning(f"Recursion limit reached on {self._purify_target}")
                self.__dict__[self._purify_target] = None
                self.__getstate__()
        del self.__dict__["_purify_target"]
        return self.__dict__

    def _purify(self):
        """Strip all unpickleable attributes from a Host before pickling"""
        for name in list(self.__dict__):
            self._purify_target = name
            try:
                pickle.dumps(self.__dict__[name])
            except (pickle.PicklingError, AttributeError):
                self.__dict__[name] = None

    def connect(
        self, username=None, password=None, timeout=None, port=22, key_filename=None
    ):
        username = username or self.username
        password = password or self.password
        timeout = timeout or self.timeout
        _hostname = self.hostname
        _port = self.port or port
        key_filename = key_filename or self.key_filename
        if ":" in self.hostname:
            _hostname, port = self.hostname.split(":")
            _port = int(port)
        self.close()
        self._session = Session(
            hostname=_hostname,
            username=username,
            password=password,
            port=_port,
            key_filename=key_filename,
        )

    def close(self):
        # This attribute may be missing after pickling
        if isinstance(getattr(self, "_session", None), Session):
            self._session.session.disconnect()
            self._session = None

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
        """Automatically ran when entering a Broker context manager"""
        pass

    def teardown(self):
        """Automatically ran when exiting a Broker context manager"""
        pass

    def __repr__(self):
        inner = ", ".join(
            f"{k}={v}"
            for k, v in self.__dict__.items()
            if not k.startswith("_") and not callable(v)
        )
        return f"{self.__class__.__name__}({inner})"

    @classmethod
    def from_dict(cls, arg_dict):
        return cls(**arg_dict, from_dict=True)
