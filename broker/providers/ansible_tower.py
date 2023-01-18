import click
import inspect
import json
import yaml
from urllib import parse as url_parser
from functools import cache, cached_property
from dynaconf import Validator
from broker import exceptions
from broker.helpers import find_origin, results_filter
from broker.settings import settings
from logzero import logger
from datetime import datetime

try:
    import awxkit
except ImportError:
    raise exceptions.ProviderError(
        provider="AnsibleTower", message="Unable to import awxkit. Is it installed?"
    )

from broker.providers import Provider
from broker import helpers


@cache
def get_awxkit_and_uname(
    config=None, root=None, url=None, token=None, uname=None, pword=None
):
    """Return an awxkit api object and resolved username"""
    # Prefer token if its set, otherwise use username/password
    # auth paths for the API taken from:
    # https://github.com/ansible/awx/blob/ddb6c5d0cce60779be279b702a15a2fddfcd0724/awxkit/awxkit/cli/client.py#L85-L94
    # unit test mock structure means the root API instance can't be loaded on the same line
    config = config or awxkit.config
    config.base_url = url
    if root is None:
        root = awxkit.api.Api()  # support mock stub for unit tests
    if token:
        helpers.emit(auth_type="token")
        logger.info("Using token authentication")
        config.token = token
        try:
            root.connection.login(
                username=None, password=None, token=token, auth_type="Bearer"
            )
        except awxkit.exceptions.Unauthorized as err:
            raise exceptions.AuthenticationError(err.args[0])
        versions = root.get().available_versions
        try:
            # lookup the user that authenticated with the token
            # If a username was specified in config, use that instead
            my_username = uname or versions.v2.get().me.get().results[0].username
        except (IndexError, AttributeError):
            # lookup failed for whatever reason
            raise exceptions.ProviderError(
                provider="AnsibleTower",
                message="Failed to lookup a username for the given token, please check credentials",
            )
    else:  # dynaconf validators should have checked that either token or password was provided
        helpers.emit(auth_type="password")
        if datetime.now() < datetime(2023, 2, 6):
            time_based_modifier = " and will be unavailable soon"
        else:
            time_based_modifier = ""
        logger.warning(
            f"Password-based authentication is deprecated{time_based_modifier}. "
            "Please use a token instead.\n"
            "See https://docs.ansible.com/automation-controller/latest/html/userguide/"
            "applications_auth.html#applications-tokens for more information"
        )

        config.credentials = {"default": {"username": uname, "password": pword}}
        config.use_sessions = True
        root.load_session().get()
        versions = root.available_versions
        my_username = uname
    return versions.v2.get(), my_username


