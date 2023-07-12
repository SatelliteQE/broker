import json
import pytest
from pathlib import Path
from broker.providers.beaker import Beaker
from broker.binds.beaker import _curate_job_info
from broker.helpers import MockStub
from broker.hosts import Host


class BeakerBindStub(MockStub):
    """This class stubs out the methods of the Beaker bind

    stubbing for:
     - self.runtime.jobid_from_system(caller_host.hostname)
     - self.runtime.release(caller_host.hostname, job_id) # no-op
     - self.runtime.system_details_curated(host)
     - self.runtime.execute_job(job_xml, max_wait)
     - self.runtime.job_clone(job, prettyxml=True, dryrun=True).stdout
     - self.runtime.system_release(host_name) # no-op
     - self.runtime.job_cancel(job_id) # no-op
     - self.runtime.job_list(**kwargs).stdout.splitlines()
     - self.runtime.user_systems()
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.job_id = "1234567"
        self.stdout = "1234567\n7654321\n"

    def jobid_from_system(self, hostname):
        return self.job_id

    def system_details_curated(self, host):
        return {
            "hostname": "test.example.com",
            "job_id": self.job_id,
            "mac_address": "00:00:00:00:00:00",
            "owner": "testuser <tuser@test.com>",
            "id": "7654321",
            "reservation_id": "1267",
            "reserved_on": "2023-01-01 00:00:00",
            "expires_on": "2025-01-01 00:00:00",
            "reserved_for": "anotheruser <auser@test.com>",
        }

    def execute_job(self, job_xml, max_wait):
        return _curate_job_info(json.loads(Path("tests/data/beaker/job_result.json").read_text()))

    def user_systems(self):
        return ["test.example.com", "test2.example.com"]


@pytest.fixture
def bind_stub():
    return BeakerBindStub()


@pytest.fixture
def beaker_stub(bind_stub):
    return Beaker(bind=bind_stub)


def test_empty_init():
    assert Beaker()


def test_host_creation(beaker_stub):
    job_res = beaker_stub.submit_job("tests/data/beaker/test_job.xml")
    host = beaker_stub.construct_host(job_res, {"host": Host})
    assert isinstance(host, Host)
    assert host.hostname == "fake.host.testdom.com"
