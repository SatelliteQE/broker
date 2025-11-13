"""OpenStack provider implementation."""

from contextlib import contextmanager
import inspect
import logging
from uuid import uuid4

import click
from dynaconf import Validator

logger = logging.getLogger(__name__)

from broker import exceptions, helpers
from broker.providers import Provider

# Deferred imports - will be imported when first OpenStack instance is created
openstack = None
OpenStackCloudException = None
ResourceNotFound = None


@contextmanager
def _suppress_retry_warnings():
    """Suppress retry warnings during server creation."""
    original_level = logger.level
    logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        logger.setLevel(original_level)


class OpenStack(Provider):
    """OpenStack provider class providing a Broker interface around OpenStack SDK."""

    _validators = [
        Validator("OPENSTACK.server_timeout", default=600),
        Validator("OPENSTACK.user_domain_name", default="Default"),
        Validator("OPENSTACK.project_domain_name", default="Default"),
        Validator("OPENSTACK.identity_api_version", default="3"),
    ]
    _checkout_options = [
        click.option("--ostack-image", help="Image name or UUID for the VM"),
        click.option("--ostack-flavor", help="Flavor for the VM"),
        click.option("--ostack-network", help="Network UUID for the VM"),
        click.option("--ostack-key-name", help="SSH key pair name"),
        click.option("--ostack-template", help="Template name from configuration"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Import openstack dependencies on first use
        global openstack, OpenStackCloudException, ResourceNotFound  # noqa: PLW0603
        if openstack is None:
            try:
                import openstack
                from openstack.exceptions import OpenStackCloudException, ResourceNotFound
            except ImportError as e:
                raise ImportError(
                    "openstacksdk is required for the OpenStack provider. "
                    "Please install it with 'pip install openstacksdk' or 'pip install broker[openstack]'"
                ) from e
        self._connection = None

    @property
    def connection(self):
        """Get OpenStack connection."""
        if self._connection is None:
            self._connection = self._get_connection()
        return self._connection

    @connection.setter
    def connection(self, value):
        """Set OpenStack connection."""
        self._connection = value

    def _get_connection_with_cloud(self, cloud_name):
        """Connect using cloud configuration."""
        logger.debug(f"Connecting to OpenStack cloud: {cloud_name}")
        return openstack.connect(cloud=cloud_name)

    def _get_connection_with_app_credentials(self, auth_url):
        """Connect using application credentials."""
        app_cred_id = getattr(self._settings.OPENSTACK, "application_credential_id", None)
        app_cred_secret = getattr(self._settings.OPENSTACK, "application_credential_secret", None)

        if not (app_cred_id and app_cred_secret):
            return None

        auth_config = {
            "auth_url": auth_url,
            "application_credential_id": app_cred_id,
            "application_credential_secret": app_cred_secret,
            "identity_api_version": self._settings.OPENSTACK.identity_api_version,
        }

        for param in ["region_name", "interface"]:
            if hasattr(self._settings.OPENSTACK, param):
                auth_config[param] = getattr(self._settings.OPENSTACK, param)

        logger.debug(f"Connecting with app credentials: {auth_url}")
        return openstack.connect(**auth_config)

    def _get_connection_with_password(self, auth_url):
        """Connect with username/password."""
        username = getattr(self._settings.OPENSTACK, "username", None)
        password = getattr(self._settings.OPENSTACK, "password", None)
        project_name = getattr(self._settings.OPENSTACK, "project_name", None)

        if not all([username, password, project_name]):
            return None

        auth_config = {
            "auth_url": auth_url,
            "username": username,
            "password": password,
            "project_name": project_name,
            "user_domain_name": self._settings.OPENSTACK.user_domain_name,
            "project_domain_name": self._settings.OPENSTACK.project_domain_name,
            "identity_api_version": self._settings.OPENSTACK.identity_api_version,
        }

        for param in ["region_name", "interface"]:
            if hasattr(self._settings.OPENSTACK, param):
                auth_config[param] = getattr(self._settings.OPENSTACK, param)

        logger.debug(f"Connecting with username/password: {auth_url}")
        return openstack.connect(**auth_config)

    def _get_connection(self):
        """Establish OpenStack connection."""
        try:
            cloud_name = getattr(self._settings.OPENSTACK, "cloud", None)
            if cloud_name:
                return self._get_connection_with_cloud(cloud_name)

            auth_url = getattr(self._settings.OPENSTACK, "auth_url", None)
            if not auth_url:
                raise exceptions.ConfigurationError(
                    "OpenStack config incomplete. Provide 'cloud' or 'auth_url'"
                )

            connection = self._get_connection_with_app_credentials(auth_url)
            if connection:
                return connection

            connection = self._get_connection_with_password(auth_url)
            if connection:
                return connection

            raise exceptions.ConfigurationError(
                "OpenStack config incomplete. Provide one of:\n"
                "1. cloud (clouds.yaml)\n"
                "2. auth_url + app credentials\n"
                "3. auth_url + username/password"
            )
        except (OpenStackCloudException, exceptions.ConfigurationError) as e:
            logger.error(f"Failed to connect to OpenStack: {e!s}")
            raise exceptions.ProviderError("OpenStack", f"Failed to connect: {e!s}") from e

    def _host_release(self):
        """Release method for host objects."""
        caller_host = inspect.stack()[1][0].f_locals["host"]
        return self.release(caller_host)

    def _set_attributes(self, host_inst, broker_args=None):
        """Set host attributes."""
        host_inst.__dict__.update(
            {
                "_prov_inst": self,
                "_broker_provider": "OpenStack",
                "_broker_provider_instance": self.instance,
                "_broker_args": broker_args,
                "release": self._host_release,
            }
        )

        if broker_args and "instance_id" in broker_args and not hasattr(host_inst, "instance_id"):
            host_inst.instance_id = broker_args["instance_id"]

    def provider_help(self, images=False, flavors=False, networks=False, templates=False, **kwargs):
        """Display OpenStack provider information."""
        if images:
            logger.info("Available images:")
            for image in self.connection.image.images():
                if image.status == "active":
                    logger.info(f"  - {image.name} ({image.id})")
        elif flavors:
            logger.info("Available flavors:")
            for flavor in self.connection.compute.flavors():
                logger.info(f"  - {flavor.name} ({flavor.id})")
        elif networks:
            logger.info("Available networks:")
            for network in self.connection.network.networks():
                logger.info(f"  - {network.name} ({network.id})")
        elif templates:
            templates = self._settings.OPENSTACK.get("templates", {})
            if templates:
                logger.info("Available templates:")
                for template_name, template_config in templates.items():
                    logger.info(f"  - {template_name}: {template_config}")
            else:
                logger.info("No templates configured")
        else:
            logger.info("OpenStack provider configured and connected")

    def construct_host(self, provider_params, host_classes, **kwargs):
        """Construct a host object from the provider_params and kwargs."""
        logger.debug(f"constructing with {provider_params=}\n{host_classes=}\n{kwargs=}")

        if provider_params:
            host_inst = host_classes[kwargs.get("type", "host")](**provider_params)
            if "instance_id" in provider_params:
                host_inst.instance_id = provider_params["instance_id"]
        else:
            host_inst = host_classes[kwargs.get("type", "host")](**kwargs)
            if "instance_id" in kwargs:
                host_inst.instance_id = kwargs["instance_id"]

        self._set_attributes(host_inst, broker_args=kwargs)

        if not hasattr(host_inst, "instance_id") or not host_inst.instance_id:
            logger.debug(
                f"Host object {host_inst} does not have instance_id set. Available attributes: {dir(host_inst)}"
            )

        return host_inst

    def _get_template_params(self, template_name, kwargs):
        """Get parameters from template configuration."""
        templates = self._settings.OPENSTACK.get("templates", {})
        if template_name not in templates:
            raise exceptions.UserError(
                f"Template '{template_name}' not found in OpenStack configuration"
            )

        template = templates[template_name]
        params = template.copy()
        param_mapping = {
            "ostack_image": "image",
            "ostack_flavor": "flavor",
            "ostack_network": "network",
            "ostack_key_name": "key_name",
        }
        for cli_key, param_key in param_mapping.items():
            if cli_key in kwargs:
                params[param_key] = kwargs[cli_key]
        return params

    def _get_direct_params(self, kwargs):
        """Get direct parameters with defaults."""
        params = {}

        param_mapping = {
            "ostack_image": "image",
            "ostack_flavor": "flavor",
            "ostack_network": "network",
            "ostack_key_name": "key_name",
            "name": "name",
        }

        for cli_key, param_key in param_mapping.items():
            if cli_key in kwargs:
                params[param_key] = kwargs[cli_key]

        default_mapping = {
            "image": "default_image",
            "flavor": "default_flavor",
            "network": "default_network",
            "key_name": "default_key_name",
        }

        for param, default_key in default_mapping.items():
            if param not in params:
                params[param] = getattr(self._settings.OPENSTACK, default_key, None)

        return params

    @Provider.register_action("ostack_template", "ostack_image")
    def checkout(self, **kwargs):
        """Create an OpenStack instance."""
        logger.debug(f"Checkout kwargs: {kwargs}")

        template_name = kwargs.get("ostack_template")

        if template_name:
            params = self._get_template_params(template_name, kwargs)
        else:
            params = self._get_direct_params(kwargs)

        required_params = ["image", "flavor", "network"]
        missing_params = [param for param in required_params if not params.get(param)]

        if missing_params:
            missing_str = ", ".join(missing_params)
            raise exceptions.UserError(
                f"Missing required OpenStack parameters: {missing_str}. "
                f"Provide them directly or via template/defaults in configuration."
            )

        logger.info(
            f"Creating OpenStack server with image: {params['image']}, "
            f"flavor: {params['flavor']}, network: {params['network']}"
        )

        try:
            image_id = self._resolve_image(params["image"])
            flavor_id = self._resolve_flavor(params["flavor"])
            network_id = self._resolve_network(params["network"])

            server_params = {
                "name": params.get("name", f"broker-{str(uuid4()).split('-')[0]}"),
                "image_id": image_id,
                "flavor_id": flavor_id,
                "networks": [{"uuid": network_id}],
                "wait": False,
            }

            if params.get("key_name"):
                server_params["key_name"] = params["key_name"]

            logger.debug(f"Server creation parameters: {server_params}")

            server = self.connection.compute.create_server(**server_params)
            logger.info(f"Server creation initiated: {server.id}")

            server_id = server.id
            timeout = self._settings.OPENSTACK.server_timeout

            def check_server():
                s = self.connection.compute.get_server(server_id)
                logger.debug(f"Server {server_id} status: {s.status}")
                if s.status == "ACTIVE":
                    logger.info(f"Server {server_id} is now active")
                    return s
                elif s.status == "ERROR":
                    raise exceptions.ProviderError(
                        "OpenStack", f"Server {server_id} failed to build"
                    )
                else:
                    logger.info(f"Server {server_id} building... (status: {s.status})")
                    raise Exception("Server not ready yet")

            with _suppress_retry_warnings():
                server = helpers.simple_retry(
                    check_server,
                    max_timeout=timeout,
                    terminal_exceptions=(exceptions.ProviderError,),
                )

            if not server:
                raise exceptions.ProviderError(
                    "OpenStack", f"Failed to retrieve server {server_id} after creation"
                )

            ip_address = self._get_ip_address(server)
            logger.info(f"Server {server.id} assigned IP: {ip_address}")

            provider_params = {
                "hostname": ip_address,
                "instance_id": server.id,
                "name": server.name,
            }

            return provider_params

        except (OpenStackCloudException, ResourceNotFound) as e:
            logger.error(f"Failed to create OpenStack server: {e!s}")
            raise exceptions.ProviderError("OpenStack", f"Failed to create server: {e!s}") from e

    def _get_ip_address(self, server):
        """Extract IP address from server object."""
        for addresses in server.addresses.values():
            for address in addresses:
                if address.get("OS-EXT-IPS:type") == "floating":
                    return address["addr"]

        for addresses in server.addresses.values():
            for address in addresses:
                return address["addr"]

        return server.name

    def _resolve_image(self, image_name_or_id):
        """Resolve image name to UUID."""
        try:
            try:
                image = self.connection.image.get_image(image_name_or_id)
                logger.debug(f"Image resolved by ID: {image_name_or_id}")
                return image.id
            except (ResourceNotFound, OpenStackCloudException):
                logger.debug(f"Image '{image_name_or_id}' not found by ID, trying name lookup")

            images = list(self.connection.image.images(name=image_name_or_id))
            if images:
                image_id = images[0].id
                logger.debug(f"Image '{image_name_or_id}' resolved to ID: {image_id}")
                return image_id

            all_images = list(self.connection.image.images())
            matching_images = [img for img in all_images if image_name_or_id in img.name]

            if matching_images:
                image_id = matching_images[0].id
                logger.debug(
                    f"Image '{image_name_or_id}' partially matched to '{matching_images[0].name}' (ID: {image_id})"
                )
                return image_id

            raise exceptions.UserError(f"Image '{image_name_or_id}' not found in OpenStack")

        except OpenStackCloudException as e:
            logger.error(f"Failed to resolve image '{image_name_or_id}': {e!s}")
            raise exceptions.ProviderError("OpenStack", f"Failed to resolve image: {e!s}") from e

    def _resolve_flavor(self, flavor_name_or_id):
        """Resolve flavor name to UUID."""
        try:
            try:
                flavor = self.connection.compute.get_flavor(flavor_name_or_id)
                logger.debug(f"Flavor resolved by ID: {flavor_name_or_id}")
                return flavor.id
            except (ResourceNotFound, OpenStackCloudException):
                logger.debug(f"Flavor '{flavor_name_or_id}' not found by ID, trying name lookup")

            flavors = list(self.connection.compute.flavors(name=flavor_name_or_id))
            if flavors:
                flavor_id = flavors[0].id
                logger.debug(f"Flavor '{flavor_name_or_id}' resolved to ID: {flavor_id}")
                return flavor_id

            raise exceptions.UserError(f"Flavor '{flavor_name_or_id}' not found in OpenStack")

        except OpenStackCloudException as e:
            logger.error(f"Failed to resolve flavor '{flavor_name_or_id}': {e!s}")
            raise exceptions.ProviderError("OpenStack", f"Failed to resolve flavor: {e!s}") from e

    def _resolve_network(self, network_name_or_id):
        """Resolve network name to UUID."""
        try:
            try:
                network = self.connection.network.get_network(network_name_or_id)
                logger.debug(f"Network resolved by ID: {network_name_or_id}")
                return network.id
            except (ResourceNotFound, OpenStackCloudException):
                logger.debug(f"Network '{network_name_or_id}' not found by ID, trying name lookup")

            networks = list(self.connection.network.networks(name=network_name_or_id))
            if networks:
                network_id = networks[0].id
                logger.debug(f"Network '{network_name_or_id}' resolved to ID: {network_id}")
                return network_id

            raise exceptions.UserError(f"Network '{network_name_or_id}' not found in OpenStack")

        except OpenStackCloudException as e:
            logger.error(f"Failed to resolve network '{network_name_or_id}': {e!s}")
            raise exceptions.ProviderError("OpenStack", f"Failed to resolve network: {e!s}") from e

    def _find_server_by_name_or_ip(self, hostname, name):
        """Find OpenStack server by name or IP address."""
        try:
            servers = list(self.connection.compute.servers(name=name))
            if servers:
                logger.info(f"Found server by name '{name}': {servers[0].id}")
                return servers[0].id

            all_servers = list(self.connection.compute.servers())
            for server in all_servers:
                for addresses in server.addresses.values():
                    for address in addresses:
                        if address.get("addr") == hostname:
                            logger.info(f"Found server by IP '{hostname}': {server.id}")
                            return server.id
            return None
        except OpenStackCloudException as e:
            logger.warning(f"Failed to search for server: {e}")
            return None

    def release(self, host_obj):
        """Release (delete) an OpenStack server."""
        instance_id = None

        if hasattr(host_obj, "instance_id") and host_obj.instance_id:
            instance_id = host_obj.instance_id
        elif hasattr(host_obj, "server_id") and host_obj.server_id:
            instance_id = host_obj.server_id
        elif hasattr(host_obj, "id") and host_obj.id:
            instance_id = host_obj.id
        elif hasattr(host_obj, "_broker_args") and host_obj._broker_args:
            logger.debug(f"Checking _broker_args: {host_obj._broker_args}")
            if isinstance(host_obj._broker_args, dict):
                instance_id = host_obj._broker_args.get("instance_id")

        if not instance_id:
            logger.info(f"Attempting to find OpenStack server by hostname: {host_obj.hostname}")
            instance_id = self._find_server_by_name_or_ip(host_obj.hostname, host_obj.name)

        if not instance_id:
            logger.error(
                f"Cannot find instance ID for host {host_obj}. Available attributes: {dir(host_obj)}"
            )
            raise exceptions.ProviderError(
                "OpenStack", f"Cannot find instance ID for host {host_obj}"
            )

        try:
            logger.info(f"Releasing OpenStack server: {instance_id}")
            self.connection.compute.delete_server(instance_id)
            logger.info(f"Server {instance_id} deleted successfully")
            return True
        except OpenStackCloudException as e:
            logger.error(f"Failed to delete server {instance_id}: {e!s}")
            raise exceptions.ProviderError("OpenStack", f"Failed to delete server: {e!s}") from e

    def extend(self, host_obj):
        """Extend lease time for an OpenStack server (if supported)."""
        raise NotImplementedError(
            "Extend operation is not implemented for OpenStack provider. "
            "OpenStack doesn't have a built-in lease extension concept."
        )

    def get_inventory(self, **kwargs):
        """Get inventory of OpenStack servers."""
        try:
            servers = list(self.connection.compute.servers())
            inventory = []

            for server in servers:
                ip_address = self._get_ip_address(server)
                inventory.append(
                    {
                        "hostname": ip_address,
                        "instance_id": server.id,
                        "name": server.name,
                        "status": server.status,
                        "provider": "OpenStack",
                    }
                )

            return inventory
        except OpenStackCloudException as e:
            logger.error(f"Failed to get OpenStack inventory: {e!s}")
            raise exceptions.ProviderError("OpenStack", f"Failed to get inventory: {e!s}") from e
