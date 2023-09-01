"""A wrapper around the Beaker CLI."""
import json
from pathlib import Path
import subprocess
import time
from xml.etree import ElementTree as ET

from logzero import logger

from broker import helpers
from broker.exceptions import BeakerBindError


def _elementree_to_dict(etree):
    """Convert an ElementTree object to a dictionary."""
    data = {}
    if etree.attrib:
        data.update(etree.attrib)
    if etree.text:
        data["text"] = etree.text
    for child in etree:
        child_data = _elementree_to_dict(child)
        if (tag := child.tag) in data:
            if not isinstance(data[tag], list):
                data[tag] = [data[tag]]
            data[tag].append(child_data)
        else:
            data[tag] = child_data
    return data


def _curate_job_info(job_info_dict):
    curated_info = {
        "job_id": "id",
        # "reservation_id": "current_reservation/recipe_id",
        "whiteboard": "whiteboard/text",
        "hostname": "recipeSet/recipe/system",
        "distro": "recipeSet/recipe/distro",
    }
    return helpers.dict_from_paths(job_info_dict, curated_info)


class BeakerBind:
    """A bind class providing a basic interface to the Beaker CLI."""

    def __init__(self, hub_url, auth="krbv", **kwargs):
        self.hub_url = hub_url
        self._base_args = ["--insecure", f"--hub={self.hub_url}"]
        if auth == "basic":
            # If we're not using system kerberos auth, add in explicit basic auth
            self.username = kwargs.pop("username", None)
            self.password = kwargs.pop("password", None)
            self._base_args.extend(
                [
                    f"--username {self.username}",
                    f"--password {self.password}",
                ]
            )
        self.__dict__.update(kwargs)

    def _exec_command(self, *cmd_args, **cmd_kwargs):
        """Execute a beaker command and return the result.

        cmd_args: Expanded into feature flags for the beaker command
        cmd_kwargs: Expanded into args and values for the beaker command
        """
        raise_on_error = cmd_kwargs.pop("raise_on_error", True)
        exec_cmd, cmd_args = ["bkr"], list(cmd_args)
        # check through kwargs and if any are True add to cmd_args
        del_keys = []
        for k, v in cmd_kwargs.items():
            if isinstance(v, bool) or v is None:
                del_keys.append(k)
            if v is True:
                cmd_args.append(f"--{k}" if not k.startswith("--") else k)
        for k in del_keys:
            del cmd_kwargs[k]
        exec_cmd.extend(cmd_args)
        exec_cmd.extend(self._base_args)
        exec_cmd.extend([f"--{k.replace('_', '-')}={v}" for k, v in cmd_kwargs.items()])
        logger.debug(f"Executing beaker command: {exec_cmd}")
        proc = subprocess.Popen(
            exec_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = proc.communicate()
        result = helpers.Result(
            stdout=stdout.decode(),
            stderr=stderr.decode(),
            status=proc.returncode,
        )
        if result.status != 0 and raise_on_error:
            raise BeakerBindError(
                f"Beaker command failed:\nCommand={' '.join(exec_cmd)}\nResult={result}",
            )
        logger.debug(f"Beaker command result: {result.stdout}")
        return result

    def job_submit(self, job_xml, wait=False):
        """Submit a job to Beaker and optionally wait for it to complete."""
        # wait behavior seems buggy to me, so best to avoid it
        if not Path(job_xml).exists():
            raise FileNotFoundError(f"Job XML file {job_xml} not found")
        result = self._exec_command("job-submit", job_xml, wait=wait)
        if not wait:
            # get the job id from the output
            # format is "Submitted: ['J:7849837'] where the number is the job id
            for line in result.stdout.splitlines():
                if line.startswith("Submitted:"):
                    return line.split("'")[1].replace("J:", "")

    def job_watch(self, job_id):
        """Watch a job via the job-watch command. This can be buggy."""
        job_id = f"J:{job_id}" if not job_id.startswith("J:") else job_id
        return self._exec_command("job-watch", job_id)

    def job_results(self, job_id, format="beaker-results-xml", pretty=False):
        """Get the results of a job in the specified format."""
        job_id = f"J:{job_id}" if not job_id.startswith("J:") else job_id
        return self._exec_command("job-results", job_id, format=format, prettyxml=pretty)

    def job_clone(self, job_id, wait=False, **kwargs):
        """Clone a job by the specified job id."""
        job_id = f"J:{job_id}" if not job_id.startswith("J:") else job_id
        return self._exec_command("job-clone", job_id, wait=wait, **kwargs)

    def job_list(self, *args, **kwargs):
        """List jobs matching the criteria specified by args and kwargs."""
        return self._exec_command("job-list", *args, **kwargs)

    def job_cancel(self, job_id):
        """Cancel a job by the specified job id."""
        if not job_id.startswith("J:") and not job_id.startswith("RS:"):
            job_id = f"J:{job_id}"
        return self._exec_command("job-cancel", job_id)

    def job_delete(self, job_id):
        """Delete a job by the specified job id."""
        job_id = f"J:{job_id}" if not job_id.startswith("J:") else job_id
        return self._exec_command("job-delete", job_id)

    def system_release(self, system_id):
        """Release a system by the specified system id."""
        return self._exec_command("system-release", system_id)

    def system_list(self, **kwargs):
        """Due to the number of arguments, we will not validate before submitting.

        Accepted arguments are:
        available                       available to be used by this user
        free                            available to this user and not currently being used
        removed                         which have been removed
        mine                            owned by this user
        type=TYPE                       of TYPE
        status=STATUS                   with STATUS
        pool=POOL                       in POOL
        arch=ARCH                       with ARCH
        dev-vendor-id=VENDOR-ID         with a device that has VENDOR-ID
        dev-device-id=DEVICE-ID         with a device that has DEVICE-ID
        dev-sub-vendor-id=SUBVENDOR-ID  with a device that has SUBVENDOR-ID
        dev-sub-device-id=SUBDEVICE-ID  with a device that has SUBDEVICE-ID
        dev-driver=DRIVER               with a device that has DRIVER
        dev-description=DESCRIPTION     with a device that has DESCRIPTION
        xml-filter=XML                  matching the given XML filter
        host-filter=NAME                matching pre-defined host filter
        """
        # convert the flags passed in kwargs to arguments
        args = [
            f"--{key}" for key in ("available", "free", "removed", "mine") if kwargs.pop(key, False)
        ]
        return self._exec_command("system-list", *args, **kwargs)

    def user_systems(self):
        """Return a list of system ids owned by the current user.

        This is used for inventory syncing against Beaker.
        """
        result = self.system_list(mine=True, raise_on_error=False)
        if result.status != 0:
            return []
        else:
            return result.stdout.splitlines()

    def system_details(self, system_id, format="json"):
        """Get details about a system by the specified system id."""
        return self._exec_command("system-details", system_id, format=format)

    def execute_job(self, job, max_wait="24h"):
        """Submit a job, periodically checking the status until it completes.

        return: a dictionary of the results.
        """
        if Path(job).exists():  # job xml path passed in
            job_id = self.job_submit(job, wait=False)
        else:  # using a job id
            job_id = self.job_clone(job)
        logger.info(f"Submitted job: {job_id}")
        _max_wait = time.time() + helpers.translate_timeout(max_wait or "24h")
        while time.time() < _max_wait:
            time.sleep(60)
            result = self.job_results(job_id, pretty=True)
            if 'result="Pass"' in result.stdout:
                return _curate_job_info(_elementree_to_dict(ET.fromstring(result.stdout)))
            elif 'result="Fail"' in result.stdout or "Exception: " in result.stdout:
                raise BeakerBindError(f"Job {job_id} failed:\n{result}")
            elif 'result="Warn"' in result.stdout:
                res_dict = _elementree_to_dict(ET.fromstring(result.stdout))
                raise BeakerBindError(
                    f"Job {job_id} was resulted in a warning. Status: {res_dict['status']}"
                )
        raise BeakerBindError(f"Job {job_id} did not complete within {max_wait}")

    def system_details_curated(self, system_id):
        """Return a curated dictionary of system details."""
        full_details = json.loads(self.system_details(system_id).stdout)
        curated_details = {
            "hostname": full_details["fqdn"],
            "mac_address": full_details["mac_address"],
            "owner": "{display_name} <{email_address}>".format(
                display_name=full_details["owner"]["display_name"],
                email_address=full_details["owner"]["email_address"],
            ),
            "id": full_details["id"],
        }
        if current_res := full_details.get("current_reservation"):
            curated_details.update(
                {
                    "reservation_id": current_res["recipe_id"],
                    "reserved_on": current_res.get("start_time"),
                    "expires_on": current_res.get("finish_time"),
                    "reserved_for": "{display_name} <{email_address}>".format(
                        display_name=current_res["user"]["display_name"],
                        email_address=current_res["user"]["email_address"],
                    ),
                }
            )
        return curated_details

    def jobid_from_system(self, system_hostname):
        """Return the job id for the current reservation on the system."""
        for job_id in json.loads(self.job_list(mine=True).stdout):
            job_result = self.job_results(job_id, pretty=True)
            job_detail = _curate_job_info(_elementree_to_dict(ET.fromstring(job_result.stdout)))
            if job_detail["hostname"] == system_hostname:
                return job_id
