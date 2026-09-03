"""Libvirt provider implementation."""

import getpass
import inspect
import logging
from pathlib import Path
import socket
from uuid import uuid4
from xml.etree import ElementTree as ET

import click
from dynaconf import Validator
from rich.console import Console
from rich.progress import track
from rich.prompt import Prompt

logger = logging.getLogger(__name__)

from broker import exceptions, helpers
from broker.binds.libvirt import LibvirtBind
from broker.providers import Provider


def _host_release():
    caller_host = inspect.stack()[1][0].f_locals["host"]
    caller_host._prov_inst.release(caller_host)
    caller_host._checked_in = True


class Libvirt(Provider):
    """Libvirt provider class providing a Broker interface around the libvirt bind."""

    _MAX_VOLUMES_DISPLAY = 20  # Maximum number of volumes to display in pool info

    _validators = [
        Validator("LIBVIRT.uri", default="qemu:///system"),
        Validator("LIBVIRT.storage_pool", default="default"),
        Validator("LIBVIRT.network", default="default"),
        Validator("LIBVIRT.default_memory", is_type_of=int, default=2048),
        Validator("LIBVIRT.default_cpus", is_type_of=int, default=2),
        Validator("LIBVIRT.default_disk_size", is_type_of=int, default=10),
        Validator("LIBVIRT.virt_type", is_in=["kvm", "qemu"], default="kvm"),
        Validator("LIBVIRT.firmware", is_in=["bios", "efi"], default="bios"),
        Validator(
            "LIBVIRT.dangling_behavior", is_in=["prompt", "checkin", "store"], default="checkin"
        ),
        Validator("LIBVIRT.default_username", default=None),
        Validator("LIBVIRT.default_password", default=None),
        Validator("LIBVIRT.default_key_filename", default=None),
        Validator("LIBVIRT.auth_override", default=None),
        Validator("LIBVIRT.name_prefix", default=None),
        Validator("LIBVIRT.sync_unmanaged", is_type_of=bool, default=False),
    ]
    _checkout_options = [
        click.option(
            "--libvirt-image", type=str, help="Name of a base qcow2 volume in the storage pool"
        ),
        click.option(
            "--libvirt-xml",
            type=click.Path(exists=True),
            help="Path to a domain XML file to define and instantiate",
        ),
    ]
    _execute_options = [
        click.option(
            "--libvirt-action",
            type=str,
            help="Hypervisor-level lifecycle command (power-off, reboot, create-snapshot, restore-snapshot, delete-snapshot, list-snapshots)",
        ),
    ]
    _extend_options = []
    _sensitive_attrs = ["default_password"]

    # Exposed as instance attributes (rather than inline literals) so tests can shrink them.
    _ip_poll_timeout = 120
    _ssh_poll_timeout = 60

    def __init__(self, **kwargs):
        # Pop bind before calling super().__init__ so it doesn't get passed up
        bind = kwargs.pop("bind", None)
        super().__init__(**kwargs)
        if bind is not None:
            self.bind = bind
        else:
            self.bind = LibvirtBind(
                uri=self._settings.LIBVIRT.uri,
                storage_pool=self._settings.LIBVIRT.storage_pool,
                network=self._settings.LIBVIRT.network,
            )
        self._name_prefix = self._settings.LIBVIRT.name_prefix or getpass.getuser()

    @staticmethod
    def _build_metadata_from_kwargs(**kwargs):
        """Build metadata dict from kwargs, filtering to serializable values.

        Stores all user-provided arguments (like --note) so they can be recovered
        during inventory sync.
        """
        metadata = {"owner": getpass.getuser()}

        # Store all kwargs that are simple types (str, int, bool, None)
        # This includes user args like --note, --cpus, --ram, --libvirt-image, etc.
        for key, value in kwargs.items():
            # Skip internal/private keys and None values
            if key.startswith("_") or value is None:
                continue
            # Only store simple types that can be serialized to strings
            if isinstance(value, (str, int, bool)):
                metadata[key] = str(value)

        return metadata

    def _set_attributes(self, host_inst, broker_args=None):
        host_inst.__dict__.update(
            {
                "_prov_inst": self,
                "_broker_provider": "Libvirt",
                "_broker_provider_instance": self.instance,
                "_broker_args": broker_args,
                "release": _host_release,
            }
        )

    def _resolve_creds(self):
        """Return only the SSH credential overrides explicitly configured for Libvirt."""
        creds = {}
        if username := self._settings.LIBVIRT.default_username:
            creds["username"] = username
        if password := self._settings.LIBVIRT.default_password:
            creds["password"] = password
        if key_filename := self._settings.LIBVIRT.default_key_filename:
            creds["key_filename"] = key_filename
        return creds

    def _parse_auth_spec(self, auth_spec):  # noqa: PLR0911
        """Parse --auth argument or config auth_override into (method, user, credential).

        :param auth_spec: String from --auth flag or LIBVIRT.auth_override config
        :return: tuple of ("password", username, password) or ("key", username, key_path)
                 or None if auth_spec is empty/None
        """
        if not auth_spec:
            return None

        # Config-style: "broker_basic" or "broker_key"
        if auth_spec == "broker_basic":
            return (
                "password",
                self._settings.SSH.HOST_USERNAME,
                self._settings.SSH.HOST_PASSWORD,
            )
        if auth_spec == "broker_key":
            return (
                "key",
                self._settings.SSH.HOST_USERNAME,
                self._settings.SSH.HOST_SSH_KEY_FILENAME,
            )

        # CLI-style: "basic" or "key" (use Libvirt defaults, fallback to SSH defaults)
        if auth_spec == "basic":
            return (
                "password",
                self._settings.LIBVIRT.default_username or self._settings.SSH.HOST_USERNAME,
                self._settings.LIBVIRT.default_password or self._settings.SSH.HOST_PASSWORD,
            )
        if auth_spec == "key":
            return (
                "key",
                self._settings.LIBVIRT.default_username or self._settings.SSH.HOST_USERNAME,
                self._settings.LIBVIRT.default_key_filename
                or self._settings.SSH.HOST_SSH_KEY_FILENAME,
            )

        # CLI-style: "user:password"
        if ":" in auth_spec:
            user, password = auth_spec.split(":", 1)
            return ("password", user, password)

        # CLI-style: "/path/to/key" or "~/path/to/key"
        if auth_spec.startswith("/") or auth_spec.startswith("~"):
            return (
                "key",
                self._settings.LIBVIRT.default_username or self._settings.SSH.HOST_USERNAME,
                auth_spec,
            )

        raise exceptions.UserError(
            f"Invalid --auth format: {auth_spec}. "
            "Use 'basic', 'key', 'user:password', or '/path/to/keyfile'"
        )

    def _generate_cloud_init_configs(self, auth_config):
        """Generate cloud-init meta-data and user-data for SSH configuration.

        :param auth_config: tuple of (method, username, credential) from _parse_auth_spec()
        :return: tuple of (meta_data_str, user_data_str)
        """
        from uuid import uuid4

        # meta-data: minimal instance identity
        instance_id = f"broker-{uuid4().hex[:8]}"
        meta_data = f"""instance-id: {instance_id}
local-hostname: {instance_id}
"""

        if not auth_config:
            return None, None

        method, username, credential = auth_config

        if method == "password":
            # Password-based authentication
            user_data = f"""#cloud-config
disable_root: false
ssh_pwauth: true

chpasswd:
  list: |
    {username}:{credential}
  expire: false

write_files:
  - path: /etc/ssh/sshd_config.d/00-broker.conf
    permissions: '0644'
    content: |
      PermitRootLogin yes
      PasswordAuthentication yes

runcmd:
  - usermod -U {username} || true
  - restorecon -F -R /etc/ssh/sshd_config /etc/ssh/sshd_config.d/ || true
  - systemctl restart sshd
"""

        elif method == "key":
            # SSH key-based authentication
            # Read the public key file
            pubkey_path = Path(credential).expanduser()
            if not pubkey_path.exists():
                raise exceptions.UserError(f"SSH public key not found: {credential}")

            pubkey_content = pubkey_path.read_text().strip()

            user_data = f"""#cloud-config
disable_root: false

users:
  - name: {username}
    ssh_authorized_keys:
      - {pubkey_content}

write_files:
  - path: /etc/ssh/sshd_config.d/00-broker.conf
    permissions: '0644'
    content: |
      PermitRootLogin yes

runcmd:
  - restorecon -F -R /etc/ssh/sshd_config /etc/ssh/sshd_config.d/ /{username}/.ssh/ || true
  - systemctl restart sshd
"""

        return meta_data, user_data

    def construct_host(self, provider_params, host_classes, **kwargs):
        """Construct a broker host from a checkout result or from stored kwargs.

        :param provider_params: a dict of {"hostname", "name", ...} from checkout, or None
            when reconstructing a host from inventory (e.g. for checkin).

        :param host_classes: mapping of host type name to Host class.

        :return: broker object of constructed host instance
        """
        if not provider_params:
            host_inst = host_classes[kwargs.get("type", "host")](**kwargs)
            self._set_attributes(host_inst, broker_args=kwargs)
            return host_inst
        host_inst = host_classes[kwargs.get("type", "host")](
            hostname=provider_params["hostname"],
            name=provider_params["name"],
            broker_settings=self._settings,
            **self._resolve_creds(),
        )
        self._set_attributes(host_inst, broker_args=kwargs)
        return host_inst

    @staticmethod
    def _inject_identity(xml_str, name, uuid_str):
        """Overwrite the name/uuid of a custom domain XML definition."""
        root = ET.fromstring(xml_str)
        name_elem = root.find("name")
        if name_elem is None:
            name_elem = ET.SubElement(root, "name")
        name_elem.text = name
        uuid_elem = root.find("uuid")
        if uuid_elem is None:
            uuid_elem = ET.SubElement(root, "uuid")
        uuid_elem.text = uuid_str
        return ET.tostring(root, encoding="unicode")

    @staticmethod
    def _inject_metadata(xml_str, metadata):
        """Inject broker metadata into existing domain XML.

        :param xml_str: Domain XML string
        :param metadata: Dict of key-value pairs to store as metadata
        :return: Updated XML string with metadata injected
        """
        root = ET.fromstring(xml_str)

        # Find or create metadata section
        metadata_elem = root.find("metadata")
        if metadata_elem is None:
            metadata_elem = ET.SubElement(root, "metadata")

        # Create broker:origin element with namespace
        broker_meta = ET.SubElement(
            metadata_elem,
            "broker:origin",
            attrib={"xmlns:broker": "https://github.com/SatelliteQE/broker"},
        )

        # Add each metadata key as a child element
        for key, value in metadata.items():
            ET.SubElement(broker_meta, f"broker:{key}").text = str(value)

        return ET.tostring(root, encoding="unicode")

    def _wait_for_ssh(self, ip):
        port = self._settings.SSH.HOST_SSH_PORT

        def _check_port():
            socket.create_connection((ip, port), timeout=5).close()

        helpers.simple_retry(_check_port, max_timeout=self._ssh_poll_timeout)

    def _get_ip(self, domain):
        def _lookup():
            ip = self.bind.get_domain_ip(
                domain,
                prefer_ipv6=self._settings.SSH.HOST_IPV6,
                ipv4_fallback=self._settings.SSH.HOST_IPV4_FALLBACK,
            )
            if not ip:
                raise Exception(f"IP not yet available for domain {domain.name()}")
            return ip

        return helpers.simple_retry(_lookup, max_timeout=self._ip_poll_timeout)

    @Provider.register_action("libvirt_image", "libvirt_xml")
    def checkout(self, **kwargs):
        """Create and boot a libvirt domain, either from a base image overlay or custom XML."""
        domain = None
        overlay_vol = None
        cloud_init_vol = None
        name = f"{getpass.getuser()}_{str(uuid4()).split('-')[0]}"

        # Determine auth override (CLI --auth flag takes precedence over config)
        auth_spec = kwargs.get("auth") or self._settings.LIBVIRT.auth_override
        auth_config = self._parse_auth_spec(auth_spec) if auth_spec else None

        try:
            if self.bind.lookup_domain(name):
                raise exceptions.ProviderError("Libvirt", f"Domain name collision: {name}")

            # Generate cloud-init configs if auth is specified
            meta_data, user_data = None, None
            if auth_config:
                meta_data, user_data = self._generate_cloud_init_configs(auth_config)

            if kwargs.get("libvirt_xml"):
                xml_str = self._inject_identity(
                    Path(kwargs["libvirt_xml"]).read_text(), name, str(uuid4())
                )
                # Also inject metadata so custom XML VMs are trackable via inventory sync
                metadata = self._build_metadata_from_kwargs(**kwargs)
                xml_str = self._inject_metadata(xml_str, metadata)
            else:
                image = kwargs.get("libvirt_image")
                if not image or not self.bind.base_volume_exists(image):
                    raise exceptions.UserError(f"Base volume '{image}' not found in storage pool")
                overlay_vol = self.bind.create_overlay_volume(
                    image,
                    f"{name}.qcow2",
                    kwargs.get("libvirt_disk_size") or self._settings.LIBVIRT.default_disk_size,
                )

                # Build metadata from all user-provided args for recovery during sync
                metadata = self._build_metadata_from_kwargs(**kwargs)

                xml_str = self.bind.build_domain_xml(
                    name=name,
                    uuid_str=str(uuid4()),
                    memory_mb=kwargs.get("ram") or self._settings.LIBVIRT.default_memory,
                    vcpus=kwargs.get("cpus") or self._settings.LIBVIRT.default_cpus,
                    disk_name=f"{name}.qcow2",
                    virt_type=self._settings.LIBVIRT.virt_type,
                    firmware=self._settings.LIBVIRT.firmware,
                    network_name=self._settings.LIBVIRT.network,
                    extra_metadata=metadata,
                )

            # Create and attach cloud-init ISO if auth config provided
            if meta_data and user_data:
                iso_name = f"{name}_cidata.iso"
                cloud_init_vol = self.bind.create_cloud_init_iso(iso_name, meta_data, user_data)
                xml_str = self.bind.attach_cloud_init_iso(xml_str, iso_name)
                logger.info("Attached cloud-init ISO for SSH configuration")

            domain = self.bind.define_and_start(xml_str)
            ip = self._get_ip(domain)
            self._wait_for_ssh(ip)

            return {"hostname": ip, "ip": ip, "name": domain.name(), "_broker_args": kwargs}
        except Exception as err:
            if domain is not None:
                self._handle_dangling(domain, err)
            elif cloud_init_vol is not None:
                self.bind.delete_volume(cloud_init_vol)
            elif overlay_vol is not None:
                self.bind.delete_volume(overlay_vol)
            raise

    def _prompt_for_dangling_host_action(self, reason=None):
        """Prompt user for action to take with a dangling domain."""
        if reason:
            logger.warning(f"Failure reason: {reason}")
        while True:
            try:
                return Prompt.ask(
                    "What would you like to do with this host? [c/s/cA/sA]\n"
                    "Checkin (c), Store (s), Checkin All (cA), Store All (sA)",
                    choices=["c", "s", "cA", "sA"],
                )
            except exceptions.InterruptResumeError:  # noqa: PERF203
                logger.debug("Prompt interrupted, retrying...")

    def _handle_dangling(self, domain, err):
        """Clean up (or store) a domain that failed checkout partway through, per settings."""
        behavior = self._settings.LIBVIRT.dangling_behavior
        if behavior == "prompt":
            choice = self._prompt_for_dangling_host_action(reason=str(err))
            behavior = "checkin" if choice in ("c", "cA") else "store"
        if behavior == "checkin":
            self._teardown_domain(domain)
        elif behavior == "store":
            try:
                ip = self.bind.get_domain_ip(domain)
            except Exception:  # noqa: BLE001
                ip = None
            helpers.update_inventory(
                add=[
                    {
                        "name": domain.name(),
                        "hostname": ip,
                        "_broker_provider": "Libvirt",
                        "deploy_failed": True,
                    }
                ]
            )

    def _delete_volume_quietly(self, vol, domain_name):
        try:
            self.bind.delete_volume(vol)
        except Exception:  # noqa: BLE001
            logger.warning(f"Could not delete volume for domain {domain_name}")

    def _teardown_domain(self, domain):
        """Destroy, undefine, and delete the storage volumes for a domain."""
        try:
            volumes = self.bind.get_domain_disk_volumes(domain)
        except Exception:  # noqa: BLE001
            volumes = []
        self.bind.destroy_domain(domain)
        self.bind.undefine_domain(domain)
        for vol in volumes:
            self._delete_volume_quietly(vol, domain.name())

    def release(self, host_obj):
        """Destroy and undefine a domain, and delete its overlay storage volume(s)."""
        name = getattr(host_obj, "name", None) or (host_obj._broker_args or {}).get("name")
        domain = self.bind.lookup_domain(name)
        if domain is None:
            logger.info(f"Domain {name} already gone; treating checkin as success")
            return
        self._teardown_domain(domain)
        logger.info(f"Successfully released domain {name}")

    @Provider.register_action("libvirt_action")
    def execute(self, libvirt_action, **kwargs):
        """Perform a hypervisor-level lifecycle action against an existing domain.

        Supports domain lifecycle actions (power-off, reboot, etc.) and snapshot operations
        (create-snapshot, restore-snapshot, delete-snapshot, list-snapshots).

        Args (passed via broker_args):
            libvirt_action: The action to perform
            host: Name of the domain to target (required)
            snapshot: Snapshot name for create/restore/delete operations
            snapshot_description: Optional description for create-snapshot

        Examples:
            broker execute --libvirt-action reboot --host my-vm
            broker execute --libvirt-action create-snapshot --host my-vm
            broker execute --libvirt-action create-snapshot --host my-vm --snapshot before-upgrade --snapshot-description "Clean state"
            broker execute --libvirt-action restore-snapshot --host my-vm --snapshot before-upgrade
            broker execute --libvirt-action list-snapshots --host my-vm
            broker execute --libvirt-action delete-snapshot --host my-vm --snapshot before-upgrade
        """
        # Extract from kwargs
        host = kwargs.get("host")
        snapshot = kwargs.get("snapshot")
        snapshot_description = kwargs.get("snapshot_description")

        # Validate host parameter
        if not host:
            raise exceptions.UserError(
                "--host is required to identify which domain --libvirt-action targets"
            )

        # Lookup domain
        domain = self.bind.lookup_domain(host)
        if domain is None:
            raise exceptions.ProviderError(
                "Libvirt",
                f"Domain '{host}' not found. Use 'broker providers Libvirt --domains' to list available domains.",
            )

        # Prepare kwargs for actions that need them
        action_kwargs = {}
        if snapshot:
            action_kwargs["snapshot"] = snapshot
        if snapshot_description:
            action_kwargs["snapshot_description"] = snapshot_description

        # Execute action
        result = self.bind.domain_action(domain, libvirt_action, **action_kwargs)

        # Log results for snapshot operations
        if libvirt_action == "create-snapshot":
            logger.info(f"Created snapshot '{result['name']}' for domain {host}")
        elif libvirt_action == "restore-snapshot":
            logger.info(f"Restored domain {host} to snapshot '{result['name']}'")
        elif libvirt_action == "delete-snapshot":
            logger.info(f"Deleted snapshot '{result['name']}' from domain {host}")
        elif libvirt_action == "list-snapshots":
            if result:
                logger.info(f"Found {len(result)} snapshot(s) for domain {host}")
            else:
                logger.info(f"No snapshots found for domain {host}")

        return result

    @Provider.register_action("libvirt_image_name")
    def detect_image_capabilities(self, libvirt_image_name, **kwargs):
        """Detect packages/capabilities in a qcow2 image using virt-inspector.

        :param libvirt_image_name: Name of image in storage pool to inspect
        :return: dict with has_cloud_init, has_sshd, has_guest_agent, image_type
        """
        import subprocess

        try:
            vol = self.bind.storage_pool.storageVolLookupByName(libvirt_image_name)
            image_path = vol.path()
        except Exception as err:
            raise exceptions.UserError(
                f"Image '{libvirt_image_name}' not found in storage pool"
            ) from err

        try:
            result = subprocess.run(
                ["virt-inspector", "-a", image_path],
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )

            root = ET.fromstring(result.stdout)
            packages = {app.findtext("name") for app in root.findall(".//application")}

            has_cloud_init = "cloud-init" in packages
            has_sshd = any(p in packages for p in ["openssh-server", "openssh"])
            has_guest_agent = "qemu-guest-agent" in packages

            # Determine image type based on capabilities
            if has_cloud_init and has_guest_agent:
                image_type = "cloud"
            elif has_cloud_init or has_sshd:
                image_type = "server"
            else:
                image_type = "minimal"

            return {
                "has_cloud_init": has_cloud_init,
                "has_sshd": has_sshd,
                "has_guest_agent": has_guest_agent,
                "image_type": image_type,
                "image_name": libvirt_image_name,
            }
        except subprocess.CalledProcessError as err:
            raise exceptions.ProviderError(
                "Libvirt", f"virt-inspector failed: {err.stderr}"
            ) from err
        except FileNotFoundError as err:
            raise exceptions.ProviderError(
                "Libvirt",
                "virt-inspector not found. Install libguestfs-tools to detect image capabilities.",
            ) from err

    @Provider.register_action("libvirt_network")
    def validate_network(self, libvirt_network=None, **kwargs):
        """Validate network configuration and connectivity.

        :param libvirt_network: Network name to validate (optional, defaults to configured network)
        :return: dict with network status and diagnostics
        """
        network_name = libvirt_network or self._settings.LIBVIRT.network
        return self.bind.validate_network(network_name)

    def get_inventory(self, name_prefix=None):
        """Get all broker-managed domains (identified by metadata presence).

        Optionally filter by name prefix for backwards compatibility, but the primary
        way to identify broker-managed VMs is by checking for broker metadata.

        If sync_unmanaged is enabled, also include non-broker VMs marked as read-only.
        """
        name_prefix = name_prefix or self._name_prefix
        sync_unmanaged = self._settings.LIBVIRT.sync_unmanaged
        # Get all domains - if sync_unmanaged is enabled, don't filter by prefix
        # to include non-broker VMs that may not follow the naming convention
        all_domains = self.bind.list_domains(None if sync_unmanaged else name_prefix)
        inventory = []

        for dom in track(all_domains, description="Compiling host information"):
            # Check if this domain has broker metadata (indicates broker-managed)
            broker_metadata = self.bind.get_broker_metadata(dom)
            if not broker_metadata and not sync_unmanaged:
                # Skip domains without broker metadata unless sync_unmanaged is enabled
                continue

            try:
                ip = self.bind.get_domain_ip(dom)
            except Exception:  # noqa: BLE001
                ip = None

            info = dom.info()

            if broker_metadata:
                # Broker-managed VM - extract full metadata
                # Convert string values back to appropriate types
                broker_args = {}
                for key, value in broker_metadata.items():
                    # Skip owner - it's metadata, not a broker arg
                    if key == "owner":
                        continue

                    # Convert known int fields
                    if key in ("cpus", "ram"):
                        try:
                            broker_args[key] = int(value)
                        except (ValueError, TypeError):
                            broker_args[key] = value
                    # Convert boolean strings
                    elif isinstance(value, str) and value.lower() in ("true", "false"):
                        broker_args[key] = value.lower() == "true"
                    # Keep everything else as strings
                    else:
                        broker_args[key] = value

                inventory.append(
                    {
                        "name": dom.name(),
                        "hostname": ip,  # Used for SSH connection
                        "ip": ip,
                        "_broker_provider": "Libvirt",
                        "_broker_provider_instance": self.instance,
                        "_broker_args": broker_args,
                        "state": info[0],
                        "memory_mb": info[1] // 1024,
                        "vcpus": info[3],
                    }
                )
            else:
                # Non-broker VM (read-only) - extract basic info from domain
                broker_args = {
                    "name": dom.name(),
                    "cpus": info[3],
                    "ram": info[1] // 1024,
                    "_synced_vm": True,
                }
                inventory.append(
                    {
                        "name": dom.name(),
                        "hostname": ip,  # Used for SSH connection
                        "ip": ip,
                        "_broker_provider": "Libvirt",
                        "_broker_provider_instance": self.instance,
                        "_broker_args": broker_args,
                        "_read_only": True,  # Mark as read-only
                        "state": info[0],
                        "memory_mb": info[1] // 1024,
                        "vcpus": info[3],
                    }
                )
        return inventory

    def _format_bytes(self, bytes_val, decimals=2):
        """Convert bytes to GB with specified decimal places."""
        if bytes_val is None:
            return "N/A"
        gb_val = bytes_val / (1024**3)
        return f"{gb_val:.{decimals}f}"

    def _get_state_name(self, state_code, domain=True):
        """Map libvirt state codes to human-readable names."""
        if domain:
            domain_states = {
                0: "No State",
                1: "Running",
                2: "Blocked",
                3: "Paused",
                4: "Shutdown",
                5: "Shut off",
                6: "Crashed",
                7: "PM Suspended",
            }
            return domain_states.get(state_code, f"Unknown ({state_code})")
        else:
            pool_states = {
                0: "Inactive",
                1: "Building",
                2: "Running",
                3: "Degraded",
                4: "Inaccessible",
            }
            return pool_states.get(state_code, f"Unknown ({state_code})")

    def _format_uuid(self, uuid_bytes):
        """Convert UUID bytes to standard hex string format (8-4-4-4-12)."""
        hex_str = uuid_bytes.hex()
        return f"{hex_str[:8]}-{hex_str[8:12]}-{hex_str[12:16]}-{hex_str[16:20]}-{hex_str[20:]}"

    def _search_volume_in_pools(self, volume_name):
        """Search for a volume across all storage pools.

        Returns:
            tuple: (virStorageVol, pool_name) if found, (None, None) otherwise
        """
        try:
            import libvirt
        except ImportError:
            logger.error("libvirt package not installed")
            return None, None

        for pool_name in self.bind.list_pools():
            try:
                pool = self.bind.connection.storagePoolLookupByName(pool_name)
                vol = pool.storageVolLookupByName(volume_name)
                return vol, pool_name
            except libvirt.libvirtError:  # noqa: PERF203
                continue
        return None, None

    def _parse_network_ip_config(self, network_xml):
        """Parse network XML for IP configuration details.

        Returns:
            dict: IP configuration with address, netmask, dhcp info
        """
        try:
            root = ET.fromstring(network_xml)
            ip_elem = root.find("./ip")
            if ip_elem is None:
                return None

            config = {
                "address": ip_elem.get("address", "N/A"),
                "netmask": ip_elem.get("netmask") or ip_elem.get("prefix", "N/A"),
            }

            dhcp_elem = ip_elem.find("./dhcp")
            if dhcp_elem is not None:
                dhcp_range = dhcp_elem.find("./range")
                if dhcp_range is not None:
                    config["dhcp_start"] = dhcp_range.get("start", "N/A")
                    config["dhcp_end"] = dhcp_range.get("end", "N/A")
                    config["dhcp_enabled"] = "Yes"
                else:
                    config["dhcp_enabled"] = "No"
            else:
                config["dhcp_enabled"] = "No"

            return config
        except Exception as err:  # noqa: BLE001
            logger.debug(f"Failed to parse network IP config: {err}")
            return None

    def _show_domain_info(self, domain_name, rich_console):  # noqa: PLR0912, PLR0915
        """Display detailed information about a specific domain."""
        try:
            import libvirt
        except ImportError:
            logger.error("libvirt package not installed")
            return None

        dom = self.bind.lookup_domain(domain_name)
        if not dom:
            logger.warning(f"Domain {domain_name} not found!")
            return None

        try:
            # Gather basic domain information
            state, max_mem, memory, vcpus, _cpu_time = dom.info()
            ip = self.bind.get_domain_ip(dom) or "N/A"
            uuid_str = self._format_uuid(dom.UUID())
            is_active = dom.isActive()

            # Main information table
            main_table = helpers.dict_to_table(
                {
                    "Name": domain_name,
                    "UUID": uuid_str,
                    "State": self._get_state_name(state),
                    "Active": "Yes" if is_active else "No",
                    "IP Address": ip,
                    "Memory": f"{self._format_bytes(memory * 1024)} / {self._format_bytes(max_mem * 1024)} GB",
                    "vCPUs": str(vcpus),
                },
                title=f"{domain_name} Information",
            )
            rich_console.print(main_table)

            # Broker metadata table (if present)
            metadata = self.bind.get_broker_metadata(dom)
            if metadata:
                metadata_table = helpers.dict_to_table(
                    metadata,
                    title="Broker Metadata",
                )
                rich_console.print(metadata_table)

            # Attached volumes table
            volumes = self.bind.get_domain_disk_volumes(dom)
            if volumes:
                vol_data = []
                for vol in volumes:
                    try:
                        vol_info = vol.info()
                        vol_name = vol.name()
                        # Try to get pool name from path
                        pool_name = "unknown"
                        try:
                            vol_path = vol.path()
                            for pname in self.bind.list_pools():
                                pool = self.bind.connection.storagePoolLookupByName(pname)
                                try:
                                    pool.storageVolLookupByPath(vol_path)
                                    pool_name = pname
                                    break
                                except libvirt.libvirtError:
                                    continue
                        except Exception as err:  # noqa: BLE001
                            logger.debug(f"Failed to get pool name for volume: {err}")

                        vol_data.append(
                            {
                                "Volume": vol_name,
                                "Pool": pool_name,
                                "Capacity": f"{self._format_bytes(vol_info[1])} GB",
                                "Allocation": f"{self._format_bytes(vol_info[2])} GB",
                            }
                        )
                    except Exception as err:  # noqa: BLE001
                        logger.debug(f"Failed to get volume info: {err}")
                        continue

                if vol_data:
                    vol_table = helpers.dictlist_to_table(
                        vol_data,
                        title="Attached Volumes",
                        _id=False,
                        headers=True,
                    )
                    rich_console.print(vol_table)

            # Snapshots table
            try:
                snapshots = self.bind.list_snapshots(dom)
                if snapshots:
                    snap_data = [
                        {
                            "Name": snap["name"],
                            "Created": snap.get("created", "N/A"),
                            "State": snap.get("state", "N/A"),
                            "Current": "Yes" if snap.get("current") else "No",
                            "Description": snap.get("description", "")[:50],  # Truncate
                        }
                        for snap in snapshots
                    ]
                    snap_table = helpers.dictlist_to_table(
                        snap_data,
                        title=f"Snapshots ({len(snapshots)})",
                        _id=False,
                        headers=True,
                    )
                    rich_console.print(snap_table)
            except Exception as err:  # noqa: BLE001
                logger.debug(f"Failed to list snapshots: {err}")

            # Return structured data
            return {
                "name": domain_name,
                "uuid": uuid_str,
                "state": self._get_state_name(state),
                "active": is_active,
                "ip": ip,
                "memory_gb": float(self._format_bytes(memory * 1024)),
                "max_memory_gb": float(self._format_bytes(max_mem * 1024)),
                "vcpus": vcpus,
                "metadata": metadata,
                "volumes": vol_data if volumes else [],
                "snapshots": snapshots if snapshots else [],
            }
        except Exception as err:  # noqa: BLE001
            logger.error(f"Failed to get domain information: {err}")
            return None

    def _show_image_info(self, image_name, rich_console):
        """Display detailed information about a specific image/volume."""
        vol, pool_name = self._search_volume_in_pools(image_name)
        if not vol:
            logger.warning(f"Image {image_name} not found!")
            return None

        try:
            vol_info = vol.info()
            vol_path = vol.path()

            # Volume type mapping
            vol_type_map = {
                0: "File",
                1: "Block",
                2: "Directory",
                3: "Network",
                4: "Netdir",
                5: "Ploop",
            }
            vol_type = vol_type_map.get(vol_info[0], f"Unknown ({vol_info[0]})")

            # Main information table
            main_table = helpers.dict_to_table(
                {
                    "Name": image_name,
                    "Pool": pool_name,
                    "Type": vol_type,
                    "Capacity": f"{self._format_bytes(vol_info[1])} GB",
                    "Allocation": f"{self._format_bytes(vol_info[2])} GB",
                    "Path": vol_path,
                },
                title=f"{image_name} Information",
            )
            rich_console.print(main_table)

            # Return structured data
            return {
                "name": image_name,
                "pool": pool_name,
                "type": vol_type,
                "capacity_gb": float(self._format_bytes(vol_info[1])),
                "allocation_gb": float(self._format_bytes(vol_info[2])),
                "path": vol_path,
            }
        except Exception as err:  # noqa: BLE001
            logger.error(f"Failed to get image information: {err}")
            return None

    def _show_network_info(self, network_name, rich_console):
        """Display detailed information about a specific network."""
        try:
            import libvirt

            network = self.bind.connection.networkLookupByName(network_name)
        except libvirt.libvirtError:
            logger.warning(f"Network {network_name} not found!")
            return None

        try:
            network_xml = network.XMLDesc()
            is_active = network.isActive()
            autostart = network.autostart()

            # Parse XML for additional details
            root = ET.fromstring(network_xml)
            bridge_name = root.find("./bridge")
            bridge = bridge_name.get("name") if bridge_name is not None else "N/A"

            forward_elem = root.find("./forward")
            forward_mode = forward_elem.get("mode") if forward_elem is not None else "isolated"

            # Main information table
            main_table = helpers.dict_to_table(
                {
                    "Name": network_name,
                    "Active": "Yes" if is_active else "No",
                    "Autostart": "Yes" if autostart else "No",
                    "Bridge": bridge,
                    "Forward Mode": forward_mode,
                },
                title=f"{network_name} Information",
            )
            rich_console.print(main_table)

            # IP configuration table (if present)
            ip_config = self._parse_network_ip_config(network_xml)
            if ip_config:
                ip_table = helpers.dict_to_table(
                    {
                        "Address": ip_config.get("address", "N/A"),
                        "Netmask": ip_config.get("netmask", "N/A"),
                        "DHCP Range": f"{ip_config.get('dhcp_start', 'N/A')} - {ip_config.get('dhcp_end', 'N/A')}"
                        if ip_config.get("dhcp_enabled") == "Yes"
                        else "N/A",
                        "DHCP Enabled": ip_config.get("dhcp_enabled", "No"),
                    },
                    title="IP Configuration",
                )
                rich_console.print(ip_table)

            # Return structured data
            return {
                "name": network_name,
                "active": is_active,
                "autostart": autostart,
                "bridge": bridge,
                "forward_mode": forward_mode,
                "ip_config": ip_config,
            }
        except Exception as err:  # noqa: BLE001
            logger.error(f"Failed to get network information: {err}")
            return None

    def _show_pool_info(self, pool_name, rich_console):
        """Display detailed information about a specific storage pool."""
        try:
            import libvirt

            pool = self.bind.connection.storagePoolLookupByName(pool_name)
        except libvirt.libvirtError:
            logger.warning(f"Pool {pool_name} not found!")
            return None

        try:
            pool_info = pool.info()
            is_active = pool.isActive()
            autostart = pool.autostart()

            # Parse XML for additional details
            pool_xml = pool.XMLDesc()
            root = ET.fromstring(pool_xml)

            pool_type = root.get("type", "unknown")
            target_elem = root.find("./target/path")
            target_path = target_elem.text if target_elem is not None else "N/A"

            # Main information table
            main_table = helpers.dict_to_table(
                {
                    "Name": pool_name,
                    "State": self._get_state_name(pool_info[0], domain=False),
                    "Active": "Yes" if is_active else "No",
                    "Autostart": "Yes" if autostart else "No",
                    "Type": pool_type,
                    "Capacity": f"{self._format_bytes(pool_info[1])} GB",
                    "Allocation": f"{self._format_bytes(pool_info[2])} GB",
                    "Available": f"{self._format_bytes(pool_info[3])} GB",
                    "Path": target_path,
                },
                title=f"{pool_name} Information",
            )
            rich_console.print(main_table)

            # List volumes in pool
            try:
                volumes = pool.listVolumes()
                vol_count = len(volumes)
                if vol_count > 0:
                    display_vols = volumes[: self._MAX_VOLUMES_DISPLAY]
                    vol_list = ", ".join(display_vols)
                    if vol_count > self._MAX_VOLUMES_DISPLAY:
                        vol_list += f" ... and {vol_count - self._MAX_VOLUMES_DISPLAY} more"
                    rich_console.print(f"\n[bold]Volumes ({vol_count}):[/bold] {vol_list}")
            except Exception as err:  # noqa: BLE001
                logger.debug(f"Failed to list volumes: {err}")
                volumes = []
                vol_count = 0

            # Return structured data
            return {
                "name": pool_name,
                "state": self._get_state_name(pool_info[0], domain=False),
                "active": is_active,
                "autostart": autostart,
                "type": pool_type,
                "capacity_gb": float(self._format_bytes(pool_info[1])),
                "allocation_gb": float(self._format_bytes(pool_info[2])),
                "available_gb": float(self._format_bytes(pool_info[3])),
                "path": target_path,
                "volume_count": vol_count,
                "volumes": volumes[: self._MAX_VOLUMES_DISPLAY] if volumes else [],
            }
        except Exception as err:  # noqa: BLE001
            logger.error(f"Failed to get pool information: {err}")
            return None

    def provider_help(  # noqa: PLR0911
        self,
        image=None,
        domain=None,
        network=None,
        pool=None,
        images=False,
        domains=False,
        networks=False,
        pools=False,
        **kwargs,
    ):
        """Return useful information about libvirt resources."""
        rich_console = Console(no_color=self._settings.less_colors)

        # Handle singular resource information displays
        if domain:
            return self._show_domain_info(domain, rich_console)
        elif image:
            return self._show_image_info(image, rich_console)
        elif network:
            return self._show_network_info(network, rich_console)
        elif pool:
            return self._show_pool_info(pool, rich_console)

        # Handle plural resource listings (existing logic)
        if images:
            results = self.bind.list_base_volumes()
        elif domains:
            results = [d.name() for d in self.bind.list_domains()]
        elif networks:
            results = self.bind.list_networks()
        elif pools:
            results = self.bind.list_pools()
        else:
            return None
        if res_filter := kwargs.get("results_filter"):
            results = helpers.eval_filter(results, res_filter, "res")
            results = results if isinstance(results, list) else [results]
        if not results:
            logger.warning("No results found!")
            return results
        table = helpers.dictlist_to_table(
            [{"name": r} for r in results], title="Libvirt Resources", _id=False, headers=False
        )
        rich_console.print(table)
        return results

    def extend(self):
        """Libvirt domains have no lease concept; nothing to extend."""
