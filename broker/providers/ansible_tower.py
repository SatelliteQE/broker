"""Ansible Tower provider implementation."""

from functools import cache, cached_property
import inspect
import json
import os
import random
import string
import sys
import time
from urllib import parse as url_parser

import click
from dynaconf import Validator
from logzero import logger
from packaging.version import InvalidVersion, Version
from requests.exceptions import ConnectionError

from broker import exceptions
from broker.helpers import MockStub, eval_filter, find_origin, yaml
from broker.settings import settings

# Basic import first - we'll configure it properly later
try:
    import awxkit
except ImportError as err:
    raise exceptions.UserError(message="Unable to import awxkit. Is it installed?") from err

from broker import helpers
from broker.providers import Provider

AAP_URL_PREFIX = "#"  # Used to construct the UI URL

# Configure ruamel yaml representer for PseudoNamespace
yaml.representer.add_representer(
    awxkit.utils.PseudoNamespace,
    lambda dumper, data: dumper.represent_dict(dict(data)),
)


def detect_and_reconfigure_for_aap25(base_url):
    """Detect AAP 2.5+ and reconfigure awxkit if needed.

    This function can be called multiple times safely.
    Returns True if reconfiguration was done, False otherwise.
    """
    # Exit immediately if we've already configured
    if getattr(detect_and_reconfigure_for_aap25, "_configured", False):
        return False

    # Check to see if the user wants us to operate against AAP 2.4-
    need_reconfigure = False
    try:
        if Version(settings.ANSIBLETOWER.get("AAP_VERSION")) < Version("2.5"):
            return False
        need_reconfigure = True
    except (InvalidVersion, TypeError):
        pass  # aap_version isn't set or is invalid, need to manually check

    # Only perform API check if we don't already know we need to reconfigure
    if not need_reconfigure:
        config = awxkit.config
        config.base_url = base_url
        try:
            # Create temporary API instance to test version
            api = awxkit.api.Api()
            api.get().available_versions
        except AttributeError:
            # AAP 2.5+ hits this error, so we need to reconfigure
            logger.debug("AAP 2.5+ detected, need to reconfigure awxkit")
            need_reconfigure = True

    # Perform reconfiguration only if needed
    if need_reconfigure:
        logger.debug("Reconfiguring awxkit for AAP 2.5+")

        # Remove all awxkit modules from sys.modules
        for module_name in list(sys.modules.keys()):
            if "awx" in module_name:
                del sys.modules[module_name]

        # Set environment variable for API path
        os.environ["AWXKIT_API_BASE_PATH"] = "/api/controller/"

        # Re-import awxkit - must use globals() to update module reference in this scope
        import awxkit as awxkit_new

        globals()["awxkit"] = awxkit_new

        # Update the url prefix
        global AAP_URL_PREFIX  # noqa: PLW0603
        AAP_URL_PREFIX = "execution"

        # re-onfigure ruamel yaml representer for PseudoNamespace
        yaml.representer.add_representer(
            awxkit_new.utils.PseudoNamespace,
            lambda dumper, data: dumper.represent_dict(dict(data)),
        )

        # Set flag to avoid redundant reconfiguration
        detect_and_reconfigure_for_aap25._configured = True
        return True

    return False


def convert_pseudonamespaces(attr_dict):
    """Recursively convert PsuedoNamespace objects into dictionaries."""
    out_dict = {}
    for key, value in attr_dict.items():
        if isinstance(value, awxkit.utils.PseudoNamespace):
            out_dict[key] = dict(value)
        elif isinstance(value, dict):
            out_dict[key] = convert_pseudonamespaces(value)
        else:
            out_dict[key] = value
    return out_dict


def resilient_job_wait(job, job_timeout=None, max_wait=None):
    """Wait for a job to complete. Retry on errors.

    Args:
        job: The job object to wait for
        job_timeout: Timeout for individual job wait attempts
        max_wait: Maximum time to continue retrying before giving up
    """
    job_timeout = job_timeout or settings.ANSIBLETOWER.workflow_timeout
    max_wait = max_wait or settings.ANSIBLETOWER.max_resilient_wait

    completed = False
    start_time = time.time()

    while not completed:
        # Check if we've exceeded max wait time
        if time.time() - start_time > max_wait:
            raise JobExecutionError(
                message_data={
                    "error": "Maximum resilient wait time exceeded",
                    "max_wait_seconds": max_wait,
                    "elapsed_seconds": int(time.time() - start_time),
                }
            )

        try:
            job.wait_until_completed(timeout=job_timeout)
            completed = True
        except (ConnectionError, awxkit.exceptions.Unknown, awxkit.exceptions.Forbidden) as err:
            logger.error(f"Error occurred while waiting for job: {err}")
            logger.info("Retrying job wait...")
            # Add a small delay before retrying to avoid hammering the API
            time.sleep(5)


