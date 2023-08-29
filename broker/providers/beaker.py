"""Beaker provider implementation."""
import inspect

import click
from dynaconf import Validator
from logzero import logger

from broker import helpers
from broker.binds.beaker import BeakerBind
from broker.exceptions import BrokerError, ProviderError
from broker.hosts import Host
from broker.providers import Provider
from broker.settings import settings


@Provider.auto_hide
class Beaker(Provider):
    """Beaker provider class providing a Broker interface around the Beaker bind."""

    _validators = [
        Validator("beaker.hub_url", must_exist=True),
        Validator("beaker.max_job_wait", default="24h"),
    ]
    _checkout_options = [
        click.option(
            "--job-xml",
            type=click.Path(exists=True, dir_okay=False),
            help="Path to the job XML file to submit",
        ),
        click.option(
            "--job-id",
            type=str,
            help="Beaker job ID to clone",
        ),
    ]
    _execute_options = [
        click.option(
            "--job-xml",
            type=str,
            help="Path to the job XML file to submit",
        ),
    ]
    _extend_options = [
        click.option(
            "--extend-duration",
            type=click.IntRange(1, 99),
            help="Number of hours to extend the job. Must be between 1 and 99",
        )
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.hub_url = settings.beaker.hub_url
        self.runtime = kwargs.pop("bind", BeakerBind)(self.hub_url, **kwargs)

    def _host_release(self):
        caller_host = inspect.stack()[1][0].f_locals["host"]
        if not (job_id := getattr(caller_host, "job_id", None)):
            job_id = self.runtime.jobid_from_system(caller_host.hostname)
        return self.release(caller_host.hostname, job_id)

    def _set_attributes(self, host_inst, broker_args=None, misc_attrs=None):
        host_inst.__dict__.update(
            {
                "_prov_inst": self,
                "_broker_provider": "Beaker",
                "_broker_provider_instance": self.instance,
                "_broker_args": broker_args,
                "release": self._host_release,
            }
        )
        if isinstance(misc_attrs, dict):
            host_inst._attrs = misc_attrs

    def _compile_host_info(self, host, broker_info=True):
        """Compiles host information into a dictionary suitable for use in the inventory.

        :param host (beaker.host.Host): The host to compile information for.

        :return: A dictionary containing the compiled host information.
        """
        curated_host_info = self.runtime.system_details_curated(host)
        if broker_info:
            curated_host_info.update(
                {
                    "_broker_provider": "Beaker",
                    "_broker_provider_instance": self.instance,
                    "_broker_args": getattr(host, "_broker_args", {}),
                }
            )
        if not curated_host_info.get("job_id"):
            curated_host_info["job_id"] = self.runtime.jobid_from_system(
                curated_host_info["hostname"]
            )
        return curated_host_info

    def construct_host(self, provider_params, host_classes, **kwargs):
        """Construct a broker host from a beaker system information.

        :param provider_params: a beaker system information dictionary

        :param host_classes: host object

        :return: constructed broker host object
        """
        logger.debug(f"constructing with {provider_params=}\n{host_classes=}\n{kwargs=}")
        if not provider_params:
            host_inst = host_classes[kwargs.get("type", "host")](**kwargs)
            # cont_inst = self._cont_inst_by_name(host_inst.name)
            self._set_attributes(host_inst, broker_args=kwargs)
        else:
            host_info = self._compile_host_info(provider_params["hostname"], broker_info=False)
            host_inst = host_classes[kwargs.get("type", "host")](**provider_params)
            self._set_attributes(host_inst, broker_args=kwargs, misc_attrs=host_info)
        return host_inst

    @Provider.register_action("job_xml", "job_id")
    def submit_job(self, max_wait=None, **kwargs):
        """Submit a job to Beaker and wait for it to complete."""
        job = kwargs.get("job_xml") or kwargs.get("job_id")
        max_wait = max_wait or settings.beaker.get("max_job_wait")
        result = self.runtime.execute_job(job, max_wait)
        logger.debug(f"Job completed with results: {result}")
        return result

    def provider_help(self, jobs=False, job=None, **kwargs):
        """Print useful information from the Beaker provider."""
        results_limit = kwargs.get("results_limit", settings.container.results_limit)
        if job:
            if not job.startswith("J:"):
                job = f"J:{job}"
            logger.info(self.runtime.job_clone(job, prettyxml=True, dryrun=True).stdout)
        elif jobs:
            result = self.runtime.job_list(**kwargs).stdout.splitlines()
            if res_filter := kwargs.get("results_filter"):
                result = helpers.eval_filter(result, res_filter, "res")
            result = "\n".join(result[:results_limit])
            logger.info(f"Available jobs:\n{result}")

    def release(self, host_name, job_id):
        """Release a hosts reserved from Beaker by cancelling the job."""
        return self.runtime.job_cancel(job_id)
        # return self.runtime.system_release(host_name)

    def extend(self, host_name, extend_duration=99):
        """Extend the duration of a Beaker reservation."""
        try:
            Host(hostname=host_name).execute(f"/usr/bin/extendtesttime.sh {extend_duration}")
        except BrokerError as err:
            raise ProviderError(
                f"Failed to extend host {host_name}: {err}\n"
                f"Try running: root@{host_name} /usr/bin/extendtesttime.sh {extend_duration}"
            ) from err

    def get_inventory(self, *args):
        """Get a list of hosts and their information from Beaker."""
        hosts = self.runtime.user_systems()
        with click.progressbar(hosts, label="Compiling host information") as hosts_bar:
            compiled_host_info = [self._compile_host_info(host) for host in hosts_bar]
        return compiled_host_info
