from dynaconf import settings
from logzero import logger

try:
    from awxkit import api, config
except:
    logger.error("Unable to import awxkit. Is it installed?")
    raise Exception("Unable to import awxkit. Is it installed?")

from broker.providers import Provider
from broker.helpers import merge_dicts, flatten_dict

AT_URL = settings.ANSIBLETOWER.base_url
UNAME = settings.ANSIBLETOWER.username
PWORD = settings.ANSIBLETOWER.password
RELEASE_WORKFLOW = settings.ANSIBLETOWER.release_workflow


class AnsibleTower(Provider):
    def __init__(self, **kwargs):
        self._construct_params = []
        config.base_url = AT_URL
        config.credentials = {"default": {"username": UNAME, "password": PWORD}}
        config.use_sessions = True
        root = api.Api()
        root.load_session().get()
        self.v2 = root.available_versions.v2.get()

    def _host_release(self):
        self._at_inst.release(self)

    def _set_attributes(self, host_inst, broker_args=None):
        host_inst.__dict__.update(
            {
                "release": self._host_release,
                "_at_inst": self,
                "_broker_provider": "AnsibleTower",
                "_broker_args": broker_args,
            }
        )

    def _merge_artifacts(self, at_object, strategy="latest", artifacts=None):
        """Gather and merge all artifacts associated with an object and its children
        strategies:
           - latest: overwite existing values with newer values
           - branch: each branched child gets its own sub-dictionary (todo)
           - min-branch: only branch children if conflict is detected (todo)
        """
        logger.debug(f"Attempting to merge: {at_object.name}")
        if not artifacts:
            artifacts = {}
        if getattr(at_object, "artifacts", None):
            logger.debug(f"Found artifacts: {at_object.artifacts}")
            if strategy == "latest":
                artifacts = merge_dicts(artifacts, at_object.artifacts)
        if "workflow_nodes" in at_object.related:
            children = at_object.get_related("workflow_nodes").results
            for child in children:
                if child.type == "workflow_job_node":
                    child_id = child.summary_fields.job.id
                    child_obj = self.v2.jobs.get(id=child_id).results.pop()
                    artifacts = self._merge_artifacts(child_obj, strategy, artifacts)
        return artifacts

    def construct_host(self, provider_params, host_classes, **kwargs):
        job = provider_params
        job_attrs = self._merge_artifacts(
            job, strategy=kwargs.get("strategy", "latest")
        )
        job_attrs = flatten_dict(job_attrs)
        logger.debug(job_attrs)
        hostname, host_type = None, "host"
        for key, value in job_attrs.items():
            if key.endswith("fqdn"):
                hostname = value if not isinstance(value, list) else value[0]
            if key.endswith("host_type"):
                host_type = value
        logger.debug(f"hostname: {hostname}, host type: {host_type}")
        if hostname:
            host_inst = host_classes[host_type](hostname=hostname, **kwargs)
            self._set_attributes(host_inst, broker_args=kwargs)
            return host_inst
        raise Exception(f"No hostname found in job attributes:\n{job_attrs}")

    def exec_workflow(self, **kwargs):
        workflow = kwargs.get("workflow")
        wfjt = self.v2.workflow_job_templates.get(name=workflow).results.pop()
        job = wfjt.launch(payload={"extra_vars": str(kwargs).replace("--", "")})
        job.wait_until_completed()
        assert job.status == "successful"
        return job

    def release(self, host_obj):
        return self.exec_workflow(RELEASE_WORKFLOW, **host_obj.to_dict())
