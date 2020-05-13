import json
import pytest
from broker.broker import HOST_CLASSES
from broker.providers.ansible_tower import AnsibleTower
from broker.helpers import MockStub


class AwxkitApiStub(MockStub):
    """This class stubs out the methods of the awxkit Api class

    stubbing for:
     - root.load_session.get()
     - root.available_versions.v2.get()
     - v2.jobs.get(id=child_id).results.pop()
     - v2.workflow_job_templates.get(name=workflow).results.pop()
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

    def _load_job(self, job_id):
        with open("tests/data/ansible_tower/fake_jobs.json") as job_file:
            job_data = json.load(job_file)
        for job in job_data:
            if job["id"] == job_id:
                return job

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
            return AwxkitApiStub()
        return self

    def launch(self, payload={}):
        return AwxkitApiStub(job_id=343, **payload)

    def pop(self, item=None):
        """awxkit uses pop() on objects, this allows for that and normal use"""
        if not item:
            return self
        else:
            return super().pop(item)


@pytest.fixture(scope="function")
def api_stub():
    yield AwxkitApiStub()


@pytest.fixture(scope="function")
def config_stub():
    yield MockStub()


def test_exec_workflow(api_stub, config_stub):
    at_inst = AnsibleTower(root=api_stub, config=config_stub)
    job = at_inst.exec_workflow(workflow="deploy-base-rhel")
    assert "workflow_nodes" in job.related


def test_host_creation(api_stub, config_stub):
    at_inst = AnsibleTower(root=api_stub, config=config_stub)
    job = at_inst.exec_workflow(workflow="deploy-base-rhel")
    host = at_inst.construct_host(job, HOST_CLASSES)
    assert isinstance(host, HOST_CLASSES["host"])
    assert host.hostname == "fake.host.test.com"
