"""Foreman provider implementation."""
import time
from uuid import uuid4

import click
from dynaconf import Validator
from logzero import logger
import requests

from broker.exceptions import ProviderError
from broker.helpers import Result
from broker.providers import Provider
from broker.settings import settings


class ForemanAPI:
    """Default runtime to query Foreman."""

    headers = {
        "Content-Type": "application/json",
    }

    def __init__(self, **kwargs):
        self.foreman_username = settings.foreman.foreman_username
        self.foreman_password = settings.foreman.foreman_password
        self.url = settings.foreman.foreman_url
        self.prefix = settings.foreman.name_prefix
        self.verify = settings.foreman.verify
        self.session = requests.session()

    def interpret_response(self, response):
        """Handle responses from Foreman, in particular catch errors."""
        if "error" in response:
            if "Unable to authenticate user" in response["error"]["message"]:
                logger.warning("Could not authenticate")
            raise ProviderError(
                provider=self.__class__.__name__,
                message=" ".join(response["error"]["full_messages"]),
            )
        if "errors" in response:
            raise ProviderError(
                provider=self.__class__.__name__, message=" ".join(response["errors"]["base"])
            )
        return response

    def _get(self, endpoint):
        """Send GET request to Foreman API."""
        response = self.session.get(
            self.url + endpoint,
            auth=(self.foreman_username, self.foreman_password),
            headers=self.headers,
            verify=self.verify,
        ).json()
        return self.interpret_response(response)

    def _post(self, endpoint, **kwargs):
        """Send POST request to Foreman API."""
        response = self.session.post(
            self.url + endpoint,
            auth=(self.foreman_username, self.foreman_password),
            headers=self.headers,
            verify=self.verify,
            **kwargs,
        ).json()
        return self.interpret_response(response)

    def _delete(self, endpoint, **kwargs):
        """Send DELETE request to Foreman API."""
        response = self.session.delete(
            self.url + endpoint,
            auth=(self.foreman_username, self.foreman_password),
            headers=self.headers,
            verify=self.verify,
            **kwargs,
        )
        return self.interpret_response(response)

    def obtain_id_from_name(self, resource_type, resource_name):
        """Obtain id for resource with given name.

        :param resource_type: Resource type, like hostgroups, hosts, ...

        :param resource_name: String-like identifiere of the resource

        :return: ID of the found object
        """
        response = self._get(
            f"/api/{resource_type}?per_page=200",
        )
        try:
            result = response["results"]
            resource = next(x for x in result if x["name"] == resource_name)
            id_ = resource["id"]
        except KeyError:
            logger.error(f"Could not find {resource_type} {resource_name}")
            raise
        except StopIteration:
            raise ProviderError(
                provider=self.__class__.__name__,
                message=f"Could not find {resource_name} in {resource_type}",
            )
        return id_

    def create_job_invocation(self, data):
        """Run a job from the provided data."""
        return self._post(
            "/api/job_invocations",
            json=data,
        )["id"]

    def job_output(self, job_id):
        """Return output of job."""
        return self._get(f"/api/job_invocations/{job_id}/outputs")["outputs"][0]["output"]

    def wait_for_job_to_finish(self, job_id):
        """Poll API for job status until it is finished.

        :param job_id: id of the job to poll
        """
        still_running = True
        while still_running:
            response = self._get(f"/api/job_invocations/{job_id}")
            still_running = response["status_label"] == "running"
            time.sleep(1)

    def hostgroups(
        self,
    ):
        """Return list of available hostgroups."""
        return self._get("/api/hostgroups")

    def hosts(self):
        """Return list of hosts deployed using this prefix."""
        return self._get(f"/api/hosts?search={self.prefix}")["results"]

    def image_uuid(self, compute_resource_id, image_name):
        """Return the uuid of a VM image on a specific compute resource."""
        try:
            return self._get(
                "/api/compute_resources/"
                f"{compute_resource_id}"
                f"/images/?search=name={image_name}"
            )["results"][0]["uuid"]
        except IndexError:
            logger.error(f"Could not find {image_name} in VM images")

    def create_host(self, data):
        """Create a host from the provided data."""
        return self._post("/api/hosts", json=data)

    def wait_for_host_to_install(self, hostname):
        """Poll API for host build status until it is built.

        :param hostname: name of the host which is currently being built
        """
        building = True
        while building:
            host_status = self._get(f"/api/hosts/{hostname}")
            building = host_status["build_status"] != 0
            time.sleep(1)


