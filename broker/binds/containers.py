"""A collection of classes to ease interaction with Docker and Podman libraries."""
from broker.exceptions import UserError
from broker.settings import settings

HEADER_SIZE = 8
STDOUT = 1
STDERR = 2
SSH_PORT = 22


def demux_output(data_bytes):
    """Demuxes the output of a container stream into stdout and stderr streams.

    Stream data is expected to be in the following format:
    - 1 byte: stream type (1=stdout, 2=stderr)
    - 3 bytes: padding
    - 4 bytes: payload size (big-endian)
    - N bytes: payload data
    ref: https://docs.podman.io/en/latest/_static/api.html?version=v5.0#tag/containers/operation/ContainerAttachLibpod

    Args:
        data_bytes: Bytes object containing the combined stream data.

    Returns:
        A tuple containing two bytes objects: (stdout, stderr).
    """
    stdout = b""
    stderr = b""
    while len(data_bytes) >= HEADER_SIZE:
        # Extract header information
        header, data_bytes = data_bytes[:HEADER_SIZE], data_bytes[HEADER_SIZE:]
        stream_type = header[0]
        payload_size = int.from_bytes(header[4:HEADER_SIZE], "big")
        # Check if data is sufficient for payload
        if len(data_bytes) < payload_size:
            break  # Incomplete frame, wait for more data

        # Extract and process payload
        payload = data_bytes[:payload_size]
        if stream_type == STDOUT:
            stdout += payload
        elif stream_type == STDERR:
            stderr += payload
        else:
            # todo: Handle unexpected stream types
            pass

        # Update data for next frame
        data_bytes = data_bytes[payload_size:]

    return stdout, stderr


class ContainerBind:
    """A base class that provides common functionality for Docker and Podman containers."""

    _sensitive_attrs = ["password", "host_password"]

    def __init__(self, host=None, username=None, password=None, port=22, timeout=None):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout
        self._client = None
        self._ClientClass = None

    @property
    def client(self):
        """Return the client instance. Create one if it does not exist."""
        if not isinstance(self._client, self._ClientClass):
            self._client = self._ClientClass(base_url=self.uri, timeout=self.timeout)
        return self._client

    @property
    def images(self):
        """Return a list of images on the container host."""
        return self.client.images.list()

    @property
    def containers(self):
        """Return a list of containers on the container host."""
        return self.client.containers.list(all=True)

    @property
    def networks(self):
        """Return a list of networks on the container host."""
        return self.client.networks.list()

    def image_info(self, name):
        """Return curated information about an image on the container host."""
        if image := self.client.images.get(name):
            return {
                "id": image.short_id,
                "tags": image.tags,
                "size": image.attrs["Size"],
                "config": {k: v for k, v in image.attrs["Config"].items() if k != "Env"},
            }

    def create_container(self, image, command=None, **kwargs):
        """Create and return running container instance."""
        if net_name := settings.container.network:
            net_dict = {}
            for name in net_name.split(","):
                if not self.get_network_by_attrs({"name": name}):
                    raise UserError(f"Network '{name}' not found on container host.")
                net_dict[name] = {"NetworkId": name}
            kwargs["networks"] = net_dict
        kwargs = self._sanitize_create_args(kwargs)
        return self.client.containers.create(image, command, **kwargs)

    def execute(self, image, command=None, remove=True, **kwargs):
        """Run a container and return the raw result."""
        return self.client.containers.run(image, command=command, remove=remove, **kwargs).decode()

    def remove_container(self, container=None):
        """Remove a container from the container host."""
        if container:
            container.remove(v=True, force=True)

    def pull_image(self, name):
        """Pull an image into the container host."""
        return self.client.images.pull(name)

    def get_network_by_attrs(self, attr_dict):
        """Return the first matching network that matches all attr_dict keys and values."""
        for network in self.networks:
            if all(network.attrs.get(k) == v for k, v in attr_dict.items()):
                return network

    @staticmethod
    def get_logs(container):
        """Return the logs from a container."""
        return "\n".join(x.decode() for x in container.logs(stream=False))

    @staticmethod
    def get_attrs(cont):
        """Return curated information about a container."""
        return {
            "id": cont.id,
            "image": cont.attrs.get("ImageName", cont.attrs["Image"]),
            "name": cont.name or cont.attrs["Names"][0],
            "container_config": cont.attrs.get("Config", {}),
            "host_config": cont.attrs.get("HostConfig", {}),
            "ports": cont.ports or cont.attrs.get("Ports"),
        }

    def __repr__(self):
        """Return a string representation of the object."""
        inner = ", ".join(
            f"{k}={'******' if k in self._sensitive_attrs and v else v}"
            for k, v in self.__dict__.items()
            if not k.startswith("_") and not callable(v)
        )
        return f"{self.__class__.__name__}({inner})"


class PodmanBind(ContainerBind):
    """Handles Podman-specific connection and implementation differences."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        from podman import PodmanClient

        self._ClientClass = PodmanClient
        if self.host == "localhost":
            self.uri = "unix:///run/user/1000/podman/podman.sock"
        elif kwargs.get("port") == SSH_PORT:
            self.uri = "http+ssh://{username}@{host}:{port}/run/podman/podman.sock".format(**kwargs)
        else:
            self.uri = "tcp://{host}:{port}".format(**kwargs)

    def _sanitize_create_args(self, kwargs):
        from podman.domain.containers_create import CreateMixin

        try:
            CreateMixin._render_payload(kwargs)
        except TypeError as err:
            sanitized = (
                err.args[0]
                .replace("Unknown keyword argument(s): ", "")
                .replace("'", "")
                .split(" ,")
            )
            kwargs = {k: v for k, v in kwargs.items() if k not in sanitized}
            kwargs = self._sanitize_create_args(kwargs)
        return kwargs


class DockerBind(ContainerBind):
    """Handles Docker-specific connection and implementation differences."""

    def __init__(self, port=2375, **kwargs):
        kwargs["port"] = port
        super().__init__(**kwargs)
        from docker import DockerClient

        self._ClientClass = DockerClient
        if self.host == "localhost":
            self.uri = "unix://var/run/docker.sock"
        elif kwargs.get("port") == SSH_PORT:
            self.uri = "ssh://{username}@{host}:{port}".format(**kwargs)
        else:
            self.uri = "tcp://{username}@{host}".format(**kwargs)

    def _sanitize_create_args(self, kwargs):
        from docker.models.containers import RUN_CREATE_KWARGS, RUN_HOST_CONFIG_KWARGS

        special_kwargs = ["ports", "volumes", "network", "networking_config"]
        accepted_kwargs = RUN_HOST_CONFIG_KWARGS + RUN_CREATE_KWARGS + special_kwargs
        return {k: v for k, v in kwargs.items() if k in accepted_kwargs}
