import json
import pytest
from broker.broker import Broker
from broker.providers.ansible_tower import AnsibleTower
from broker.helpers import MockStub


class AwxkitApiStub(MockStub):
    """This class stubs out the methods of the awxkit Api class

    stubbing for:
     - root.load_session.get()
     - root.available_versions.v2.get()
     - v2.ping.get().version
     - v2.jobs.get(id=child_id).results.pop()
     - v2.workflow_job_templates.get(name=workflow).results.pop()
     - wfjt.launch(payload={"extra_vars": str(kwargs).replace("--", "")})
     - job.wait_until_completed()
     - merge_dicts(artifacts, at_object.artifacts)
     - at_object.get_related("workflow_nodes").results
    """

    def __init__(self, **kwargs):
        if "job_id" in kwargs:
            # we're a job, so load in job information
            super().__init__(self._load_job(kwargs.pop("job_id")))
        elif "name" in kwargs:
            # workflow job template lookup
            super().__init__(self._load_workflow(kwargs.pop("name")))
        else:
            super().__init__()
        self.version = "3.7.1"

    @staticmethod
    def _load_job(job_id):
        with open("tests/data/ansible_tower/fake_jobs.json") as job_file:
            job_data = json.load(job_file)
        for job in job_data:
            if job["id"] == job_id:
                return job

    @staticmethod
    def _load_workflow(workflow_name):
        with open("tests/data/ansible_tower/fake_workflows.json") as workflow_file:
            workflow_data = json.load(workflow_file)
        for workflow in workflow_data:
            if workflow["name"] == workflow_name:
                return workflow

    def get_related(self, related=None):
        with open("tests/data/ansible_tower/fake_children.json") as child_file:
            child_data = json.load(child_file)
        return MockStub({"results": [MockStub(child) for child in child_data]})

    def get(self, *args, **kwargs):
        if "id" in kwargs:
            # requesting a job by id
            return AwxkitApiStub(job_id=kwargs.pop("id"))
        if "name" in kwargs:
            # requesting a workflow job template by name
            return AwxkitApiStub(name=kwargs.pop("name"))
        return self

    def launch(self, payload={}):
        return AwxkitApiStub(job_id=343, **payload)

    def pop(self, item=None):
        """awxkit uses pop() on objects, this allows for that and normal use"""
        if not item:
            return self
        else:
            return super().pop(item)


@pytest.fixture
def api_stub():
    return AwxkitApiStub()


@pytest.fixture
def config_stub():
    return MockStub()


@pytest.fixture
def tower_stub(api_stub, config_stub):
    return AnsibleTower(root=api_stub, config=config_stub)


def test_execute(tower_stub):
    job = tower_stub.execute(workflow="deploy-rhel")
    assert "workflow_nodes" in job.related


def test_host_creation(tower_stub):
    bx = Broker()
    job = tower_stub.execute(workflow="deploy-rhel")
    host = tower_stub.construct_host(job, bx.host_classes)
    assert isinstance(host, bx.host_classes["host"])
    assert host.hostname == "fake.host.test.com"
    assert host.os_distribution_version == "9.4"


def test_workflow_lookup_failure(tower_stub):
    with pytest.raises(Broker.UserError) as err:
        tower_stub.execute(workflow="this-does-not-exist")
    assert "Workflow not found by name: this-does-not-exist" in err.value.message


def test_host_release_dual_params(tower_stub):
    bx = Broker()
    job = tower_stub.execute(workflow="deploy-rhel")
    host = tower_stub.construct_host(job, bx.host_classes)
    host._broker_args["source_vm"] = "fake-physical-host"
    assert host._broker_args["source_vm"] == host.name
    host.release()