class Foreman(Provider):
    """Foreman provider class providing an interface around the Foreman API."""

    _validators = [
        Validator("FOREMAN.organization", must_exist=True),
        Validator("FOREMAN.location", must_exist=True),
        Validator("FOREMAN.foreman_username", must_exist=True),
        Validator("FOREMAN.foreman_password", must_exist=True),
        Validator("FOREMAN.foreman_url", must_exist=True),
        Validator("FOREMAN.hostgroup", must_exist=False),
        Validator("FOREMAN.verify", default=False),
    ]
    _sensitive_attrs = ["foreman_password"]

    _checkout_options = [
        click.option(
            "--hostgroup",
            type=str,
            help="Name of the Foreman hostgroup to deploy the host",
        ),
    ]
    _execute_options = []
    _extend_options = []

    hidden = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        if kwargs.get("bind") is not None:
            self._runtime_cls = kwargs.pop("bind")
        else:
            self._runtime_cls = ForemanAPI

        self.runtime = self._runtime_cls(
            foreman_username=settings.foreman.foreman_username,
            foreman_password=settings.foreman.foreman_password,
            url=settings.foreman.foreman_url,
            prefix=settings.foreman.name_prefix,
            verify=settings.foreman.verify,
        )
        self.prefix = settings.foreman.name_prefix

        self.organization_id = self.runtime.obtain_id_from_name(
            "organizations", settings.foreman.organization
        )
        self.location_id = self.runtime.obtain_id_from_name("locations", settings.foreman.location)

    def release(self, host):
        """Release a host.

        :param host: Hostname or ID of the host to delete
        """
        data = {
            "organization_id": self.organization_id,
            "location_id": self.location_id,
        }
        self.runtime._delete(
            f"/api/hosts/{host}",
            json=data,
        )

    def extend(self):
        """There is no need to extend a host on Foreman."""
        pass

    def _host_execute(self, command):
        """Execute command on a single host.

        :param command: a command to be executed on the target host

        :return: Result object containing information about the executed job
        """
        hostname = self.last_deployed_host

        return self.execute(
            hostname=hostname,
            job_template="Run Command - Script Default",
            command=command,
        )

    def _parse_job_output(self, job_output):
        """Parse response Foreman gives when querying job output.

        Example data can be found in tests/data/foreman/fake_jobs.json

        Typically the data is a list. In case of failures the last element
        of the output has the element "output_type" "debug" otherwise it is
        "stdout".

        The element before or the last element in case of success contains the
        statusline with the errorcode.
        """
        if job_output[-1]["output_type"] == "debug":
            # command was not successful
            statusline = job_output[-2]["output"]
            status = int(statusline.split(": ")[1])
            stdout = "\n".join([item["output"] for item in job_output[:-2]])
            stderr = ""
        else:
            # command was successful
            status = 0
            stdout = "\n".join([item["output"] for item in job_output[:-1]])
            stderr = ""
        return Result(status=status, stdout=stdout, stderr=stderr)

    def execute(self, hostname, job_template, **kwargs):
        """Execute remote execution on target host.

        :param hostname: hostname to perform remote execution on

        :param job_template: name of the job template

        :param kwargs: input parameters for the job template

        :return: Result object containing all information about executed jobs
        """
        job_template_id = self.runtime.obtain_id_from_name("job_templates", job_template)
        data = {
            "organization_id": self.organization_id,
            "location_id": self.location_id,
            "job_invocation": {
                "job_template_id": job_template_id,
                "targeting_type": "static_query",
                "inputs": kwargs,
                "search_query": f"name={hostname}",
            },
        }
        job_id = self.runtime.create_job_invocation(data)
        self.runtime.wait_for_job_to_finish(job_id)
        job_output = self.runtime.job_output(job_id)

        return self._parse_job_output(job_output)

    def provider_help(
        self,
        hostgroups=True,
        rex=None,
        **kwargs,
    ):
        """Return useful information about Foreman provider."""
        if hostgroups:
            self.__init__()
            all_hostgroups = self.runtime.hostgroups()
            logger.info(f"On Foreman {self.instance} you have the following hostgroups:")
            for hostgroup in all_hostgroups["results"]:
                logger.info(f"- {hostgroup['title']}")

    def _compile_host_info(self, host):
        return {
            "name": host["certname"],  # alternatives: name, display_name
            "hostgroup": host["hostgroup_title"],
            "hostname": host["certname"],  # alternatives: name, display_name
            "ip": host["ip"],
            "_broker_provider": "Foreman",
            "_broker_provider_instance": self.instance,
        }

    def get_inventory(self, *args, **kwargs):
        """Synchronize list of hosts on Foreman using set prefix."""
        all_hosts = self.runtime.hosts()
        with click.progressbar(all_hosts, label="Compiling host information") as hosts_bar:
            compiled_host_info = [self._compile_host_info(host) for host in hosts_bar]
        return compiled_host_info

    def _host_release(self):
        """Delete a specific hostDelete a specific host."""
        hostname = self.last_deployed_host
        self.release(hostname)

    def _set_attributes(self, host_inst, broker_args=None, misc_attrs=None):
        """Extend host object by required parameters and methods."""
        host_inst.__dict__.update(
            {
                "_prov_inst": self,
                "_broker_provider": "Foreman",
                "_broker_args": broker_args,
                "release": self._host_release,
                "execute": self._host_execute,
            }
        )

    def construct_host(self, provider_params, host_classes, **kwargs):
        """Construct a broker host from a Foreman host.

        :param provider_params: a container instance object

        :param host_classes: host object

        :return: broker object of constructed host instance
        """
        logger.debug(f"constructing with {provider_params=}\n{host_classes=}\n{kwargs=}")
        if not provider_params:
            host_inst = host_classes[kwargs.get("type", "host")](**kwargs)
            self._set_attributes(host_inst, broker_args=kwargs)
            return host_inst
        name = provider_params["name"]
        host_inst = host_classes["host"](
            **{
                **kwargs,
                "hostname": name,
                "name": name,
            }
        )
        self._set_attributes(host_inst, broker_args=kwargs)
        return host_inst

    def _gen_name(self):
        return f"{self.prefix}-{str(uuid4()).split('-')[0]}"

    @Provider.register_action("hostgroup")
    def create_host(self, hostgroup, **host):
        """Create a new Foreman host.

        :param host: additional parameters for host creation

        :return: Foreman's response from host creation
        """
        if not host.get("name"):
            host["name"] = self._gen_name()

        logger.debug(f"Creating host {host['name']} from hostgroup '{hostgroup}'")

        host["hostgroup_id"] = self.runtime.obtain_id_from_name("hostgroups", hostgroup)
        host["build"] = True
        host["compute_attributes"] = {"start": "1"}
        host["organization_id"] = self.organization_id
        host["location_id"] = self.location_id

        image_name = host.pop("image", False)
        compute_resource_name = host.pop("computeresource", False)
        if image_name and compute_resource_name:
            host["compute_resource_id"] = self.runtime.obtain_id_from_name(
                "compute_resources", compute_resource_name
            )
            host["compute_attributes"]["image_id"] = self.runtime.image_uuid(
                host["compute_resource_id"], image_name
            )
            host["provision_method"] = "image"

            logger.debug(
                "Setting parameters for image based deployment: {\n"
                f"  compute_resource_id: {host['compute_resource_id']},\n"
                f"  image_id: {host['compute_attributes']['image_id']},\n"
                f"  provision_method: {host['provision_method']}\n"
                "}"
            )

        data = {
            "organization_id": self.organization_id,
            "location_id": self.location_id,
            "host": host,
        }
        result = self.runtime.create_host(data)

        self.runtime.wait_for_host_to_install(result["name"])
        self.last_deployed_host = result["name"]

        # set hostname = hostname -f
        self.execute(
            hostname=result["name"],
            job_template="Run Command - Script Default",
            command=f"hostname {result['name']}",
        )
        return result
