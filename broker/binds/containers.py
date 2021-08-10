class ContainerBind:
    def __init__(self, host=None, username=None, password=None, port=22):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self._client = None
        self._ClientClass = None

    @property
    def client(self):
        if not isinstance(self._client, self._ClientClass):
            self._client = self._ClientClass(base_url=self.uri)
        return self._client

    @property
    def images(self):
        return self.client.images.list()

    @property
    def containers(self):
        return self.client.containers.list(all=True)

    def image_info(self, name):
        if image := self.client.images.get(name):
            return {
                "id": image.short_id,
                "tags": image.tags,
                "size": image.attrs["Size"],
                "config": {
                    k: v for k, v in image.attrs["Config"].items() if k != "Env"
                },
            }

    def create_container(self, image, command=None, **kwargs):
        """Create and return running container instance"""
        return self.client.containers.create(image, command, **kwargs)

    def execute(self, image, command=None, remove=True, **kwargs):
        """Run a container and return the raw result"""
        return self.client.containers.run(
            image, command=command, remove=remove, **kwargs
        ).decode()

    def remove_container(self, container=None):
        if container:
            container.remove(v=True, force=True)

    def pull_image(self, name):
        return self.client.images.pull(name)

    @staticmethod
    def get_logs(container):
        return "\n".join(map(lambda x: x.decode(), container.logs(stream=False)))

    @staticmethod
    def get_attrs(cont):
        return {
            "id": cont.id,
            "image": cont.attrs.get("ImageName", cont.attrs["Image"]),
            "name": cont.name or cont.attrs["Names"][0],
            "container_config": cont.attrs.get("Config", {}),
            "host_config": cont.attrs.get("HostConfig", {}),
            "ports": cont.ports or cont.attrs.get("Ports"),
        }

    def __repr__(self):
        inner = ", ".join(
            f"{k}={v}"
            for k, v in self.__dict__.items()
            if not k.startswith("_") and not callable(v)
        )
        return f"{self.__class__.__name__}({inner})"


class PodmanBind(ContainerBind):
    def __init__(self, host=None, username=None, password=None, port=22):
        super().__init__(host, username, password, port)
        from podman import PodmanClient

        self._ClientClass = PodmanClient
        if self.host == "localhost":
            self.uri = "unix:///run/user/1000/podman/podman.sock"
        else:
            self.uri = f"http+ssh://{username}@{host}:{port}/run/podman/podman.sock"


class DockerBind(ContainerBind):
    def __init__(self, host=None, username=None, password=None, port=2375):
        super().__init__(host, username, password, port)
        from docker import DockerClient

        self._ClientClass = DockerClient
        if self.host == "localhost":
            self.uri = "unix://var/run/docker.sock"
        else:
            self.uri = f"ssh://{username}@{host}"
