# from functools import cached_property
from broker.settings import settings
from broker import session

class Host:
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

    def connect(self, username=None, password=None):
        username = username or self.username
        password = password or self.password
        self.session = session.Session(
            hostname=self.hostname,
            username=username,
            password=password
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

    def execute(self, command):
        return self.session.run(command)

    def to_dict(self):
        return {
            "hostname": self.hostname,
            "name": getattr(self, "name", None),
            "_broker_provider": self._broker_provider,
            "type": "host",
            "_broker_args": self._broker_args
        }

    @classmethod
    def from_dict(cls, arg_dict):
        return cls(**arg_dict, from_dict=True)
