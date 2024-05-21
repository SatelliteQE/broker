"""Foreman provider implementation."""
import inspect
from uuid import uuid4

import click
from dynaconf import Validator
from logzero import logger

from broker.binds import foreman
from broker.helpers import Result
from broker.providers import Provider
from broker.settings import settings


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
            self._runtime_cls = foreman.ForemanBind

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
        hostgroups=False,
        hostgroup=None,
        **kwargs,
    ):
        """Return useful information about Foreman provider."""
        if hostgroups:
            all_hostgroups = self.runtime.hostgroups()
            logger.info(f"On Foreman {self.instance} you have the following hostgroups:")
            for hostgroup in all_hostgroups["results"]:
                logger.info(f"- {hostgroup['title']}")
        elif hostgroup:
            logger.info(
                f"On Foreman {self.instance} the hostgroup {hostgroup} has the following properties:"
            )
            data = self.runtime.hostgroup(name=hostgroup)
            fields_of_interest = {
                "description": "description",
                "operating_system": "operatingsystem_name",
                "domain": "domain_name",
                "subnet": "subnet_name",
                "subnet6": "subnet6_name",
            }
            for name, field in fields_of_interest.items():
                value = data.get(field, False)
                if value:
                    logger.info(f"  {name}: {value}")

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
        caller_host = inspect.stack()[1][0].f_locals["host"].hostname
        self.release(caller_host)

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
