"""Container provider implementation."""

from functools import cache
import getpass
import inspect
from uuid import uuid4

import click
from dynaconf import Validator
from logzero import logger

from broker import exceptions, helpers
from broker.binds import containers
from broker.providers import Provider
from broker.settings import settings


def container_info(container_inst):
    """Return a dict of container information."""
    attr_dict = {"container_host": "Image", "_broker_origin": "Labels/broker.origin"}
    info = {
        "_broker_provider": "Container",
        "_broker_args": helpers.dict_from_paths(container_inst.attrs, attr_dict),
        "name": container_inst.name,
        "hostname": container_inst.attrs["Config"].get("Hostname"),
        "image": container_inst.image.tags,
        "ports": container_inst.ports,
    }
    try:
        info["status"] = container_inst.status
    except TypeError:
        info["status"] = container_inst.attrs["State"]
    return info


def _host_release():
    caller_host = inspect.stack()[1][0].f_locals["host"]
    if not caller_host._cont_inst:
        caller_host._cont_inst = caller_host._prov_inst._cont_inst_by_name(caller_host.name)
    caller_host._cont_inst.remove(v=True, force=True)
    caller_host._checked_in = True


@cache
def get_runtime(runtime_cls=None, host=None, username=None, password=None, port=None, timeout=None):
    """Return a runtime instance."""
    return runtime_cls(
        host=host,
        username=username,
        password=password,
        port=port,
        timeout=timeout,
    )


