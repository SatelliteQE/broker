import inspect
import json
import sys
from urllib import parse as url_parser
from broker.settings import settings
from broker.helpers import results_filter
from logzero import logger
from datetime import datetime

try:
    import awxkit
except:
    logger.error("Unable to import awxkit. Is it installed?")
    raise Exception("Unable to import awxkit. Is it installed?")

from broker.providers import Provider
from broker import helpers

AT_URL = settings.ANSIBLETOWER.base_url
UNAME = settings.ANSIBLETOWER.get("username")
PWORD = settings.ANSIBLETOWER.get("password")
TOKEN = settings.ANSIBLETOWER.get("token")
RELEASE_WORKFLOW = settings.ANSIBLETOWER.release_workflow
EXTEND_WORKFLOW = settings.ANSIBLETOWER.extend_workflow
AT_TIMEOUT = settings.ANSIBLETOWER.workflow_timeout


class AnsibleTower(Provider):
    def __init__(self, **kwargs):
        self._construct_params = []
        config = kwargs.get("config", awxkit.config)
        config.base_url = AT_URL
        # Prefer token if its set, otherwise use username/password
        # auth paths for the API taken from:
        # https://github.com/ansible/awx/blob/ddb6c5d0cce60779be279b702a15a2fddfcd0724/awxkit/awxkit/cli/client.py#L85-L94
        # unit test mock structure means the root API instance can't be loaded on the same line
        root = kwargs.get("root")
        if root is None:
            root = awxkit.api.Api()  # support mock stub for unit tests
        if TOKEN:
            logger.info("Using token authentication")
            config.token = TOKEN
            root.connection.login(
                username=None, password=None, token=TOKEN, auth_type="Bearer"
            )
            versions = root.get().available_versions
            try:
                # lookup the user that authenticated with the token
                # If a username was specified in config, use that instead
                my_username = UNAME or versions.v2.get().me.get().results[0].username
            except (IndexError, AttributeError):
                # lookup failed for whatever reason
                logger.error(
                    "Failed to lookup a username for the given token, please check credentials"
                )
                sys.exit()
        else:  # dynaconf validators should have checked that either token or password was provided
            logger.info("Using username and password authentication")
            config.credentials = {"default": {"username": UNAME, "password": PWORD}}
            config.use_sessions = True
            root.load_session().get()
            versions = root.available_versions
            my_username = UNAME
        self.v2 = versions.v2.get()
        self.username = my_username

    @staticmethod
    def _pull_params(kwargs):
        """Given a kwarg dict, separate AT-specific parameters from other kwargs
        AT-specific params must stat with double underscores.
        Example: __page_size
        """
        params, new_kwargs = {}, {}
        for key, value in kwargs.items():
            if key.startswith("__"):
                params[key[2:]] = value
            else:
                new_kwargs[key] = value
        return params, new_kwargs

    def _host_release(self):
        caller_host = inspect.stack()[1][0].f_locals["host"]
        self.release(caller_host.name)

    def _set_attributes(self, host_inst, broker_args=None):
        host_inst.__dict__.update(
            {
                "release": self._host_release,
                "_at_inst": self,
                "_broker_provider": "AnsibleTower",
                "_broker_args": broker_args,
            }
        )

    def _merge_artifacts(self, at_object, strategy="last", artifacts=None):
        """Gather and merge all artifacts associated with an object and its children

        :param at_object: object you want to merge

        :param strategy:
            strategies:
               - merge: merge artifact dictionaries together
               - branch: each branched child gets its own sub-dictionary (todo)
               - min-branch: only branch children if conflict is detected (todo)

        :param artifacts: default to none

        :return: dictionary of merged artifact, used for constructing host
        """
        logger.debug(f"Attempting to merge: {at_object.name}")
        if not artifacts:
            artifacts = {}
        if getattr(at_object, "artifacts", None):
            logger.debug(f"Found artifacts: {at_object.artifacts}")
            if strategy == "merge":
                artifacts = helpers.merge_dicts(artifacts, at_object.artifacts)
            elif strategy == "last":
                artifacts = at_object.artifacts
        if "workflow_nodes" in at_object.related:
            children = at_object.get_related("workflow_nodes").results
            # filter out children with no associated job
            children = list(
                filter(
                    lambda child: getattr(child.summary_fields, "job", None), children
                )
            )
            children.sort(key=lambda child: child.summary_fields.job.id)
            if strategy == "last":
                children = children[-1:]
            for child in children:
                if child.type == "workflow_job_node":
                    logger.debug(child)
                    child_id = child.summary_fields.job.id
                    child_obj = self.v2.jobs.get(id=child_id).results
                    if child_obj:
                        child_obj = child_obj.pop()
                        artifacts = (
                            self._merge_artifacts(child_obj, strategy, artifacts)
                            or artifacts
                        )
                    else:
                        logger.warning(
                            f"Unable to pull information from child job with id {child_id}."
                        )
        return artifacts

    def _get_expire_date(self, host_id):
        try:
            time_stamp = (
                self.v2.hosts.get(id=host_id)
                .results[0]
                .related.ansible_facts.get()
                .expire_date
            )
            return str(datetime.fromtimestamp(int(time_stamp)))
        except:
            return None

    def _compile_host_info(self, host):
        host_info = {
            "name": host.name,
            "type": host.type,
            "hostname": host.variables["fqdn"],
            "_broker_provider": "AnsibleTower",
            "_broker_args": getattr(host, "_broker_args", {})
        }
        expire_time = self._get_expire_date(host.id)
        if expire_time:
            host_info["expire_time"] = expire_time
        if "last_job" in host.related:
            job_vars = json.loads(host.get_related("last_job").extra_vars)
            host_info["_broker_args"].update({
                arg: val
                for arg, val in job_vars.items()
                if val and isinstance(val, str)
            })
            try:
                host_info["_broker_args"]["workflow"] = host.get_related(
                    "last_job"
                ).summary_fields.source_workflow_job.name
            except:
                logger.warning(
                    f"Unable to determine workflow for {host_info['hostname']}"
                )
        return host_info

    def construct_host(self, provider_params, host_classes, **kwargs):
        """Constructs host to be read by Ansible Tower

        :param provider_params: dictionary of what the provider returns when initially
        creating the vm

        :param host_classes: host object

        :return: broker object of constructed host instance
        """
        if provider_params:
            job = provider_params
            job_attrs = self._merge_artifacts(
                job, strategy=kwargs.get("strategy", "merge")
            )
            # pull information about the job arguments
            job_extra_vars = json.loads(job.extra_vars)
            # and update them if they have resolved values
            for key in job_extra_vars.keys():
                job_extra_vars[key] = job_attrs.get(key)
            kwargs.update({key: val for key, val in job_extra_vars.items() if val})
            job_attrs = helpers.flatten_dict(job_attrs)
            logger.debug(job_attrs)
            hostname, name, host_type = None, None, "host"
            for key, value in job_attrs.items():
                if key.endswith("fqdn") and not hostname:
                    hostname = value if not isinstance(value, list) else value[0]
                if key in ("name", "vm_provisioned") and not name:
                    name = value if not isinstance(value, list) else value[0]
                if key.endswith("host_type"):
                    host_type = value if value in host_classes else host_type
            if not hostname:
                raise Exception(f"No hostname found in job attributes:\n{job_attrs}")
            logger.debug(f"hostname: {hostname}, name: {name}, host type: {host_type}")
            host_inst = host_classes[host_type](hostname=hostname, name=name, **kwargs)
        else:
            host_inst = host_classes[kwargs.get("type")](**kwargs)
        self._set_attributes(host_inst, broker_args=kwargs)
        return host_inst

    def execute(self, **kwargs):
        """Execute workflow or job template in Ansible Tower

        :param kwargs: workflow or job template name passed in a string

        :return: dictionary containing all information about executed workflow/job template
        """
        if (name := kwargs.get("workflow")):
            subject = "workflow"
            get_path = self.v2.workflow_job_templates
        elif (name := kwargs.get("job_template")):
            subject = "job_template"
            get_path = self.v2.job_templates
        else:
            logger.error(f"No workflow or job template specified")
            return
        candidates = get_path.get(name=name).results
        if candidates:
            target = candidates.pop()
        else:
            logger.error(f"{subject.capitalize()} not found by name: {name}")
            return
        logger.debug(
            f"Launching {subject}: {url_parser.urljoin(AT_URL, str(target.url))}"
        )
        job = target.launch(payload={"extra_vars": str(kwargs).replace("--", "")})
        job_number = job.url.rstrip("/").split("/")[-1]
        job_ui_url = url_parser.urljoin(AT_URL, f"/#/{subject}s/{job_number}")
        logger.info(
            f"Waiting for job: \nAPI: {url_parser.urljoin(AT_URL, str(job.url))}\nUI: {job_ui_url}"
        )
        job.wait_until_completed(timeout=AT_TIMEOUT)
        if not job.status == "successful":
            logger.error(
                f"{subject.capitalize()} Status: {job.status}\nExplanation: {job.job_explanation}"
            )
            return
        if (artifacts := kwargs.get("artifacts")):
            del kwargs["artifacts"]
            return self._merge_artifacts(job, strategy=artifacts)
        return job

    def get_inventory(self, user=None):
        """Compile a list of hosts based on any inventory a user's name is mentioned"""
        user = user or self.username
        invs = [
            inv
            for inv in self.v2.inventory.get(page_size=100).results
            if user in inv.name
        ]
        hosts = []
        for inv in invs:
            inv_hosts = inv.get_related("hosts", page_size=200).results
            hosts.extend(inv_hosts)
        return [self._compile_host_info(host) for host in hosts]

    def extend_vm(self, target_vm):
        """Run the extend workflow with defaults args

        :param target_vm: This will likely be the vm name
        """
        return self.execute(workflow=EXTEND_WORKFLOW, target_vm=target_vm)

    def nick_help(self, **kwargs):
        """Get a list of extra vars and their defaults from a workflow"""
        results_limit = kwargs.get("results_limit", settings.ANSIBLETOWER.results_limit)
        if (workflow := kwargs.get("workflow")) :
            wfjt = self.v2.workflow_job_templates.get(name=workflow).results.pop()
            logger.info(
                f"Accepted additional nick fields:\n{helpers.yaml_format(wfjt.extra_vars)}"
            )
        elif kwargs.get("workflows"):
            workflows = [
                workflow.name
                for workflow in self.v2.workflow_job_templates.get().results
            ]
            if (res_filter := kwargs.get("results_filter")) :
                workflows = results_filter(workflows, res_filter)
            workflows = "\n".join(workflows[:results_limit])
            logger.info(f"Available workflows:\n{workflows}")
        elif (job_template := kwargs.get("job_template")) :
            jt = self.v2.job_templates.get(name=job_template).results.pop()
            logger.info(
                f"Accepted additional nick fields:\n{helpers.yaml_format(jt.extra_vars)}"
            )
        elif kwargs.get("job_templates"):
            job_templates = [
                job_template.name
                for job_template in self.v2.job_templates.get().results
            ]
            if (res_filter := kwargs.get("results_filter")) :
                job_templates = results_filter(job_templates, res_filter)
            job_templates = "\n".join(job_templates[:results_limit])
            logger.info(f"Available job templates:\n{job_templates}")
        elif kwargs.get("templates"):
            templates = list({
                tmpl
                for tmpl in self.execute(
                    workflow="list-templates", artifacts="last"
                )["data_out"]["list_templates"]
            })
            templates.sort(reverse=True)
            if (res_filter := kwargs.get("results_filter")) :
                templates = results_filter(templates, res_filter)
            templates = "\n".join(templates[:results_limit])
            logger.info(f"Available templates:\n{templates}")
        else:
            logger.warning("That action is not yet implemented.")

    def release(self, name):
        return self.execute(workflow=RELEASE_WORKFLOW, source_vm=name)