class AnsibleTower(Provider):

    _validators = [
        Validator("ANSIBLETOWER.release_workflow", default="remove-vm"),
        Validator("ANSIBLETOWER.extend_workflow", default="extend-vm"),
        Validator("ANSIBLETOWER.new_expire_time", default="+172800"),
        Validator("ANSIBLETOWER.workflow_timeout", is_type_of=int, default=3600),
        Validator("ANSIBLETOWER.results_limit", is_type_of=int, default=20),
        Validator("ANSIBLETOWER.error_scope", default="last"),
        Validator("ANSIBLETOWER.base_url", must_exist=True),
        # Validator combination for username+password or token
        (
            (
                Validator("ANSIBLETOWER.username", must_exist=True)
                & Validator("ANSIBLETOWER.password", must_exist=True)
            )
            | Validator("ANSIBLETOWER.token", must_exist=True)
        ),
        Validator("ANSIBLETOWER.inventory", default=None),
    ]

    _checkout_options = [
        click.option(
            "--tower-inventory",
            type=str,
            help="AnsibleTower inventory to checkout a host on",
        ),
        click.option(
            "--workflow", type=str, help="Name of a workflow used to checkout a host"
        ),
    ]
    _execute_options = [
        click.option(
            "--tower-inventory",
            type=str,
            help="AnsibleTower inventory to execute against",
        ),
        click.option("--workflow", type=str, help="Name of a workflow to execute"),
        click.option(
            "--job-template", type=str, help="Name of a job template to execute"
        ),
    ]
    _extend_options = [
        click.option(
            "--new-expire-time",
            type=str,
            help="Time host should expire or time added to host reservation.",
        ),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # get our instance settings
        self.url = settings.ANSIBLETOWER.base_url
        self.uname = settings.ANSIBLETOWER.get("username")
        self.pword = settings.ANSIBLETOWER.get("password")
        self.token = settings.ANSIBLETOWER.get("token")
        self._inventory = (
            kwargs.get("tower_inventory") or settings.ANSIBLETOWER.inventory
        )
        # Init the class itself
        config = kwargs.get("config")
        root = kwargs.get("root")
        self.v2, self.username = get_awxkit_and_uname(
            config=config,
            root=root,
            url=self.url,
            token=self.token,
            uname=self.uname,
            pword=self.pword,
        )
        # Check to see if we're running AAP (ver 4.0+)
        self._is_aap = False if self.v2.ping.get().version[0] == "3" else True

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
        broker_args = getattr(caller_host, "_broker_args", {}).get("_broker_args", {})
        # remove the workflow field since it will conflict with the release workflow
        broker_args.pop("workflow", None)
        # reconstruct tower provider inventory information when needed
        prov_inv = broker_args.pop("tower_inventory", None)
        if isinstance(prov_inv, str):
            logger.debug(f"prov_inv: {prov_inv}")
            prov_inv = self._translate_inventory(prov_inv)
        if prov_inv:
            logger.debug(f"prov_inv: {prov_inv}")
            broker_args["inventory"] = prov_inv
        caller_host._prov_inst.release(
            broker_args.get("source_vm", caller_host.name), broker_args
        )

    def _set_attributes(self, host_inst, broker_args=None):
        host_inst.__dict__.update(
            {
                "release": self._host_release,
                "_prov_inst": self,
                "_broker_provider": "AnsibleTower",
                "_broker_args": broker_args,
            }
        )

    def _translate_inventory(self, inventory):
        if isinstance(inventory, int):  # already an id, silly
            if inventory_info := self.v2.inventory.get(id=inventory):
                return inventory_info.results[0].name
            else:
                raise exceptions.ProviderError(
                    provider="AnsibleTower",
                    message=f"Unknown AnsibleTower inventory by id {inventory}",
                )
        if inventory_info := self.v2.inventory.get(search=inventory):
            if inventory_info.count > 1:
                raise exceptions.ProviderError(
                    provider="AnsibleTower",
                    message=f"Ambigious AnsibleTower inventory name {inventory}",
                )
            elif inventory_info.count == 1:
                inv_struct = inventory_info.results.pop()
                return inv_struct.id
            else:
                raise exceptions.ProviderError(
                    provider="AnsibleTower",
                    message=f"Unknown AnsibleTower inventory {inventory}",
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

    def _get_failure_messages(self, workflow):
        """Find all failure nodes and aggregate failure messages"""
        failure_messages = []
        # get all failed job nodes (iterate)
        if "workflow_nodes" in workflow.related:
            children = workflow.get_related("workflow_nodes").results
            # filter out children with no associated job
            children = list(
                filter(
                    lambda child: getattr(child.summary_fields, "job", None), children
                )
            )
            # filter out children that didn't fail
            children = list(
                filter(lambda child: child.summary_fields.job.failed, children)
            )
            children.sort(key=lambda child: child.summary_fields.job.id)
            for child in children[::-1]:
                if child.type == "workflow_job_node":
                    logger.debug(child)
                    child_id = child.summary_fields.job.id
                    child_obj = self.v2.jobs.get(id=child_id).results
                    if child_obj:
                        child_obj = child_obj.pop()
                        if child_obj.status == "error":
                            failure_messages.append(
                                {
                                    "job": child_obj.name,
                                    "reason": getattr(
                                        child_obj,
                                        "result_traceback",
                                        child_obj.job_explanation,
                                    ),
                                }
                            )
                        else:
                            # get all failed job_events for each job (filter failed=true)
                            failed_events = [
                                ev
                                for ev in child_obj.get_related(
                                    "job_events", page_size=200
                                ).results
                                if ev.failed
                            ]
                            # find the one(s) with event_data['res']['msg']
                            failure_messages.extend(
                                [
                                    {
                                        "job": child_obj.name,
                                        "task": ev.event_data["play"],
                                        "reason": ev.event_data["res"]["msg"],
                                    }
                                    for ev in failed_events
                                    if ev.event_data.get("res", {}).get("msg")
                                ]
                            )
        if not failure_messages:
            return {
                "reason": f"Unable to determine failure cause for {workflow.name} ar {workflow.url}"
            }
        if settings.ANSIBLETOWER.error_scope == "last":
            return failure_messages[0]
        else:
            return failure_messages

    def _get_expire_date(self, host_id):
        try:
            time_stamp = (
                self.v2.hosts.get(id=host_id)
                .results[0]
                .related.ansible_facts.get()
                .expire_date
            )
            return str(datetime.fromtimestamp(int(time_stamp)))
        except Exception:
            return None

    def _compile_host_info(self, host):
        host_info = {
            "name": host.name,
            "type": host.type,
            "hostname": host.variables.get("fqdn"),
            "tower_inventory": self._translate_inventory(host.inventory),
            "_broker_provider": "AnsibleTower",
            "_broker_provider_instance": self.instance,
            "_broker_args": getattr(host, "_broker_args", {}),
        }
        expire_time = self._get_expire_date(host.id)
        if expire_time:
            host_info["expire_time"] = expire_time
        try:
            create_job = self.v2.jobs.get(
                id=host.get_related("job_events").results[0].job
            )
            create_job = create_job.results[0].get_related("source_workflow_job")
            host_info["_broker_args"]["workflow"] = create_job.name
        except IndexError:
            if "last_job" in host.related:
                # potentially not create job, but easier processing below
                create_job = host.get_related("last_job")
                try:
                    host_info["_broker_args"]["workflow"] = host.get_related(
                        "last_job"
                    ).summary_fields.source_workflow_job.name
                except Exception:
                    logger.warning(
                        f"Unable to determine workflow for {host_info['hostname']}"
                    )
            else:
                return host_info
        create_vars = json.loads(create_job.extra_vars)
        host_info["_broker_args"].update(
            {
                arg: val
                for arg, val in create_vars.items()
                if val and isinstance(val, str)
            }
        )
        return host_info

    @cached_property
    def inventory(self):
        if not self._inventory:
            return
        elif isinstance(self._inventory, int):
            # inventory already resolved as id
            return self._inventory
        self._inventory = self._translate_inventory(self._inventory)
        return self._inventory

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
                job, strategy=kwargs.get("strategy", "last")
            )
            # pull information about the job arguments
            job_extra_vars = json.loads(job.extra_vars)
            # and update them if they have resolved values
            for key in job_extra_vars.keys():
                job_extra_vars[key] = job_attrs.get(key)
            kwargs.update({key: val for key, val in job_extra_vars.items() if val})
            kwargs.update({key: val for key, val in job_attrs.items() if val})
            if "tower_inventory" in job_attrs:
                kwargs["tower_inventory"] = job_attrs["tower_inventory"]
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
                logger.warning(f"No hostname found in job attributes:\n{job_attrs}")
            logger.debug(f"hostname: {hostname}, name: {name}, host type: {host_type}")
            host_inst = host_classes[host_type](
                **{**kwargs, "hostname": hostname, "name": name}
            )
        else:
            host_inst = host_classes[kwargs.get("type")](**kwargs)
        self._set_attributes(host_inst, broker_args=kwargs)
        return host_inst

    def execute(self, **kwargs):
        """Execute workflow or job template in Ansible Tower

        :param kwargs: workflow or job template name passed in a string

        :return: dictionary containing all information about executed workflow/job template
        """
        if name := kwargs.get("workflow"):
            subject = "workflow"
            get_path = self.v2.workflow_job_templates
            origin = find_origin()
            kwargs["_broker_origin"] = origin[0]
            if origin[1]:
                kwargs["_jenkins_url"] = origin[1]
        elif name := kwargs.get("job_template"):
            subject = "job_template"
            get_path = self.v2.job_templates
        else:
            raise exceptions.ProviderError(
                provider="AnsibleTower", message="No workflow or job template specified"
            )
        try:
            candidates = get_path.get(name=name).results
        except awxkit.exceptions.Unauthorized as err:
            raise exceptions.AuthenticationError(err.args[0])
        if candidates:
            target = candidates.pop()
        else:
            raise exceptions.ProviderError(
                provider="AnsibleTower",
                message=f"{subject.capitalize()} not found by name: {name}",
            )
        payload = {}
        if inventory := kwargs.pop("inventory", None):
            payload["inventory"] = inventory
        elif self.inventory:
            payload["inventory"] = self.inventory
        else:
            logger.info("No inventory specified, Ansible Tower will use a default.")
        payload["extra_vars"] = str(kwargs)
        logger.debug(
            f"Launching {subject}: {url_parser.urljoin(self.url, str(target.url))}\n"
            f"{payload=}"
        )
        job = target.launch(payload=payload)
        job_number = job.url.rstrip("/").split("/")[-1]
        job_api_url = url_parser.urljoin(self.url, str(job.url))
        if self._is_aap:
            job_ui_url = url_parser.urljoin(
                self.url, f"/#/jobs/{subject}/{job_number}/output"
            )
        else:
            job_ui_url = url_parser.urljoin(self.url, f"/#/{subject}s/{job_number}")
        helpers.emit(api_url=job_api_url, ui_url=job_ui_url)
        logger.info("Waiting for job: \n" f"API: {job_api_url}\n" f"UI: {job_ui_url}")
        job.wait_until_completed(timeout=settings.ANSIBLETOWER.workflow_timeout)
        if not job.status == "successful":
            message_data = {
                f"{subject.capitalize()} Status": job.status,
                "Reason(s)": self._get_failure_messages(job),
                "URL": job_ui_url,
            }
            helpers.emit(message_data)
            raise exceptions.ProviderError(
                provider="AnsibleTower", message=message_data["Reason(s)"]
            )
        if artifacts := kwargs.get("artifacts"):
            del kwargs["artifacts"]
            return self._merge_artifacts(job, strategy=artifacts)
        return job

    def get_inventory(self, user=None):
        """Compile a list of hosts based on any inventory a user's name is mentioned"""
        user = user or self.username
        invs = [
            inv
            for inv in self.v2.inventory.get(page_size=100).results
            if user in inv.name or user == "@ll"
        ]
        hosts = []
        for inv in invs:
            inv_hosts = inv.get_related("hosts", page_size=200).results
            hosts.extend(inv_hosts)
        return [self._compile_host_info(host) for host in hosts]

    def extend_vm(self, target_vm, new_expire_time=None):
        """Run the extend workflow with defaults args

        :param target_vm: This should be a host object
        """
        # check if an inventory was specified. if so overwrite the current inventory
        if new_inv := target_vm._broker_args.get("tower_inventory"):
            if new_inv != self._inventory:
                self._inventory = new_inv
                del self.inventory  # clear the cached value
        return self.execute(
            workflow=settings.ANSIBLETOWER.extend_workflow,
            target_vm=target_vm.name,
            new_expire_time=new_expire_time
            or settings.ANSIBLETOWER.get("new_expire_time"),
        )

    def nick_help(self, **kwargs):
        """Get a list of extra vars and their defaults from a workflow"""
        results_limit = kwargs.get("results_limit", settings.ANSIBLETOWER.results_limit)
        if workflow := kwargs.get("workflow"):
            wfjt = self.v2.workflow_job_templates.get(name=workflow).results.pop()
            logger.info(
                f"Accepted additional nick fields:\n{helpers.yaml_format(wfjt.extra_vars)}"
            )
        elif kwargs.get("workflows"):
            workflows = [
                workflow.name
                for workflow in self.v2.workflow_job_templates.get(
                    page_size=1000
                ).results
                if workflow.summary_fields.user_capabilities.get("start")
            ]
            if res_filter := kwargs.get("results_filter"):
                workflows = results_filter(workflows, res_filter)
                workflows = workflows if isinstance(workflows, list) else [workflows]
            workflows = "\n".join(workflows[:results_limit])
            logger.info(f"Available workflows:\n{workflows}")
        elif inventory := kwargs.get("inventory"):
            inv = self.v2.inventory.get(name=inventory, kind="").results.pop()
            inv = {"Name": inv.name, "ID": inv.id, "Description": inv.description}
            logger.info(f"Accepted additional nick fields:\n{helpers.yaml_format(inv)}")
        elif kwargs.get("inventories"):
            inv = [
                inv.name
                for inv in self.v2.inventory.get(kind="", page_size=1000).results
            ]
            if res_filter := kwargs.get("results_filter"):
                inv = results_filter(inv, res_filter)
                inv = inv if isinstance(inv, list) else [inv]
            inv = "\n".join(inv[:results_limit])
            logger.info(f"Available Inventories:\n{inv}")
        elif job_template := kwargs.get("job_template"):
            jt = self.v2.job_templates.get(name=job_template).results.pop()
            logger.info(
                f"Accepted additional nick fields:\n{helpers.yaml_format(jt.extra_vars)}"
            )
        elif kwargs.get("job_templates"):
            job_templates = [
                job_template.name
                for job_template in self.v2.job_templates.get(page_size=1000).results
                if job_template.summary_fields.user_capabilities.get("start")
            ]
            if res_filter := kwargs.get("results_filter"):
                job_templates = results_filter(job_templates, res_filter)
                job_templates = (
                    job_templates
                    if isinstance(job_templates, list)
                    else [job_templates]
                )
            job_templates = "\n".join(job_templates[:results_limit])
            logger.info(f"Available job templates:\n{job_templates}")
        elif kwargs.get("templates"):
            templates = list(
                {
                    tmpl
                    for tmpl in self.execute(
                        workflow="list-templates", artifacts="last"
                    )["data_out"]["list_templates"]
                }
            )
            templates.sort(reverse=True)
            if res_filter := kwargs.get("results_filter"):
                templates = results_filter(templates, res_filter)
                templates = templates if isinstance(templates, list) else [templates]
            templates = "\n".join(templates[:results_limit])
            logger.info(f"Available templates:\n{templates}")
        else:
            logger.warning("That action is not yet implemented.")

    def release(self, name, broker_args=None):
        if broker_args is None:
            broker_args = {}
        return self.execute(
            workflow=settings.ANSIBLETOWER.release_workflow,
            source_vm=name,
            **broker_args,
        )

    def __repr__(self):
        inner = ", ".join(
            f"{k}={v}"
            for k, v in self.__dict__.items()
            if not k.startswith("_") and not callable(v)
        )
        return f"{self.__class__.__name__}({inner})"


def awxkit_representer(dumper, data):
    """In order to resolve awxkit objects, a custom representer is needed"""
    return dumper.represent_dict(dict(data))


yaml.add_representer(awxkit.utils.PseudoNamespace, awxkit_representer)
