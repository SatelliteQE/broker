"""Foreman provider implementation."""
import time

from logzero import logger
import requests

from broker import exceptions
from broker.settings import settings


class ForemanBind:
    """Default runtime to query Foreman."""

    headers = {
        "Content-Type": "application/json",
    }

    def __init__(self, **kwargs):
        self.foreman_username = kwargs.get("foreman_username", settings.foreman.foreman_username)
        self.foreman_password = kwargs.get("foreman_password", settings.foreman.foreman_password)
        self.url = kwargs.get("url", settings.foreman.foreman_url)
        self.prefix = kwargs.get("prefix", settings.foreman.name_prefix)
        self.verify = kwargs.get("verify", settings.foreman.verify)

        self.session = requests.session()

    def _interpret_response(self, response):
        """Handle responses from Foreman, in particular catch errors."""
        if "error" in response:
            error = response["error"]
            message = error.get("message")
            if message is not None and "Unable to authenticate user" in message:
                raise exceptions.AuthenticationError(message)
            raise exceptions.ForemanBindError(
                " ".join(error["full_messages"]),
            )
        if "errors" in response:
            raise exceptions.ForemanBindError(" ".join(response["errors"]["base"]))
        return response

    def _get(self, endpoint):
        """Send GET request to Foreman API."""
        response = self.session.get(
            self.url + endpoint,
            auth=(self.foreman_username, self.foreman_password),
            headers=self.headers,
            verify=self.verify,
        ).json()
        return self._interpret_response(response)

    def _post(self, endpoint, **kwargs):
        """Send POST request to Foreman API."""
        response = self.session.post(
            self.url + endpoint,
            auth=(self.foreman_username, self.foreman_password),
            headers=self.headers,
            verify=self.verify,
            **kwargs,
        ).json()
        return self._interpret_response(response)

    def _delete(self, endpoint, **kwargs):
        """Send DELETE request to Foreman API."""
        response = self.session.delete(
            self.url + endpoint,
            auth=(self.foreman_username, self.foreman_password),
            headers=self.headers,
            verify=self.verify,
            **kwargs,
        )
        return self._interpret_response(response)

    def obtain_id_from_name(self, resource_type, resource_name):
        """Obtain id for resource with given name.

        :param resource_type: Resource type, like hostgroups, hosts, ...

        :param resource_name: String-like identifier of the resource

        :return: ID of the found object
        """
        response = self._get(
            f"/api/{resource_type}?per_page=200",
        )
        try:
            result = response["results"]
            resource = next(
                x
                for x in result
                if x.get("title") == resource_name or x.get("name") == resource_name
            )
            id_ = resource["id"]
        except KeyError:
            logger.error(f"Could not find {resource_type} {resource_name}")
            raise
        except StopIteration:
            raise exceptions.ForemanBindError(
                f"Could not find {resource_name} in {resource_type}",
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

    def hostgroups(self):
        """Return list of available hostgroups."""
        return self._get("/api/hostgroups")

    def hostgroup(self, name):
        """Return list of available hostgroups."""
        hostgroup_id = self.obtain_id_from_name("hostgroups", name)
        return self._get(f"/api/hostgroups/{hostgroup_id}")

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
            raise exceptions.ForemanBindError(f"Could not find {image_name} in VM images")

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
