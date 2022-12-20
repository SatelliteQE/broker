from functools import cache
import getpass
import inspect
from uuid import uuid4
import click
from logzero import logger
from dynaconf import Validator
from broker import exceptions
from broker import helpers
from broker.settings import settings
from broker.providers import Provider
from broker.binds import containers


def container_info(container_inst):
    return {
        "_broker_provider": "Container",
        "name": container_inst.name,
        "hostname": container_inst.id[:12],
        "image": container_inst.image.tags,
        "ports": container_inst.ports,
        "status": container_inst.status,
    }


def _host_release():
    caller_host = inspect.stack()[1][0].f_locals["host"]
    if not caller_host._cont_inst:
        caller_host._cont_inst = caller_host._prov_inst._cont_inst_by_name(
            caller_host.name
        )
    caller_host._cont_inst.remove(v=True, force=True)
    caller_host._checked_in = True


@cache
def get_runtime(
    runtime_cls=None, host=None, username=None, password=None, port=None, timeout=None
):
    return runtime_cls(
        host=host,
        username=username,
        password=password,
        port=port,
        timeout=timeout,
    )


class Container(Provider):
    _validators = [
        Validator("CONTAINER.runtime", default="podman"),
        Validator("CONTAINER.host", default="localhost"),
        Validator("CONTAINER.host_username", default="root"),
        Validator(
            "CONTAINER.host_password",
        ),
        Validator("CONTAINER.host_port", default=22),
        Validator("CONTAINER.timeout", default=360),
        Validator("CONTAINER.auto_map_ports", is_type_of=bool, default=True),
    ]
    _checkout_options = [
        click.option(
            "--container-host",
            type=str,
            help="Name of a broker-compatible container host image",
        ),
    ]
    _execute_options = [
        click.option(
            "--container-app",
            type=str,
            help="Name of a container application image",
        ),
    ]
    _extend_options = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if kwargs.get("bind") is not None:
            self._runtime_cls = kwargs.pop("bind")
        elif settings.container.runtime.lower() == "podman":
            self._runtime_cls = containers.PodmanBind
        elif settings.container.runtime.lower() == "docker":
            self._runtime_cls = containers.DockerBind
        else:
            raise exceptions.ProviderError(
                "Container",
                f"Broker has no bind for {settings.container.runtime} containers",
            )
        self.runtime = get_runtime(
            runtime_cls=self._runtime_cls,
            host=settings.container.host,
            username=settings.container.host_username,
            password=settings.container.host_password,
            port=settings.container.host_port,
            timeout=settings.container.timeout,
        )
        self._name_prefix = settings.container.get("name_prefix", getpass.getuser())

    def _post_pickle(self, purified):
        self._validate_settings()
        self.runtime = self._runtime_cls(
            host=settings.container.host,
            username=settings.container.host_username,
            password=settings.container.host_password,
            port=settings.container.host_port,
            timeout=settings.container.timeout,
        )

    def _ensure_image(self, name):
        """Check if an image exists on the provider, attempt a pull if not"""
        for image in self.runtime.images:
            if name in image.tags:
                return
            elif ("localhost/" in name) and (name[10:] in image.tags):
                return
        try:
            self.runtime.pull_image(name)
        except Exception as err:
            raise exceptions.ProviderError(
                "Container", f"Unable to find image: {name}\n{err}"
            )

    @staticmethod
    def _find_ssh_port(port_map):
        """Go through container port map and find the mapping that corresponds to port 22"""
        if isinstance(port_map, list):
            # [{'hostPort': 1337, 'containerPort': 22, 'protocol': 'tcp', 'hostIP': ''},
            for pm in port_map:
                if pm["containerPort"] == 22:
                    return pm["hostPort"]
        elif isinstance(port_map, dict):
            # {'22/tcp': [{'HostIp': '', 'HostPort': '1337'}],
            for key, val in port_map.items():
                if key.startswith("22"):
                    return val[0]["HostPort"]

    def _set_attributes(self, host_inst, broker_args=None, cont_inst=None):
        host_inst.__dict__.update(
            {
                "_prov_inst": self,
                "_cont_inst": cont_inst,
                "_broker_provider": "Container",
                "_broker_provider_instance": self.instance,
                "_broker_args": broker_args,
                "release": _host_release,
            }
        )

    def _port_mapping(self, image, **kwargs):
        """
        22
        22:1337
        22/tcp
        22/tcp:1337
        22,23
        22:1337 23:1335
        """
        mapping = {}
        if ports := kwargs.pop("ports", None):
            if isinstance(ports, str):
                for _map in ports.split():
                    if ":" in _map:
                        p, h = _map.split(":")
                    else:
                        p, h = _map, None
                    if "/" in p:
                        p, s = p.split("/")
                    else:
                        p, s = p, "tcp"
                    mapping[f"{p}/{s}"] = int(h) if h else None
        elif settings.container.auto_map_ports:
            mapping = {
                k: v or None
                for k, v in self.runtime.image_info(image)["config"][
                    "ExposedPorts"
                ].items()
            }
        return mapping

    def _cont_inst_by_name(self, cont_name):
        """Attempt to find and return a container by its name"""
        for cont_inst in self.runtime.containers:
            if cont_inst.name == cont_name:
                return cont_inst
        logger.error(f"Unable to find container by name {cont_name}")

    def construct_host(self, provider_params, host_classes, **kwargs):
        """Constructs broker host from a container instance

        :param provider_params: a container instance object

        :param host_classes: host object

        :return: broker object of constructed host instance
        """
        logger.debug(
            f"constructing with {provider_params=}\n{host_classes=}\n{kwargs=}"
        )
        if not provider_params:
            host_inst = host_classes[kwargs.get("type", "host")](**kwargs)
            cont_inst = self._cont_inst_by_name(host_inst.name)
            self._set_attributes(host_inst, broker_args=kwargs, cont_inst=cont_inst)
            return host_inst
        cont_inst = provider_params
        cont_attrs = self.runtime.get_attrs(cont_inst)
        logger.debug(cont_attrs)
        hostname = cont_inst.id[:12]
        if port := self._find_ssh_port(cont_attrs["ports"]):
            hostname = f"{hostname}:{port}"
        if not hostname:
            raise Exception(f"Could not determine container hostname:\n{cont_attrs}")
        name = cont_attrs["name"]
        logger.debug(f"hostname: {hostname}, name: {name}, host type: host")
        host_inst = host_classes["host"](
            **{**kwargs, "hostname": hostname, "name": name}
        )
        self._set_attributes(host_inst, broker_args=kwargs, cont_inst=cont_inst)
        return host_inst

    def nick_help(self, **kwargs):
        """Useful information about container images"""
        results_limit = kwargs.get("results_limit", settings.CONTAINER.results_limit)
        if image := kwargs.get("container_host"):
            logger.info(
                f"Information for {image} container-host:\n"
                f"{helpers.yaml_format(self.runtime.image_info(image))}"
            )
        elif kwargs.get("container_hosts"):
            images = [
                img.tags[0]
                for img in self.runtime.images
                if img.labels.get("broker_compatible") and img.tags
            ]
            if res_filter := kwargs.get("results_filter"):
                images = helpers.results_filter(images, res_filter)
                images = images if isinstance(images, list) else [images]
            images = "\n".join(images[:results_limit])
            logger.info(f"Available host images:\n{images}")
        elif kwargs.get("container_apps"):
            images = [img.tags[0] for img in self.runtime.images if img.tags]
            if res_filter := kwargs.get("results_filter"):
                images = helpers.results_filter(images, res_filter)
                images = images if isinstance(images, list) else [images]
            images = "\n".join(images[:results_limit])
            logger.info(f"Available app images:\n{images}")

    def get_inventory(self, name_prefix):
        """Get all containers that have a matching name prefix"""
        name_prefix = name_prefix or self._name_prefix
        return [
            container_info(cont)
            for cont in self.runtime.containers
            if cont.name.startswith(name_prefix)
        ]

    def extend(self):
        pass

    def release(self, host_obj):
        host_obj._cont_inst.remove(force=True)

    def run_container(self, container_host, **kwargs):
        """Start a container based on an image name (container_host)"""
        self._ensure_image(container_host)
        if not kwargs.get("name"):
            kwargs["name"] = self._gen_name()
        kwargs["ports"] = self._port_mapping(container_host, **kwargs)

        envars = kwargs.get('environment', {})
        if isinstance(envars, str):
            envars = {var.split('=')[0]: var.split('=')[1] for var in envars.split(',')}
        # add some context information about the container's requester
        origin = helpers.find_origin()

        if "for" in origin:
            origin = origin.split()[-1]
        envars["BROKER_ORIGIN"] = origin[0]
        if origin[1]:
            envars["JENKINS_URL"] = origin[1]
        kwargs["environment"] = envars
        kwargs["labels"] = envars
        container_inst = self.runtime.create_container(container_host, **kwargs)
        container_inst.start()
        return container_inst

    def execute(self, container_app, **kwargs):
        """Run a container and return the raw results"""
        return self.runtime.execute(container_app, **kwargs)

    def run_wait_container(self, image_name, **kwargs):
        cont_inst = self.run_container(image_name, **kwargs)
        cont_inst.wait(condition="excited")
        return self.runtime.get_logs(cont_inst)

    def _gen_name(self):
        return f"{self._name_prefix}_{str(uuid4()).split('-')[0]}"