class JobExecutionError(exceptions.ProviderError):
    """Raised when a job execution fails."""

    def __init__(self, message_data=None):
        super().__init__(
            provider="AnsibleTower",
            message=json.dumps(message_data, indent=2),
        )


class ATInventoryError(exceptions.ProviderError):
    """Raised when we can't find the right inventory."""

    def __init__(self, message=None):
        super().__init__(
            provider="AnsibleTower",
            message=message,
        )


@cache
def get_awxkit_api(awxkit_config=None, root=None, url=None, token=None, uname=None, pword=None):
    """Return an awxkit api object and token information."""
    if not isinstance(awxkit_config, MockStub):  # skip if we're in a unit test
        # Configure for AAP 2.5+ if needed, with the URL we have
        detect_and_reconfigure_for_aap25(base_url=url)

    # Now proceed with the original function logic
    awxkit_config = awxkit_config or awxkit.config
    awxkit_config.base_url = url
    if root is None:  # support mock stub for unit tests
        root = awxkit.api.Api()

    temp_token_desc = None

    # If no token was provided, try to create a temporary token
    if not token and uname and pword:
        helpers.emit(auth_type="password")
        logger.warning(
            "You should be using token-based authentication.\n"
            "I will attempt to create and use a temporary token."
        )

        # Set up password-based auth temporarily to create a token
        awxkit_config.credentials = {"default": {"username": uname, "password": pword}}
        awxkit_config.use_sessions = True
        root.load_session().get()
        versions = root.available_versions

        # Create temporary token with random description
        try:
            temp_token_desc = "Broker temp " + "".join(random.choices(string.ascii_letters, k=10))
            if token := root.get_oauth2_token(description=temp_token_desc):
                logger.info(
                    f"Successfully created a temporary token with description: {temp_token_desc}."
                )
        except Exception as err:  # noqa: BLE001 - Don't currently know the specific exception
            logger.debug(f"Error creating temporary token: {err}")
            logger.warning(
                "Failed to create temporary token. Continuing with password authentication"
            )

    # Now proceed with token authentication (either provided or newly created)
    if token:
        helpers.emit(auth_type="token")
        logger.info("Using token authentication")
        awxkit_config.token = token
        try:
            root.connection.login(username=None, password=None, token=token, auth_type="Bearer")
        except awxkit.exceptions.Unauthorized as err:
            raise exceptions.AuthenticationError(err.args[0]) from err
        versions = root.get().available_versions

    return versions.v2.get(), temp_token_desc


