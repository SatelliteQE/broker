# from functools import cached_property


class Host:
    def __init__(self, hostname, from_dict=False, **kwargs):
        # Allow the class to construct itself from kwargs
        if from_dict:
            self.__dict__.update(kwargs)
        else:
            self.hostname = hostname
        self.session = self._get_session()

    def _get_session(self):
        pass

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
        return self.session.execute(command)

    def to_dict(self):
        return {
            "hostname": self.hostname,
            "_broker_provider": self._broker_provider,
            "type": "host",
        }

    @classmethod
    def from_dict(cls, arg_dict):
        return cls(**arg_dict, from_dict=True)
