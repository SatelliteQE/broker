import json
import pytest
from broker.broker import Broker
from broker.providers.container import Container
from broker.helpers import MockStub


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
            "containers": [
                MockStub({"tags": "f37d3058317f"})
            ],  # self.runtime.containers
            "name": "f37d3058317f",  # self.runtime.get_attrs(cont_inst)["name"]
        }
        if "job_id" in kwargs:
            # we need to load in an image object
            super().__init__(self._load_image(kwargs.pop("job_id")))
        elif "name" in kwargs:
            # we need to load in a container object
            super().__init__(self._load_container(kwargs.pop("name")))
        else:
            super().__init__(in_dict=in_dict, **kwargs)

    @staticmethod
    def pull_image(tag_name):
        with open("tests/data/container/fake_images.json") as image_file:
            image_data = json.load(image_file)
        for image in image_data:
            if tag_name in image["RepoTags"]:
                return MockStub(image)
        raise Broker.ProviderError(f"Unable to find image: {tag_name}")

    def create_container(self, container_host, **kwargs):
        with open("tests/data/container/fake_containers.json") as container_file:
            container_data = json.load(container_file)
        image_data = self.pull_image(container_host)
        for container in container_data:
            if container["Config"]["Image"] == image_data.RepoTags[0]:
                container["id"] = container["Id"]  # hostname = cont_inst.id[:12]
                return MockStub(container)


@pytest.fixture(scope="function")
def api_stub():
    yield ContainerApiStub()


@pytest.fixture(scope="function")
def container_stub(api_stub):
    yield Container(bind=api_stub)


def test_empty_init():
    assert Container()


def test_host_creation(container_stub):
    bx = Broker()
    cont = container_stub.run_container(container_host="ch-d:ubi8")
    host = container_stub.construct_host(cont, bx.host_classes)
    assert isinstance(host, bx.host_classes["host"])
    assert host.hostname == "f37d3058317f"


def test_image_lookup_failure(container_stub):
    with pytest.raises(Broker.ProviderError) as err:
        container_stub.run_container(container_host="this-does-not-exist")
    assert "Unable to find image: this-does-not-exist" in err.value.message