@Provider.auto_hide
class AnsibleTower(Provider):
    """Ansible Tower provider provides a Broker-specific wrapper around awxkit."""

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
        Validator("ANSIBLETOWER.AAP_VERSION", default="", cast=str),
        Validator("ANSIBLETOWER.max_resilient_wait", is_type_of=int, default=7200),
    ]

    _checkout_options = [
        click.option(
            "--tower-inventory",
            type=str,
            help="AnsibleTower inventory to checkout a host on",
        ),
        click.option("--workflow", type=str, help="Name of a workflow used to checkout a host"),
    ]
    _execute_options = [
        click.option(
            "--tower-inventory",
            type=str,
            help="AnsibleTower inventory to execute against",
        ),
        click.option("--workflow", type=str, help="Name of a workflow to execute"),
        click.option("--job-template", type=str, help="Name of a job template to execute"),
    ]
    _extend_options = [
        click.option(
            "--new-expire-time",
            type=str,
            help="Time host should expire or time added to host reservation.",
        ),
    ]

    _sensitive_attrs = ["pword", "password", "token"]

    def __init__(self, **kwargs):
        """Almost all values are taken from Broker's config with the following exceptions.

        kwargs:
            tower_inventory: AnsibleTower inventory to use for this instance
            config: awxkit config object
            root: awxkit api root object
        """
        super().__init__(**kwargs)
        # get our instance settings
        self.url = settings.ANSIBLETOWER.base_url
        self.uname = settings.ANSIBLETOWER.get("username")
        self.pword = settings.ANSIBLETOWER.get("password")
        self.token = settings.ANSIBLETOWER.get("token")
        self._inventory = kwargs.get("tower_inventory") or settings.ANSIBLETOWER.inventory
        # Init the class itself
        config = kwargs.get("config")
        root = kwargs.get("root")
        self._v2, self._temp_token_desc = get_awxkit_api(
            awxkit_config=config,
            root=root,
            url=self.url,
            token=self.token,
            uname=self.uname,
            pword=self.pword,
        )

        # Get the username for the authenticated user
        # If a username was specified in config, use that instead
        self.username = self.uname or self._v2.me.get().results[0].username
        # Check to see if we're running AAP (ver 4.0+)
        self._is_aap = self._v2.ping.get().version[0] != "3"

    def __del__(self):
        """Clean up any temporary tokens we created."""
        if getattr(self, "_temp_token_desc", None) and hasattr(self, "_v2"):
            try:
                # Find and delete the temporary token
                tokens = self._v2.tokens.get(description=self._temp_token_desc).results
                if tokens:
                    for token in tokens:
                        token.delete()
                    logger.debug(f"Deleted temporary token: {self._temp_token_desc}")
            except Exception as err:  # noqa: BLE001 - Not currently known
                # Just log the error since we're in __del__
                logger.debug(f"Failed to delete temporary token: {err}")

    @staticmethod
    def _pull_params(kwargs):
        """Given a kwarg dict, separate AT-specific parameters from other kwargs.

        AT-specific params must stat with double underscores.
        Example: __page_size.
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
        source_vm = broker_args.pop("source_vm", caller_host.name)
        caller_host._prov_inst.release(source_vm, broker_args)

    def _set_attributes(self, host_inst, broker_args=None, misc_attrs=None):
        host_inst.__dict__.update(
            {
                "release": self._host_release,
                "_prov_inst": self,
                "_broker_provider": "AnsibleTower",
                "_broker_args": convert_pseudonamespaces(broker_args),
            }
        )
        if isinstance(misc_attrs, dict):
            host_inst.__dict__.update(convert_pseudonamespaces(misc_attrs))

    def _translate_inventory(self, inventory):
        if isinstance(inventory, int):  # already an id, silly
            if (inventory_info := self._v2.inventory.get(id=inventory)).results:
                return inventory_info.results[0].name
            else:
                raise ATInventoryError(
                    message=f"Unknown AnsibleTower inventory by id {inventory}",
                )
        elif isinstance(inventory, str):
            if inventory_info := self._v2.inventory.get(search=inventory):
                if inventory_info.count > 1:
                    # let's try to manually narrow down to one result if the api returns multiple
                    filtered = [inv for inv in inventory_info.results if inv.name == inventory]
                    if len(filtered) == 1:
                        return filtered[0].id
                    raise ATInventoryError(
                        message=f"Ambigious AnsibleTower inventory name {inventory}",
                    )
                elif inventory_info.count == 1:
                    return inventory_info.results.pop().id
                else:
                    raise ATInventoryError(
                        message=f"Unknown AnsibleTower inventory {inventory}",
                    )
        elif inv_id := getattr(inventory, "id", None):
            return inv_id
        elif inv_name := getattr(inventory, "name", None):
            return inv_name
        else:
            caller_context = inspect.stack()[1][0].f_locals
            raise ATInventoryError(
                message=f"Ambiguous AnsibleTower inventory {inventory} passed from {caller_context}",
            )

    def _merge_artifacts(self, at_object, strategy="last", artifacts=None):
        """Gather and merge all artifacts associated with an object and its children.

        :param at_object: object you want to merge

        :param strategy:
            strategies:
               - merge: merge artifact dictionaries together
               - last: return only the artifacts associated with the last child job

        :param artifacts: default to none

        :return: dictionary of merged artifact, used for constructing host
        """
        logger.debug(f"Attempting to merge: {at_object.name}")

        if artifacts is None:
            artifacts = {}

        # Merge with or overwrite previous artifacts, depending on strategy
        if getattr(at_object, "artifacts", None):
            logger.debug(f"Found artifacts: {at_object.artifacts}")
            if strategy == "merge":
                artifacts = helpers.merge_dicts(artifacts, at_object.artifacts)
            elif strategy == "last":
                artifacts = at_object.artifacts

        # If this is a workflow job, then find any children jobs
        if "workflow_nodes" in at_object.related:
            children = at_object.get_related("workflow_nodes").results

            # Filter out children with no associated job
            children = list(
                filter(lambda child: getattr(child.summary_fields, "job", None), children)
            )

            # Sort children by job id
            children.sort(key=lambda child: child.summary_fields.job.id)

            if strategy == "last":
                # Filter out all but the last job
                children = children[-1:]

            for child in children:
                if child.type == "workflow_job_node":
                    logger.debug(child)
                    child_id = child.summary_fields.job.id
                    child_obj = self._v2.jobs.get(id=child_id).results
                    if child_obj:
                        child_obj = child_obj.pop()
                        artifacts = (
                            self._merge_artifacts(child_obj, strategy, artifacts) or artifacts
                        )
                    else:
                        logger.warning(
                            f"Unable to pull information from child job with id {child_id}."
                        )
        return artifacts

    def _get_failure_messages(self, workflow):
        """Find all failure nodes and aggregate failure messages."""
        failure_messages = []
        # get all failed job nodes (iterate)
        if "workflow_nodes" in workflow.related:
            children = workflow.get_related("workflow_nodes").results
            # filter out children with no associated job
            children = list(
                filter(lambda child: getattr(child.summary_fields, "job", None), children)
            )
            # filter out children that didn't fail
            children = list(filter(lambda child: child.summary_fields.job.failed, children))
            children.sort(key=lambda child: child.summary_fields.job.id)
            for child in children[::-1]:
                if child.type == "workflow_job_node":
                    logger.debug(child)
                    child_id = child.summary_fields.job.id
                    child_obj = self._v2.jobs.get(id=child_id).results
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
                                for ev in child_obj.get_related("job_events", page_size=200).results
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

    def _compile_host_info(self, host):
        try:
            host_facts = host.related.ansible_facts.get()
        except awxkit.exceptions.Forbidden as err:
            logger.warning(f"Unable to get facts for {host.name}: {err}")
            host_facts = {}

        # Get the hostname from host variables or facts
        hostname = (
            host.variables.get("fqdn")
            or getattr(host_facts, "ansible_fqdn", None)
            # Workaround for OSP hosts that have lost their hostname
            or host.variables.get("openstack", {}).get("metadata", {}).get("fqdn", None)
        )

        # Get broker_args from host facts if present
        broker_args = getattr(host_facts, "_broker_args", {})
        broker_facts = getattr(host_facts, "_broker_facts", {})

        host_info = {key: val for key, val in broker_facts.items() if val}
        host_info.update(
            {
                "name": host.name,
                "type": host.type,
                "hostname": hostname,
                "ip": host.variables.get("ansible_host"),
                "tower_inventory": self._translate_inventory(host.inventory),
                "_broker_provider": "AnsibleTower",
                "_broker_provider_instance": self.instance,
                # Get _broker_args from host facts if present
                "_broker_args": {key: val for key, val in broker_args.items() if val},
            }
        )

        # Find and add extra fields
        interfaces = getattr(host_facts, "ansible_interfaces", [])
        facts = {
            "os_distribution": getattr(host_facts, "ansible_distribution", None),
            "os_distribution_version": getattr(host_facts, "ansible_distribution_version", None),
            "reported_devices": {"nics": interfaces} if interfaces else None,
        }
        host_info.update({key: val for key, val in facts.items() if val})

        return host_info

    @staticmethod
    def _pull_extra_vars(extra_vars):
        """Pull extra vars from a json string or pseudo-dictionary."""
        if not extra_vars:
            return {}
        try:
            return json.loads(extra_vars)
        except json.JSONDecodeError:
            logger.warning(
                f"Job uses non-json extra_vars:\n{extra_vars}\n"
                "Attempting to parse as pseudo-dictionary."
            )
            compiled = {}
            for line in extra_vars.splitlines():
                key, val = line.split(": ")
                compiled[key] = val
            return compiled

    def _resolve_labels(self, labels, target):
        """Fetch and return ids of the given labels.

        If label does not exist, create it under the same org as the target template.
        """
        label_ids = []
        for label in labels:
            label_expanded = f"{label}={labels[label]}" if labels[label] else label
            try:
                result = self._v2.labels.post(
                    {"name": label_expanded, "organization": target.summary_fields.organization.id}
                )
                if result:
                    label_ids.append(result.id)
            except awxkit.exceptions.Duplicate:
                logger.debug(f"Provider label {label_expanded} already exists on AAP instance")
                if result := self._v2.labels.get(name=label_expanded).results:
                    logger.debug(f"Provider label {label_expanded} retrieved successfully")
                    label_ids.append(result[0].id)
                else:
                    logger.warning(
                        f"Provider label {label_expanded} not found on AAP despite AAP returning 400: Duplicate while trying to create it"
                    )
        return label_ids

    def _verify_default_inventory(self, inv_id, subject):
        """Verify that the user at least has view access to a workflow/template's default inventory."""
        if inv_id is None:
            raise exceptions.UserError(
                f"The {subject} has no default inventory set, please specify one."
            )
        try:
            self._translate_inventory(inv_id)
        except ATInventoryError as err:
            raise exceptions.UserError(
                f"You don't have access to this {subject}'s default inventory, please specify one."
            ) from err

    @cached_property
    def inventory(self):
        """Return the current tower inventory."""
        if not self._inventory:
            return
        elif isinstance(self._inventory, int):
            # inventory already resolved as id
            return self._inventory
        self._inventory = self._translate_inventory(self._inventory)
        return self._inventory

    def construct_host(self, provider_params, host_classes, **kwargs):
        """Construct a host to be read by Ansible Tower.

        :param provider_params: dictionary of what the provider returns when initially
        creating the vm

        :param host_classes: host object

        :return: broker object of constructed host instance
        """
        broker_args = kwargs.copy()
        broker_facts = {}

        strategy = broker_args.pop("strategy", "last")

        def _get_fields_from_facts(facts):
            hostname = None
            name = None
            host_type = "host"

            for key, value in facts.items():
                if key.endswith("fqdn") and not hostname:
                    hostname = value if not isinstance(value, list) else value[0]
                if key in ("name", "vm_provisioned") and not name:
                    name = value if not isinstance(value, list) else value[0]
                if key.endswith("host_type"):
                    host_type = value if value in host_classes else host_type

            return hostname, name, host_type

        if provider_params:
            job = provider_params
            artifacts = self._merge_artifacts(job, strategy=strategy)

            # Get host facts from job artifacts
            if "_broker_args" in artifacts and "_broker_facts" in artifacts:
                broker_args = {k: v for k, v in artifacts._broker_args.items() if v}
                broker_facts = {k: v for k, v in artifacts._broker_facts.items() if v}
                logger.debug(artifacts)

                # Get hostname, VM name, and host type
                hostname, name, host_type = _get_fields_from_facts(broker_facts)
                if not hostname:
                    logger.warning(f"No hostname found in job artifacts:\n{artifacts}")
                logger.debug(f"hostname: {hostname}, name: {name}, host type: {host_type}")

                host_inst = host_classes[host_type](
                    **{**broker_args, "hostname": hostname, "name": name}
                )
                broker_facts["name"] = name
                broker_facts["hostname"] = hostname
            else:
                logger.debug(f"Host facts not found in artifacts for job: {job}")

        else:
            host_inst = host_classes[kwargs.get("type")](**broker_args)

        self._set_attributes(host_inst, broker_args=broker_args, misc_attrs=broker_facts)

        return host_inst

    @Provider.register_action("workflow", "job_template")
    def execute(self, **kwargs):  # noqa: PLR0912,PLR0915 - Possible TODO refactor
        """Execute workflow or job template in Ansible Tower.

        :param kwargs: workflow or job template name passed in a string

        :return: dictionary containing all information about executed workflow/job template
        """
        if name := kwargs.get("workflow"):
            subject = "workflow"
            get_path = self._v2.workflow_job_templates
            origin = find_origin()
            kwargs["_broker_origin"] = origin[0]
            if origin[1]:
                kwargs["_jenkins_url"] = origin[1]
        elif name := kwargs.get("job_template"):
            subject = "job_template"
            get_path = self._v2.job_templates
        else:
            raise exceptions.UserError(message="No workflow or job template specified")
        try:
            candidates = get_path.get(name=name).results
        except awxkit.exceptions.Unauthorized as err:
            raise exceptions.AuthenticationError(err.args[0]) from err
        if candidates:
            target = candidates.pop()
        else:
            raise exceptions.UserError(message=f"{subject.capitalize()} not found by name: {name}")
        payload = {}
        if inventory := kwargs.pop("inventory", None):
            payload["inventory"] = inventory
            logger.info(f"Using tower inventory: {self._translate_inventory(inventory)}")

        elif self.inventory:
            payload["inventory"] = self.inventory
            logger.info(f"Using tower inventory: {self._translate_inventory(self.inventory)}")
        else:
            self._verify_default_inventory(inv_id=getattr(target, "inventory", None), subject=name)
            logger.info("No inventory specified, Ansible Tower will use a default.")

        # provider labels handling

        provider_labels = kwargs.get("provider_labels", {})
        # include eventual common labels, specified at each level of configuration
        # typically imported from dynaconf env vars
        provider_labels.update(settings.get("provider_labels", {}))
        provider_labels.update(settings.ANSIBLETOWER.get("provider_labels", {}))
        if provider_labels:
            payload["labels"] = self._resolve_labels(provider_labels, target)
            kwargs["provider_labels"] = provider_labels

        # Save custom, non-workflow extra vars to a named variable.
        # The workflow can save these values to job artifacts / host facts.
        workflow_extra_vars = self._pull_extra_vars(target.extra_vars)
        kwargs["_broker_extra_vars"] = {
            k: v for k, v in kwargs.items() if k not in workflow_extra_vars
        }
        payload["extra_vars"] = json.dumps(kwargs)
        logger.debug(
            f"Launching {subject}: {url_parser.urljoin(self.url, str(target.url))}\n{payload=}"
        )
        job = target.launch(payload=payload)
        job_number = job.url.rstrip("/").split("/")[-1]
        job_api_url = url_parser.urljoin(self.url, str(job.url))
        # Need to change the url subject for job templates. If this increases, then come up with a better solution
        job_ui_url = url_parser.urljoin(
            self.url,
            f"/{AAP_URL_PREFIX}/jobs/{'playbook' if subject == 'job_template' else subject}/{job_number}/output",
        )
        helpers.emit(api_url=job_api_url, ui_url=job_ui_url)
        logger.info(f"Waiting for job: \nAPI: {job_api_url}\nUI: {job_ui_url}")
        resilient_job_wait(job)
        if job.status != "successful":
            message_data = {
                f"{subject.capitalize()} Status": job.status,
                "Reason(s)": self._get_failure_messages(job),
                "URL": job_ui_url,
            }
            helpers.emit(message_data)
            raise JobExecutionError(message_data=message_data["Reason(s)"])
        if strategy := kwargs.pop("artifacts", None):
            return self._merge_artifacts(job, strategy=strategy)
        return job

    def get_inventory(self, user=None):
        """Compile a list of hosts based on any inventory a user's name is mentioned."""
        user = user or self.username
        invs = [
            inv
            for inv in self._v2.inventory.get(page_size=200).results
            if user in inv.name or user == "@ll"
        ]
        hosts = []
        for inv in invs:
            inv_hosts = inv.get_related("hosts", page_size=200).results
            hosts.extend(inv_hosts)
        with click.progressbar(hosts, label="Compiling host information") as hosts_bar:
            compiled_host_info = [self._compile_host_info(host) for host in hosts_bar]
        return compiled_host_info

    def extend(self, target_vm, new_expire_time=None, provider_labels=None):
        """Run the extend workflow with defaults args.

        :param target_vm: This should be a host object
        """
        if provider_labels is None:
            provider_labels = {}
        # check if an inventory was specified. if so overwrite the current inventory
        if new_inv := target_vm._broker_args.get("tower_inventory"):
            if new_inv != self._inventory:
                self._inventory = new_inv
                if hasattr(self.__dict__, "inventory"):
                    del self.inventory  # clear the cached value
        return self.execute(
            workflow=settings.ANSIBLETOWER.extend_workflow,
            target_vm=target_vm.name,
            new_expire_time=new_expire_time or settings.ANSIBLETOWER.get("new_expire_time"),
            provider_labels=provider_labels,
        )

    def provider_help(  # noqa: PLR0911, PLR0912, PLR0915 - Possible TODO refactor
        self,
        workflows=False,
        workflow=None,
        job_templates=False,
        job_template=None,
        templates=False,
        inventories=False,
        inventory=None,
        **kwargs,
    ):
        """Get a list of extra vars and their defaults from a workflow."""
        results_limit = kwargs.get("results_limit", settings.ANSIBLETOWER.results_limit)
        if workflow:
            if wfjt := self._v2.workflow_job_templates.get(name=workflow).results:
                wfjt = wfjt.pop()
            else:
                logger.warning(f"Workflow {workflow} not found!")
                return
            default_inv = self._v2.inventory.get(id=wfjt.inventory).results.pop()
            logger.info(
                f"\nDescription:\n{wfjt.description}\n\n"
                f"Accepted additional nick fields:\n{helpers.yaml_format(wfjt.extra_vars)}"
                f"tower_inventory: {default_inv['name']}"
            )
        elif workflows:
            workflows = [
                workflow.name
                for workflow in self._v2.workflow_job_templates.get(page_size=1000).results
                if workflow.summary_fields.user_capabilities.get("start")
            ]
            if not workflows:
                logger.warning("No workflows found")
                return
            if res_filter := kwargs.get("results_filter"):
                workflows = eval_filter(workflows, res_filter, "res")
                workflows = workflows if isinstance(workflows, list) else [workflows]
            workflows = "\n".join(workflows[:results_limit])
            logger.info(f"Available workflows:\n{workflows}")
        elif inventory:
            if inv := self._v2.inventory.get(name=inventory, kind="").results:
                inv = inv.pop()
            else:
                logger.warning(f"Inventory {inventory} not found!")
                return
            inv = {"Name": inv.name, "ID": inv.id, "Description": inv.description}
            logger.info(f"Accepted additional nick fields:\n{helpers.yaml_format(inv)}")
        elif inventories:
            inv = [inv.name for inv in self._v2.inventory.get(kind="", page_size=1000).results]
            if not inv:
                logger.warning("No inventories found!")
                return
            if res_filter := kwargs.get("results_filter"):
                inv = eval_filter(inv, res_filter, "res")
                inv = inv if isinstance(inv, list) else [inv]
            inv = "\n".join(inv[:results_limit])
            logger.info(f"Available Inventories:\n{inv}")
        elif job_template:
            if jt := self._v2.job_templates.get(name=job_template).results:
                jt = jt.pop()
            else:
                logger.warning(f"Job Template {job_template} not found!")
                return
            default_inv = self._v2.inventory.get(id=jt.inventory).results.pop()
            logger.info(
                f"\nDescription:\n{jt.description}\n\n"
                f"Accepted additional nick fields:\n{helpers.yaml_format(jt.extra_vars)}"
                f"tower_inventory: {default_inv['name']}"
            )
        elif job_templates:
            job_templates = [
                job_template.name
                for job_template in self._v2.job_templates.get(page_size=1000).results
                if job_template.summary_fields.user_capabilities.get("start")
            ]
            if not job_templates:
                logger.warning("No job templates found!")
                return
            if res_filter := kwargs.get("results_filter"):
                job_templates = eval_filter(job_templates, res_filter, "res")
                job_templates = (
                    job_templates if isinstance(job_templates, list) else [job_templates]
                )
            job_templates = "\n".join(job_templates[:results_limit])
            logger.info(f"Available job templates:\n{job_templates}")
        elif templates:
            templates = list(
                set(
                    self.execute(workflow="list-templates", artifacts="last")["data_out"][
                        "list_templates"
                    ]
                )
            )
            if not templates:
                logger.warning("No templates found!")
                return
            templates.sort(reverse=True)
            if res_filter := kwargs.get("results_filter"):
                templates = eval_filter(templates, res_filter, "res")
                templates = templates if isinstance(templates, list) else [templates]
            templates = "\n".join(templates[:results_limit])
            logger.info(f"Available templates:\n{templates}")

    def release(self, name, broker_args=None):
        """Release the host back to the tower instance via the release workflow."""
        if broker_args is None:
            broker_args = {}
        return self.execute(
            workflow=settings.ANSIBLETOWER.release_workflow,
            source_vm=name,
            **broker_args,
        )
