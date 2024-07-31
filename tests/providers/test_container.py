import json
from pathlib import Path
import pytest
from broker.broker import Broker
from broker.helpers import MockStub
from broker.providers.container import Container
from broker.settings import settings


class ContainerApiStub(MockStub):
    """This class stubs out the methods of the Container API client

    stubbing for:
     - self.runtime.images
     - self.runtime.pull_image(name)
     - self.runtime.image_info(image)["config"]["ExposedPorts"].items()
     - self.runtime.containers
     - self.runtime.get_attrs(cont_inst)
     - helpers.yaml_format(self.runtime.image_info(image))
     - self.runtime.create_container(container_host, **kwargs)
     - self.runtime.get_logs(cont_inst)
    """

    def __init__(self, **kwargs):
        in_dict = {
            "images": [MockStub({"tags": "ch-d:ubi8"})],  # self.runtime.images
            "containers": [MockStub({"tags": "f37d3058317f"})],  # self.runtime.containers
            "name": "f37d3058317f",  # self.runtime.get_attrs(cont_inst)["name"]
            "ports": MockStub({'22/tcp': [{'HostIp': '', 'HostPort': '1337'}]}),  # self.runtime.get_attrs(cont_inst)["ports"]
        }
        if "job_id" in kwargs:
            # we need to load in an image object
            super().__init__(self._load_image(kwargs.pop("job_id")))
        elif "name" in kwargs:
            # we need to load in a container object
            super().__init__(self._load_container(kwargs.pop("name")))
        else:
            super().__init__(in_dict=in_dict, **kwargs)

    @property
    def networks(self):
        return [MockStub(obj) for obj in json.loads(Path("tests/data/container/fake_networks.json").read_text())]

    def get_network_by_attrs(self, attr_dict):
        """Return the first matching network that matches all attr_dict keys and values."""
        for network in self.networks:
            if all(network.attrs.get(k) == v for k, v in attr_dict.items()):
                return network

    @staticmethod
    def pull_image(tag_name):
        with open("tests/data/container/fake_images.json") as image_file:
            image_data = json.load(image_file)
        for image in image_data:
            if tag_name in image["RepoTags"]:
                return MockStub(image)
        raise Broker.ProviderError(f"Unable to find image: {tag_name}")

    def create_container(self, container_host, **kwargs):
        if net_name := settings.container.network:
            net_dict = {}
            for name in net_name.split(","):
                if not self.get_network_by_attrs({"name": name}):
                    raise Exception(
                        f"Network '{name}' not found on container host."
                    )
                net_dict[name] = {"NetworkId": name}
            kwargs["networks"] = net_dict
        with open("tests/data/container/fake_containers.json") as container_file:
            container_data = json.load(container_file)
        image_data = self.pull_image(container_host)
        for container in container_data:
            if container["Config"]["Image"] == image_data.RepoTags[0]:
                container["id"] = container["Id"]  # hostname = cont_inst.id[:12]
                container["kwargs"] = kwargs
                return MockStub(container)


@pytest.fixture
def api_stub():
    return ContainerApiStub()


@pytest.fixture
def container_stub(api_stub):
    return Container(bind=api_stub)


def test_empty_init():
    assert Container()


def test_host_creation(container_stub):
    bx = Broker()
    cont = container_stub.run_container(container_host="ch-d:ubi8")
    host = container_stub.construct_host(cont, bx.host_classes)
    assert isinstance(host, bx.host_classes["host"])
    assert host.hostname == "f37d3058317f"


def test_single_network(container_stub):
    settings.container.network = "podman2"
    cont = container_stub.run_container(container_host="ch-d:ubi8")
    assert cont.kwargs["networks"] == {'podman2': {'NetworkId': 'podman2'}}


def test_multiple_networks(container_stub):
    settings.container.network = "podman1,podman2"
    cont = container_stub.run_container(container_host="ch-d:ubi8")
    assert cont.kwargs["networks"] == {'podman1': {'NetworkId': 'podman1'}, 'podman2': {'NetworkId': 'podman2'}}


def test_image_lookup_failure(container_stub):
    with pytest.raises(Broker.ProviderError) as err:
        container_stub.run_container(container_host="this-does-not-exist")
    assert "Unable to find image: this-does-not-exist" in err.value.message
