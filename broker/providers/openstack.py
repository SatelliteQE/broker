"""OpenStack provider implementation."""
import inspect
import time
from uuid import uuid4

import click
from dynaconf import Validator
from logzero import logger

from broker import exceptions
from broker.providers import Provider
from broker.settings import settings

try:
    import openstack
    from openstack.exceptions import OpenStackCloudException, ResourceNotFound
except ImportError as e:
    raise ImportError(
        "openstacksdk is required for the OpenStack provider. Please install it with 'pip install openstacksdk'"
    ) from e


@Provider.auto_hide
class OpenStack(Provider):
    """OpenStack provider class providing a Broker interface around OpenStack SDK."""

    _validators = [
        # Simple validators with defaults - like other providers
        Validator("OPENSTACK.server_timeout", default=600),  # 10 minutes default
        Validator("OPENSTACK.user_domain_name", default="Default"),
        Validator("OPENSTACK.project_domain_name", default="Default"),
        Validator("OPENSTACK.identity_api_version", default="3"),
    ]
    _checkout_options = [
        click.option("--image", help="Image name or UUID for the VM"),
        click.option("--flavor", help="Flavor for the VM"),
        click.option("--network", help="Network UUID for the VM"),
        click.option("--key-name", help="SSH key pair name"),
        click.option("--template", help="Template name from configuration"),
        click.option("--project", help="Project/tenant name or ID"),
    ]
    _execute_options = []
    _extend_options = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.connection = self._get_connection()

    def _get_connection_with_cloud(self, cloud_name):
        """Connect using cloud configuration."""
        logger.debug(f"Connecting to OpenStack cloud: {cloud_name}")
        return openstack.connect(cloud=cloud_name)

    def _get_connection_with_app_credentials(self, auth_url):
        """Connect using application credentials."""
        app_cred_id = getattr(settings.OPENSTACK, "application_credential_id", None)
        app_cred_secret = getattr(settings.OPENSTACK, "application_credential_secret", None)

        if not (app_cred_id and app_cred_secret):
            return None

        auth_config = {
            "auth_url": auth_url,
            "application_credential_id": app_cred_id,
            "application_credential_secret": app_cred_secret,
            "identity_api_version": settings.OPENSTACK.identity_api_version,
        }

        # Add optional parameters
        for param in ["region_name", "interface"]:
            if hasattr(settings.OPENSTACK, param):
                auth_config[param] = getattr(settings.OPENSTACK, param)

        logger.debug(f"Connecting to OpenStack with application credentials: {auth_url}")
        return openstack.connect(**auth_config)

    def _get_connection_with_password(self, auth_url):
        """Connect using username/password."""
        username = getattr(settings.OPENSTACK, "username", None)
        password = getattr(settings.OPENSTACK, "password", None)
        project_name = getattr(settings.OPENSTACK, "project_name", None)

        if not all([username, password, project_name]):
            return None

        auth_config = {
            "auth_url": auth_url,
            "username": username,
            "password": password,
            "project_name": project_name,
            "user_domain_name": settings.OPENSTACK.user_domain_name,
            "project_domain_name": settings.OPENSTACK.project_domain_name,
            "identity_api_version": settings.OPENSTACK.identity_api_version,
        }

        # Add optional parameters
        for param in ["region_name", "interface"]:
            if hasattr(settings.OPENSTACK, param):
                auth_config[param] = getattr(settings.OPENSTACK, param)

        logger.debug(f"Connecting to OpenStack with username/password: {auth_url}")
        return openstack.connect(**auth_config)

    def _get_connection(self):
        """Establish connection to OpenStack based on instance config."""
        try:
            # Check if using cloud-based auth
            cloud_name = getattr(settings.OPENSTACK, "cloud", None)
            if cloud_name:
                return self._get_connection_with_cloud(cloud_name)

            # Use direct authentication
            auth_url = getattr(settings.OPENSTACK, "auth_url", None)
            if not auth_url:
                raise exceptions.ConfigurationError(
                    "OpenStack configuration incomplete. Either provide 'cloud' name "
                    "or 'auth_url' for direct authentication"
                )

            # Try application credentials first
            connection = self._get_connection_with_app_credentials(auth_url)
            if connection:
                return connection

            # Fall back to username/password
            connection = self._get_connection_with_password(auth_url)
            if connection:
                return connection

            raise exceptions.ConfigurationError(
                "OpenStack configuration incomplete. Provide either:\n"
                "1. 'cloud' name (uses clouds.yaml), OR\n"
                "2. Application credentials: auth_url, application_credential_id, application_credential_secret, OR\n"
                "3. Username/password: auth_url, username, password, project_name"
            )
        except (OpenStackCloudException, exceptions.ConfigurationError) as e:
            logger.error(f"Failed to connect to OpenStack: {e!s}")
            raise exceptions.ProviderError("OpenStack", f"Failed to connect: {e!s}") from e

    def _host_release(self):
        """Release method that will be attached to host objects."""
        caller_host = inspect.stack()[1][0].f_locals["host"]
        return self.release(caller_host)

    def _set_attributes(self, host_inst, broker_args=None):
        """Set broker-specific attributes on the host instance."""
        host_inst.__dict__.update(
            {
                "_prov_inst": self,
                "_broker_provider": "OpenStack",
                "_broker_provider_instance": self.instance,
                "_broker_args": broker_args,
                "release": self._host_release,
            }
        )

        # Ensure instance_id is set if it's available in broker_args
        if broker_args and "instance_id" in broker_args and not hasattr(host_inst, "instance_id"):
            host_inst.instance_id = broker_args["instance_id"]

    def provider_help(self, **kwargs):
        """Print useful information from the OpenStack provider."""
        # This method should not return anything, just log information
        logger.info("OpenStack provider configured")

    def construct_host(self, provider_params, host_classes, **kwargs):
        """Construct a host object from the provider_params and kwargs."""
        logger.debug(f"constructing with {provider_params=}\n{host_classes=}\n{kwargs=}")

        if provider_params:
            # If we have provider_params, use them to construct the host
            host_inst = host_classes[kwargs.get("type", "host")](**provider_params)
            # Ensure instance_id is set from provider_params
            if "instance_id" in provider_params:
                host_inst.instance_id = provider_params["instance_id"]
        else:
            # If reconstructing from inventory or direct parameters
            host_inst = host_classes[kwargs.get("type", "host")](**kwargs)
            # Ensure instance_id is set from kwargs
            if "instance_id" in kwargs:
                host_inst.instance_id = kwargs["instance_id"]

        # Set broker-specific attributes (this will also set instance_id if not already set)
        self._set_attributes(host_inst, broker_args=kwargs)

        # Final check - ensure instance_id is definitely set
        if not hasattr(host_inst, "instance_id") or not host_inst.instance_id:
            logger.debug(
                f"Host object {host_inst} does not have instance_id set. Available attributes: {dir(host_inst)}"
            )

        return host_inst

    def _get_template_params(self, template_name, kwargs):
        """Get parameters from template configuration."""
        templates = settings.OPENSTACK.get("templates", {})
        if template_name not in templates:
            raise exceptions.UserError(
                f"Template '{template_name}' not found in OpenStack configuration"
            )

        template = templates[template_name]
        params = template.copy()
        # Override template with any direct parameters
        for key in ["image", "flavor", "network", "key_name"]:
            if key in kwargs:
                params[key] = kwargs[key]
        return params

    def _get_direct_params(self, kwargs):
        """Get parameters from direct arguments with defaults."""
        params = {}
        for key in ["image", "flavor", "network", "key_name", "name"]:
            if key in kwargs:
                params[key] = kwargs[key]

        # Apply defaults from config if not provided
        default_mapping = {
            "image": "default_image",
            "flavor": "default_flavor",
            "network": "default_network",
            "key_name": "default_key_name",
        }

        for param, default_key in default_mapping.items():
            if param not in params:
                params[param] = getattr(settings.OPENSTACK, default_key, None)

        return params

    @Provider.register_action("template", "image")
    def checkout(self, **kwargs):
        """Create an OpenStack instance."""
        logger.debug(f"Checkout kwargs: {kwargs}")

        # Support both template-based and direct parameter approaches
        template_name = kwargs.get("template")

        if template_name:
            params = self._get_template_params(template_name, kwargs)
        else:
            params = self._get_direct_params(kwargs)

        # Validate required parameters with helpful messages
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
            # Resolve names to UUIDs
            image_id = self._resolve_image(params["image"])
            flavor_id = self._resolve_flavor(params["flavor"])
            network_id = self._resolve_network(params["network"])

            # Prepare server creation parameters
            server_params = {
                "name": params.get("name", f"broker-{str(uuid4()).split('-')[0]}"),
                "imageRef": image_id,
                "flavorRef": flavor_id,
                "networks": [{"uuid": network_id}],
                "wait": False,  # We'll handle waiting ourselves with custom timeout
            }

            # Only add key_name if it's provided and not None
            if params.get("key_name"):
                server_params["key_name"] = params["key_name"]

            logger.debug(f"Server creation parameters: {server_params}")

            # Create the server
            server = self.connection.compute.create_server(**server_params)
            logger.info(f"Server creation initiated: {server.id}")

            # Wait for server to become active with custom timeout
            timeout = settings.OPENSTACK.server_timeout
            start_time = time.time()

            while time.time() - start_time < timeout:
                server = self.connection.compute.get_server(server.id)
                logger.debug(f"Server {server.id} status: {server.status}")

                if server.status == "ACTIVE":
                    logger.info(f"Server {server.id} is now active")
                    break
                elif server.status == "ERROR":
                    raise exceptions.ProviderError(
                        "OpenStack", f"Server {server.id} failed to build"
                    )

                time.sleep(5)  # Poll every 5 seconds
            else:
                raise exceptions.ProviderError(
                    "OpenStack",
                    f"Server {server.id} did not become active within {timeout} seconds",
                )

            # Get IP address
            ip_address = self._get_ip_address(server)
            logger.info(f"Server {server.id} assigned IP: {ip_address}")

            # Prepare provider parameters for host construction
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
        # Try to get IP from server addresses
        for addresses in server.addresses.values():
            for address in addresses:
                # Prefer floating/public IPs, fall back to any IP
                if address.get("OS-EXT-IPS:type") == "floating":
                    return address["addr"]

        # If no floating IP, get first available IP
        for addresses in server.addresses.values():
            for address in addresses:
                return address["addr"]

        # Fallback to server name if no IP found
        return server.name

    def _resolve_image(self, image_name_or_id):
        """Resolve image name to UUID."""
        try:
            # First try to get by ID (if it's already a UUID)
            try:
                image = self.connection.image.get_image(image_name_or_id)
                logger.debug(f"Image resolved by ID: {image_name_or_id}")
                return image.id
            except ResourceNotFound:
                pass

            # Try to find by name
            images = list(self.connection.image.images(name=image_name_or_id))
            if images:
                image_id = images[0].id
                logger.debug(f"Image '{image_name_or_id}' resolved to ID: {image_id}")
                return image_id

            # If not found by exact name, try partial match
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
            # First try to get by ID
            try:
                flavor = self.connection.compute.get_flavor(flavor_name_or_id)
                logger.debug(f"Flavor resolved by ID: {flavor_name_or_id}")
                return flavor.id
            except ResourceNotFound:
                pass

            # Try to find by name
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
            # First try to get by ID
            try:
                network = self.connection.network.get_network(network_name_or_id)
                logger.debug(f"Network resolved by ID: {network_name_or_id}")
                return network.id
            except ResourceNotFound:
                pass

            # Try to find by name
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
            # Try to find server by name
            servers = list(self.connection.compute.servers(name=name))
            if servers:
                logger.info(f"Found server by name '{name}': {servers[0].id}")
                return servers[0].id

            # Try to find by IP address in server addresses
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
        # Try to get the instance ID from various possible attributes
        instance_id = None

        # Check for instance_id first (our preferred attribute)
        if hasattr(host_obj, "instance_id") and host_obj.instance_id:
            instance_id = host_obj.instance_id
        # Check for server_id (alternative name)
        elif hasattr(host_obj, "server_id") and host_obj.server_id:
            instance_id = host_obj.server_id
        # Check for id (generic)
        elif hasattr(host_obj, "id") and host_obj.id:
            instance_id = host_obj.id
        # Check broker args for instance_id
        elif hasattr(host_obj, "_broker_args") and host_obj._broker_args:
            logger.debug(f"Checking _broker_args: {host_obj._broker_args}")
            if isinstance(host_obj._broker_args, dict):
                instance_id = host_obj._broker_args.get("instance_id")

        # If still no instance_id, try to find it by hostname/name in OpenStack
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
        # OpenStack doesn't have a built-in lease extension concept
        # This would depend on the specific OpenStack deployment
        logger.info(
            f"Extend operation not implemented for OpenStack server: {host_obj.instance_id}"
        )
        return host_obj

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
