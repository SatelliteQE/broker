"""Result and testing helper classes."""

from collections import UserDict, namedtuple
from collections.abc import Hashable

FilterTest = namedtuple("FilterTest", "haystack needle test")


class MockStub(UserDict):
    """Test helper class. Allows for both arbitrary mocking and stubbing."""

    def __init__(self, in_dict=None):
        """Initialize the class and all nested dictionaries."""
        if in_dict is None:
            in_dict = {}
        for key, value in in_dict.items():
            if isinstance(value, dict):
                setattr(self, key, MockStub(value))
            elif type(value) in (list, tuple):
                setattr(
                    self,
                    key,
                    [MockStub(x) if isinstance(x, dict) else x for x in value],
                )
            else:
                setattr(self, key, value)
        super().__init__(in_dict)

    def __getattr__(self, name):
        """Fallback to returning self if attribute doesn't exist."""
        return self

    def __getitem__(self, key):
        """Get an item from the dictionary-like object.

        If the key is a string, this method will attempt to get an attribute with that name.
        If the key is not found, this method will return the object itself.
        """
        try:
            return super().__getitem__(key)
        except KeyError:
            if isinstance(key, str):
                return getattr(self, key, self)
            return self

    def __call__(self, *args, **kwargs):
        """Allow MockStub to be used like a function."""
        return self

    def __eq__(self, other):
        """Check equality with another object."""
        if isinstance(other, MockStub):
            return self.data == other.data
        if isinstance(other, dict):
            return self.data == other
        return False

    def __hash__(self):
        """Return a hash value for the object.

        The hash value is computed using the hash value of all hashable attributes of the object.
        """
        return hash(tuple(kp for kp in self.__dict__.items() if isinstance(kp[1], Hashable)))


def eval_filter(filter_list, raw_filter, filter_key="inv"):
    """Run each filter through an eval to get the results."""
    filter_list = [MockStub(item) if isinstance(item, dict) else item for item in filter_list]
    for raw_f in raw_filter.split("|"):
        if f"@{filter_key}[" in raw_f:
            # perform a list filter on the inventory
            filter_list = eval(  # noqa: S307
                raw_f.replace(f"@{filter_key}", filter_key), {filter_key: filter_list}
            )
            filter_list = filter_list if isinstance(filter_list, list) else [filter_list]
        elif f"@{filter_key}" in raw_f:
            # perform an attribute filter on each host
            filter_list = list(
                filter(
                    lambda item: eval(  # noqa: S307
                        raw_f.replace(f"@{filter_key}", filter_key), {filter_key: item}
                    ),
                    filter_list,
                )
            )
    return [dict(item) if isinstance(item, MockStub) else item for item in filter_list]


class Result:
    """Dummy result class for presenting results in dot access."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __repr__(self):
        """Return a string representation of the object."""
        return f"stdout:\n{self.stdout}\nstderr:\n{self.stderr}\nstatus: {self.status}"

    @classmethod
    def from_ssh(cls, stdout, channel):
        """Create a Result object from an SSH channel."""
        return cls(
            stdout=stdout,
            status=channel.get_exit_status(),
            stderr=channel.read_stderr()[1].decode("utf-8"),
        )

    @classmethod
    def from_duplexed_exec(cls, duplex_exec, runtime=None):
        """Create a Result object from a duplexed exec object from podman or docker."""
        if runtime == "podman":
            status, (stdout, stderr) = duplex_exec
            return cls(
                status=status,
                stdout=stdout.decode("utf-8") if stdout else "",
                stderr=stderr.decode("utf-8") if stderr else "",
            )

        if duplex_exec.output[0]:
            stdout = duplex_exec.output[0].decode("utf-8")
        else:
            stdout = ""
        if duplex_exec.output[1]:
            stderr = duplex_exec.output[1].decode("utf-8")
        else:
            stderr = ""
        return cls(
            status=duplex_exec.exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    @classmethod
    def from_nonduplexed_exec(cls, nonduplex_exec):
        """Create a Result object from a nonduplexed exec object from the docker library."""
        return cls(
            status=nonduplex_exec.exit_code,
            stdout=nonduplex_exec.output.decode("utf-8"),
            stderr="",
        )