@Provider.auto_hide
class Container(Provider):
    """Container provider class providing a Broker interface around the container binds."""

    _validators = [
        Validator("CONTAINER.runtime", default="podman"),
        Validator("CONTAINER.host", default="localhost"),
        Validator("CONTAINER.host_username", default="root"),
        Validator(
            "CONTAINER.host_password",
        ),
        Validator("CONTAINER.host_port", default=2375),
        Validator("CONTAINER.network", default=None),
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

    _sensitive_attrs = ["password", "host_password"]

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

    def _ensure_image(self, name):
        """Check if an image exists on the provider, attempt a pull if not."""
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
            ) from err

    @staticmethod
    def _find_ssh_port(port_map, ssh_port=22):
        """Go through container port map and find the mapping that corresponds to port 22."""
        if isinstance(port_map, list):
            # [{'hostPort': 1337, 'containerPort': 22, 'protocol': 'tcp', 'hostIP': ''},
            for pm in port_map:
                if pm["containerPort"] == ssh_port:
                    return pm["hostPort"]
        elif isinstance(port_map, dict):
            # {'22/tcp': [{'HostIp': '', 'HostPort': '1337'}],
            for key, val in port_map.items():
                if key.startswith("22") and isinstance(val, list):
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
        """Create a mapping of ports to expose on the container.

        Accepted `ports` formats:
            22
            22:1337
            22/tcp
            22/tcp:1337
            22,23
            22:1337 23:1335.
        """
        mapping = {}
        # create mapping for all exposed ports in the image
        if settings.container.auto_map_ports or kwargs.get("auto_map_ports"):
            mapping = {
                k: v or None
                for k, v in self.runtime.image_info(image)["config"]["ExposedPorts"].items()
            }
        # add any ports that were passed as arguments
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
                        s = "tcp"
                    mapping[f"{p}/{s}"] = int(h) if h else None
        return mapping

    def _cont_inst_by_name(self, cont_name):
        """Attempt to find and return a container by its name."""
        for cont_inst in self.runtime.containers:
            if cont_inst.name == cont_name:
                return cont_inst
        logger.error(f"Unable to find container by name {cont_name}")

    def construct_host(self, provider_params, host_classes, **kwargs):
        """Construct a broker host from a container instance.

        :param provider_params: a container instance object

        :param host_classes: host object

        :return: broker object of constructed host instance
        """
        logger.debug(f"constructing with {provider_params=}\n{host_classes=}\n{kwargs=}")
        if not provider_params:
            host_inst = host_classes[kwargs.get("type", "host")](**kwargs)
            cont_inst = self._cont_inst_by_name(host_inst.name)
            self._set_attributes(host_inst, broker_args=kwargs, cont_inst=cont_inst)
            return host_inst
        cont_inst = provider_params
        cont_attrs = self.runtime.get_attrs(cont_inst)
        logger.debug(cont_attrs)
        hostname = cont_inst.attrs["Config"].get("Hostname")
        if port := self._find_ssh_port(cont_attrs["ports"]):
            hostname = f"{self.runtime.host}:{port}"
        if not hostname:
            raise Exception(f"Could not determine container hostname:\n{cont_attrs}")
        name = cont_attrs["name"]
        logger.debug(f"hostname: {hostname}, name: {name}, host type: host")
        host_inst = host_classes["host"](**{**kwargs, "hostname": hostname, "name": name})
        self._set_attributes(host_inst, broker_args=kwargs, cont_inst=cont_inst)
        # add the container's port mapping to the host instance only if there are any ports open
        if cont_attrs.get("ports"):
            host_inst.exposed_ports = {
                f"{k.split('/')[0]}": v[0]["HostPort"] for k, v in cont_attrs["ports"].items() if v
            }
        return host_inst

    def provider_help(
        self, container_hosts=False, container_host=None, container_apps=False, **kwargs
    ):
        """Return useful information about container images."""
        results_limit = kwargs.get("results_limit", settings.container.results_limit)
        if container_host:
            logger.info(
                f"Information for {container_host} container-host:\n"
                f"{helpers.yaml_format(self.runtime.image_info(container_host))}"
            )
        elif container_hosts:
            images = [
                img.tags[0]
                for img in self.runtime.images
                if img.labels.get("broker_compatible") and img.tags
            ]
            if res_filter := kwargs.get("results_filter"):
                images = helpers.eval_filter(images, res_filter, "res")
                images = images if isinstance(images, list) else [images]
            images = "\n".join(images[:results_limit])
            logger.info(f"Available host images:\n{images}")
        elif container_apps:
            images = [img.tags[0] for img in self.runtime.images if img.tags]
            if res_filter := kwargs.get("results_filter"):
                images = helpers.eval_filter(images, res_filter, "res")
                images = images if isinstance(images, list) else [images]
            images = "\n".join(images[:results_limit])
            logger.info(f"Available app images:\n{images}")

    def get_inventory(self, name_prefix):
        """Get all containers that have a matching name prefix."""
        name_prefix = name_prefix or self._name_prefix
        return [
            container_info(cont)
            for cont in self.runtime.containers
            if cont.name.startswith(name_prefix)
        ]

    def extend(self):
        """There is no need to extend a continer-ased host."""

    def release(self, host_obj):
        """Remove a container-based host from the container host."""
        host_obj._cont_inst.remove(force=True)

    @Provider.register_action("container_host")
    def run_container(self, container_host, **kwargs):
        """Start a container based on an image name (container_host)."""
        self._ensure_image(container_host)
        if not kwargs.get("name"):
            kwargs["name"] = self._gen_name()
        kwargs["ports"] = self._port_mapping(container_host, **kwargs)

        envars = kwargs.get("environment", {})
        if isinstance(envars, str):
            envars = {var.split("=")[0]: var.split("=")[1] for var in envars.split(",")}
        # add some context information about the container's requester
        origin = helpers.find_origin()

        if "for" in origin:
            origin = origin.split()[-1]
        envars["BROKER_ORIGIN"] = origin[0]
        if origin[1]:
            envars["JENKINS_URL"] = origin[1]
        kwargs["environment"] = envars

        # process eventual provider labels for each setting level
        kwargs["provider_labels"] = kwargs.get("provider_labels", {})
        kwargs["provider_labels"].update(settings.get("provider_labels", {}))
        kwargs["provider_labels"].update(settings.CONTAINER.get("provider_labels", {}))
        # prefix eventual label keys with 'broker.' to conform to the docker guidelines
        # https://docs.docker.com/config/labels-custom-metadata/#key-format-recommendations
        kwargs["provider_labels"] = {
            f"broker.{label[0]}": label[1] for label in kwargs.get("provider_labels", {}).items()
        }
        # process eventual labels that were passed externally, split by "="
        kwargs["provider_labels"].update(
            {"broker.origin": origin[0], "broker.jenkins.url": origin[1]}
        )
        # rename the dict key to the name of the arg recognized by provider
        kwargs["labels"] = kwargs.pop("provider_labels")
        container_inst = self.runtime.create_container(container_host, **kwargs)
        container_inst.start()
        return container_inst

    @Provider.register_action("container_app")
    def execute(self, container_app, **kwargs):
        """Run a container and return the raw results."""
        if not kwargs.get("name"):
            kwargs["name"] = self._gen_name()
        return self.runtime.execute(container_app, **kwargs)

    def run_wait_container(self, image_name, **kwargs):
        """Run a container and wait for it to exit."""
        cont_inst = self.run_container(image_name, **kwargs)
        cont_inst.wait(condition="excited")
        return self.runtime.get_logs(cont_inst)

    def _gen_name(self):
        return f"{self._name_prefix}_{str(uuid4()).split('-')[0]}"
