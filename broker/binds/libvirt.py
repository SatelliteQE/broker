"""A collection of classes to ease interaction with the libvirt API."""

import contextlib
import logging
import platform
import threading
from xml.etree import ElementTree as ET

# Deferred import - will be imported when the first LibvirtBind instance is created
libvirt = None

logger = logging.getLogger(__name__)

LOOPBACK_PREFIXES = ("127.",)
LINK_LOCAL_PREFIXES = ("169.254.", "fe80:")

OVMF_CODE_CANDIDATES = (
    "/usr/share/OVMF/OVMF_CODE.fd",
    "/usr/share/edk2/ovmf/OVMF_CODE.fd",
    "/usr/share/edk2-ovmf/x64/OVMF_CODE.fd",
)
OVMF_VARS_CANDIDATES = (
    "/usr/share/OVMF/OVMF_VARS.fd",
    "/usr/share/edk2/ovmf/OVMF_VARS.fd",
    "/usr/share/edk2-ovmf/x64/OVMF_VARS.fd",
)


def _is_loopback_or_link_local(addr):
    """Return True if an address string is loopback or link-local."""
    return addr.startswith(LOOPBACK_PREFIXES) or addr.startswith(LINK_LOCAL_PREFIXES)


def _select_address(addresses, prefer_ipv6=False, ipv4_fallback=True):
    """Pick the best address to use for SSH connectivity out of a flat address list.

    :param addresses: list of {"addr": str, "family": "ipv4"|"ipv6"} dicts
    :param prefer_ipv6: prefer an IPv6 address when one is available
    :param ipv4_fallback: when prefer_ipv6 is set but no IPv6 address is available, allow
        falling back to an IPv4 address instead of returning None

    :return: the selected address string, or None if no usable address was found
    """
    usable = [a for a in addresses if a.get("addr") and not _is_loopback_or_link_local(a["addr"])]
    ipv4 = [a["addr"] for a in usable if a.get("family") == "ipv4"]
    ipv6 = [a["addr"] for a in usable if a.get("family") == "ipv6"]

    if prefer_ipv6:
        if ipv6:
            return ipv6[0]
        return ipv4[0] if (ipv4_fallback and ipv4) else None
    if ipv4:
        return ipv4[0]
    if ipv6:
        return ipv6[0]
    return None


def _build_volume_xml(name, size_gib, backing_path):
    """Build storage-volume XML for a qcow2 overlay backed by an existing base volume."""
    volume = ET.Element("volume")
    ET.SubElement(volume, "name").text = name
    ET.SubElement(volume, "capacity", unit="G").text = str(size_gib)
    target = ET.SubElement(volume, "target")
    ET.SubElement(target, "format", type="qcow2")
    backing_store = ET.SubElement(volume, "backingStore")
    ET.SubElement(backing_store, "path").text = backing_path
    ET.SubElement(backing_store, "format", type="qcow2")
    return ET.tostring(volume, encoding="unicode")


def _build_domain_xml(
    *,
    name,
    uuid_str,
    memory_mb,
    vcpus,
    disk_name,
    storage_pool,
    arch,
    machine,
    emulator,
    virt_type,
    firmware,
    network_name,
    ovmf_code=None,
    ovmf_vars_template=None,
    extra_metadata=None,
):
    """Build portable domain XML for a libvirt guest.

    Portability per design: virt_type/arch/machine/emulator are supplied by the caller
    (resolved from the connection's capabilities), not hardcoded, so this function has no
    connection dependency and is fully unit-testable.
    """
    domain = ET.Element("domain", type=virt_type)
    ET.SubElement(domain, "name").text = name
    ET.SubElement(domain, "uuid").text = uuid_str
    ET.SubElement(domain, "memory", unit="MiB").text = str(memory_mb)
    ET.SubElement(domain, "currentMemory", unit="MiB").text = str(memory_mb)
    ET.SubElement(domain, "vcpu").text = str(vcpus)

    os_elem = ET.SubElement(domain, "os")
    ET.SubElement(os_elem, "type", arch=arch, machine=machine).text = "hvm"
    if firmware == "efi":
        loader = ET.SubElement(os_elem, "loader", readonly="yes", type="pflash")
        loader.text = ovmf_code or OVMF_CODE_CANDIDATES[0]
        ET.SubElement(os_elem, "nvram", template=ovmf_vars_template or OVMF_VARS_CANDIDATES[0])

    features = ET.SubElement(domain, "features")
    ET.SubElement(features, "acpi")
    ET.SubElement(features, "apic")

    ET.SubElement(domain, "cpu", mode="host-passthrough", check="none")

    devices = ET.SubElement(domain, "devices")
    ET.SubElement(devices, "emulator").text = emulator

    disk = ET.SubElement(devices, "disk", type="volume", device="disk")
    ET.SubElement(disk, "driver", name="qemu", type="qcow2")
    ET.SubElement(disk, "source", pool=storage_pool, volume=disk_name)
    ET.SubElement(disk, "target", dev="vda", bus="virtio")

    interface = ET.SubElement(devices, "interface", type="network")
    ET.SubElement(interface, "source", network=network_name)
    ET.SubElement(interface, "model", type="virtio")

    channel = ET.SubElement(devices, "channel", type="unix")
    ET.SubElement(channel, "target", type="virtio", name="org.qemu.guest_agent.0")

    ET.SubElement(devices, "console", type="pty")

    if extra_metadata:
        metadata = ET.SubElement(domain, "metadata")
        broker_meta = ET.SubElement(
            metadata,
            "broker:origin",
            attrib={"xmlns:broker": "https://github.com/SatelliteQE/broker"},
        )
        for key, value in extra_metadata.items():
            ET.SubElement(broker_meta, f"broker:{key}").text = str(value)

    return ET.tostring(domain, encoding="unicode")


def _libvirt_error_handler(ctx, err):
    """Suppress libvirt stderr output unless debug logging is enabled.

    This handler suppresses the default stderr output from libvirt (which includes expected
    errors like "Guest agent is not responding" during VM boot) unless debug logging
    is enabled.
    """
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"libvirt: {err[2]}")


class LibvirtBind:
    """Wraps libvirt-python connection handling, XML synthesis, and storage-volume management."""

    _sensitive_attrs = []

    def __init__(self, uri=None, storage_pool="default", network="default"):
        global libvirt  # noqa: PLW0603
        if libvirt is None:
            try:
                import libvirt as libvirt_module

                libvirt = libvirt_module
                # Register custom error handler to suppress stderr noise unless debug logging
                libvirt.registerErrorHandler(_libvirt_error_handler, None)
            except ImportError as err:
                raise ImportError(
                    "libvirt-python is required for the Libvirt provider. "
                    "Please install it with 'pip install libvirt-python' or "
                    "'pip install broker[libvirt]'"
                ) from err
        self.uri = uri
        self.storage_pool_name = storage_pool
        self.network_name = network
        self._local = threading.local()

    @property
    def connection(self):
        """Return a connection scoped to the current thread, opening one if needed.

        virConnect objects are not thread-safe and Broker performs concurrent checkouts
        (e.g. `--count N`), so each worker thread gets and keeps its own connection.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None or not conn.isAlive():
            conn = libvirt.open(self.uri)
            self._local.conn = conn
        return conn

    @property
    def storage_pool(self):
        """Return the configured storage pool object."""
        return self.connection.storagePoolLookupByName(self.storage_pool_name)

    def _resolve_domain_target(self, virt_type):
        """Resolve (arch, machine, emulator) for the given virt_type from host capabilities.

        Capabilities XML lists one <guest> block per architecture QEMU can target (including
        emulated foreign architectures like i686/arm/alpha on an x86_64 host), often with the
        host's own architecture nowhere near the front of the list. Picking the first match
        would silently boot guests under the wrong architecture, so the host's native
        architecture is preferred and other matches are only used as a fallback.
        """
        host_arch = platform.machine()
        matches = []
        caps = ET.fromstring(self.connection.getCapabilities())
        for guest in caps.findall("guest"):
            if guest.findtext("os_type") != "hvm":
                continue
            arch_elem = guest.find("arch")
            arch = arch_elem.get("name")
            for domain_elem in arch_elem.findall("domain"):
                if domain_elem.get("type") == virt_type:
                    emulator = domain_elem.findtext("emulator") or arch_elem.findtext("emulator")
                    machine = domain_elem.findtext("machine") or arch_elem.findtext("machine")
                    matches.append((arch, machine, emulator))
        for arch, machine, emulator in matches:
            if arch == host_arch:
                return arch, machine, emulator
        if matches:
            return matches[0]
        raise RuntimeError(f"No capabilities found for virt_type={virt_type!r}")

    def _resolve_ovmf_paths(self, emulator, arch, machine, virt_type):
        """Resolve OVMF code/vars paths, preferring what the hypervisor advertises."""
        code_path, vars_path = None, None
        with contextlib.suppress(Exception):  # fall back to well-known paths below
            caps_xml = self.connection.getDomainCapabilities(emulator, arch, machine, virt_type)
            caps = ET.fromstring(caps_xml)
            for value in caps.findall("./os/loader/value"):
                if value.text and "CODE" in value.text.upper():
                    code_path = value.text
                elif value.text and "VARS" in value.text.upper():
                    vars_path = value.text
        return (
            code_path or OVMF_CODE_CANDIDATES[0],
            vars_path or OVMF_VARS_CANDIDATES[0],
        )

    def build_domain_xml(
        self,
        *,
        name,
        uuid_str,
        memory_mb,
        vcpus,
        disk_name,
        virt_type,
        firmware,
        network_name,
        extra_metadata=None,
    ):
        """Resolve portability details from the connection and synthesize domain XML."""
        arch, machine, emulator = self._resolve_domain_target(virt_type)
        ovmf_code = ovmf_vars = None
        if firmware == "efi":
            ovmf_code, ovmf_vars = self._resolve_ovmf_paths(emulator, arch, machine, virt_type)
        return _build_domain_xml(
            name=name,
            uuid_str=uuid_str,
            memory_mb=memory_mb,
            vcpus=vcpus,
            disk_name=disk_name,
            storage_pool=self.storage_pool_name,
            arch=arch,
            machine=machine,
            emulator=emulator,
            virt_type=virt_type,
            firmware=firmware,
            network_name=network_name,
            ovmf_code=ovmf_code,
            ovmf_vars_template=ovmf_vars,
            extra_metadata=extra_metadata,
        )

    def base_volume_exists(self, name):
        """Return True if a volume with the given name exists in the storage pool."""
        # Refresh pool to ensure we see newly added volumes (especially for remote pools)
        pool = self.storage_pool
        logger.debug(f"Checking for volume '{name}' in pool '{pool.name()}' on URI '{self.uri}'")
        pool.refresh()
        volumes = pool.listVolumes()
        logger.debug(f"Available volumes in pool '{pool.name()}': {volumes}")
        exists = name in volumes
        logger.debug(f"Volume '{name}' exists: {exists}")
        return exists

    def list_base_volumes(self):
        """Return the names of all volumes in the storage pool."""
        return self.storage_pool.listVolumes()

    def list_networks(self):
        """Return the names of all defined networks."""
        return [net.name() for net in self.connection.listAllNetworks()]

    def validate_network(self, network_name=None):
        """Validate that a network is active and properly configured.

        :param network_name: Network to validate (defaults to configured network)
        :return: dict with status and diagnostics
        """
        if network_name is None:
            network_name = self.storage_pool_name  # Use configured network

        try:
            network = self.connection.networkLookupByName(network_name)
        except libvirt.libvirtError:
            return {
                "active": False,
                "error": f"Network '{network_name}' not found",
                "fix": "Run setup scenario to create default network",
            }

        is_active = network.isActive()
        is_autostart = network.autostart()

        # Parse XML to check configuration
        xml_desc = network.XMLDesc()
        root = ET.fromstring(xml_desc)

        has_forward = root.find("forward") is not None
        has_dhcp = root.find(".//dhcp") is not None
        bridge_elem = root.find("bridge")
        bridge_name = bridge_elem.get("name") if bridge_elem is not None else None

        return {
            "active": is_active,
            "autostart": is_autostart,
            "has_nat": has_forward,
            "has_dhcp": has_dhcp,
            "bridge": bridge_name,
        }

    def list_pools(self):
        """Return the names of all defined storage pools."""
        return [pool.name() for pool in self.connection.listAllStoragePools()]

    def create_overlay_volume(self, base_volume_name, overlay_name, size_gib):
        """Create a copy-on-write qcow2 overlay volume backed by an existing base volume."""
        pool = self.storage_pool
        base_vol = pool.storageVolLookupByName(base_volume_name)
        vol_xml = _build_volume_xml(overlay_name, size_gib, base_vol.path())
        return pool.createXML(vol_xml, 0)

    def define_and_start(self, domain_xml):
        """Define a domain from XML and start it."""
        domain = self.connection.defineXML(domain_xml)
        domain.create()
        return domain

    def lookup_domain(self, name):
        """Look up a domain by name, returning None only if it genuinely doesn't exist.

        Any other libvirt error (permission/policykit failures, a dropped connection, etc.)
        is re-raised rather than treated as "not found" - conflating the two would make
        release() silently report a checkin as successful without actually tearing anything
        down.
        """
        try:
            return self.connection.lookupByName(name)
        except libvirt.libvirtError as err:
            if err.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return None
            raise

    def destroy_domain(self, domain):
        """Power off a domain if it is currently active."""
        if domain.isActive():
            domain.destroy()

    def undefine_domain(self, domain):
        """Undefine a domain, removing its NVRAM and snapshot metadata."""
        try:
            domain.undefineFlags(
                libvirt.VIR_DOMAIN_UNDEFINE_NVRAM | libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
            )
        except libvirt.libvirtError as err:
            if err.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN:
                raise

    def get_domain_disk_volumes(self, domain):
        """Return the virStorageVol objects backing a domain's disks."""
        desc = ET.fromstring(domain.XMLDesc())
        volumes = []
        for disk in desc.findall("./devices/disk"):
            source = disk.find("source")
            if source is None:
                continue
            try:
                if source.get("volume") and source.get("pool"):
                    pool = self.connection.storagePoolLookupByName(source.get("pool"))
                    volumes.append(pool.storageVolLookupByName(source.get("volume")))
                elif source.get("file"):
                    volumes.append(self.connection.storageVolLookupByPath(source.get("file")))
            except libvirt.libvirtError:
                continue
        return volumes

    def delete_volume(self, vol):
        """Delete a storage volume, treating an already-missing volume as success."""
        try:
            vol.delete(0)
        except libvirt.libvirtError as err:
            if err.get_error_code() != libvirt.VIR_ERR_NO_STORAGE_VOL:
                raise

    def get_domain_ip(self, domain, prefer_ipv6=False, ipv4_fallback=True):
        """Resolve a domain's IP via the guest agent, falling back to DHCP leases."""
        addresses = None
        try:
            addresses = domain.interfaceAddresses(libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT)
        except libvirt.libvirtError:
            addresses = None
        if not addresses:
            try:
                addresses = domain.interfaceAddresses(
                    libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE
                )
            except libvirt.libvirtError:
                addresses = None
        if not addresses:
            return None

        flat = []
        for iface in addresses.values():
            for addr in iface.get("addrs") or []:
                family = "ipv6" if addr.get("type") == libvirt.VIR_IP_ADDR_TYPE_IPV6 else "ipv4"
                flat.append({"addr": addr.get("addr"), "family": family})
        return _select_address(flat, prefer_ipv6=prefer_ipv6, ipv4_fallback=ipv4_fallback)

    def get_broker_metadata(self, domain):
        """Extract broker-specific metadata from a domain's XML.

        :param domain: virDomain object
        :return: dict of metadata key-value pairs, empty dict if no metadata found
        """
        try:
            xml = domain.XMLDesc()
            root = ET.fromstring(xml)

            # Find the broker:origin element in the metadata section
            broker_ns = "{https://github.com/SatelliteQE/broker}"
            origin = root.find(f"./metadata/{broker_ns}origin")

            if origin is None:
                return {}

            metadata = {}
            for child in origin:
                # Strip namespace prefix from tag (e.g., "{...}cpus" -> "cpus")
                key = child.tag.split("}")[-1]
                if child.text:
                    metadata[key] = child.text

            return metadata
        except Exception:  # noqa: BLE001
            # If XML parsing fails or metadata is malformed, return empty dict
            return {}

    def create_cloud_init_iso(self, iso_name, meta_data, user_data):
        """Create a cloud-init NoCloud ISO and upload it to the storage pool.

        :param iso_name: Name for the ISO volume (e.g., "user_uuid_cidata.iso")
        :param meta_data: String content for meta-data file
        :param user_data: String content for user-data file
        :return: virStorageVol object for the created ISO
        """
        import io

        import pycdlib

        # Create ISO filesystem in memory
        iso = pycdlib.PyCdlib()
        iso.new(
            interchange_level=3,
            joliet=3,
            rock_ridge="1.09",
            vol_ident="cidata",  # CRITICAL: cloud-init looks for this label
        )

        # Add meta-data file
        # ISO9660 primary path must use underscores; joliet/rock ridge can use hyphens
        meta_data_bytes = meta_data.encode("utf-8")
        iso.add_fp(
            io.BytesIO(meta_data_bytes),
            len(meta_data_bytes),
            "/META_DATA.;1",
            joliet_path="/meta-data",
            rr_name="meta-data",
        )

        # Add user-data file
        user_data_bytes = user_data.encode("utf-8")
        iso.add_fp(
            io.BytesIO(user_data_bytes),
            len(user_data_bytes),
            "/USER_DATA.;1",
            joliet_path="/user-data",
            rr_name="user-data",
        )

        # Write ISO to memory buffer
        iso_buffer = io.BytesIO()
        iso.write_fp(iso_buffer)
        iso.close()
        iso_data = iso_buffer.getvalue()
        iso_size = len(iso_data)

        # Create storage volume in libvirt pool
        pool = self.storage_pool
        vol_xml = f"""
        <volume>
            <name>{iso_name}</name>
            <capacity unit="bytes">{iso_size}</capacity>
            <target>
                <format type="raw"/>
            </target>
        </volume>
        """

        vol = pool.createXML(vol_xml, 0)

        # Upload ISO data to the volume
        stream = self.connection.newStream(0)
        vol.upload(stream, 0, iso_size, 0)

        # Write data in chunks
        chunk_size = 1024 * 1024  # 1MB chunks
        offset = 0
        while offset < iso_size:
            chunk = iso_data[offset : offset + chunk_size]
            stream.send(chunk)
            offset += len(chunk)

        stream.finish()

        return vol

    def attach_cloud_init_iso(self, domain_xml, iso_volume_name):
        """Add cloud-init ISO as a CD-ROM device to domain XML.

        :param domain_xml: Domain XML string
        :param iso_volume_name: Name of the ISO volume in the storage pool
        :return: Modified domain XML string with CD-ROM attached
        """
        root = ET.fromstring(domain_xml)
        devices = root.find("devices")

        # Create CD-ROM disk element
        disk = ET.SubElement(devices, "disk", type="volume", device="cdrom")
        ET.SubElement(disk, "driver", name="qemu", type="raw")

        source = ET.SubElement(disk, "source")
        source.set("pool", self.storage_pool_name)
        source.set("volume", iso_volume_name)

        # Use sata bus for broad compatibility
        ET.SubElement(disk, "target", dev="sda", bus="sata")
        ET.SubElement(disk, "readonly")

        return ET.tostring(root, encoding="unicode")

    _ACTIONS = {
        "power-off": lambda d: d.shutdown(),
        "power-on": lambda d: d.create(),
        "hard-stop": lambda d: d.destroy(),
        "reboot": lambda d: d.reboot(0),
        "reset": lambda d: d.reset(),
        "pause": lambda d: d.suspend(),
        "resume": lambda d: d.resume(),
    }

    def domain_action(self, domain, action, **kwargs):
        """Perform a hypervisor-level lifecycle action on a domain.

        Args:
            domain: virDomain object
            action: Action name (power-off, reboot, create-snapshot, etc.)
            **kwargs: Additional arguments for specific actions

        Returns:
            Action result (may be None or a dict)
        """
        # Handle snapshot actions that need kwargs
        if action == "create-snapshot":
            return self.create_snapshot(
                domain,
                name=kwargs.get("snapshot"),
                description=kwargs.get("snapshot_description"),
            )
        elif action == "restore-snapshot":
            snapshot = kwargs.get("snapshot")
            if not snapshot:
                raise ValueError("--snapshot required for restore-snapshot")
            return self.restore_snapshot(domain, snapshot)
        elif action == "delete-snapshot":
            snapshot = kwargs.get("snapshot")
            if not snapshot:
                raise ValueError("--snapshot required for delete-snapshot")
            return self.delete_snapshot(domain, snapshot)
        elif action == "list-snapshots":
            return self.list_snapshots(domain)
        # Handle standard domain lifecycle actions
        elif action in self._ACTIONS:
            return self._ACTIONS[action](domain)
        else:
            valid_actions = [
                *self._ACTIONS.keys(),
                "create-snapshot",
                "restore-snapshot",
                "delete-snapshot",
                "list-snapshots",
            ]
            raise ValueError(
                f"Unknown libvirt action: {action}. Valid actions: {', '.join(valid_actions)}"
            )

    def create_snapshot(self, domain, name=None, description=None):
        """Create an internal snapshot of a domain.

        Args:
            domain: virDomain object
            name: Snapshot name (auto-generated if None)
            description: Optional description

        Returns:
            dict: {name, domain, created, state, description, type}
        """
        from datetime import datetime
        from xml.etree import ElementTree as ET

        # Auto-generate name if not provided
        if not name:
            name = f"broker-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        # Check for name conflict
        try:
            domain.snapshotLookupByName(name)
            raise ValueError(
                f"Snapshot '{name}' already exists for domain {domain.name()}. "
                "Use a different --snapshot or omit it for auto-generated name."
            )
        except libvirt.libvirtError as err:
            if err.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN_SNAPSHOT:
                raise

        # Build minimal snapshot XML
        snapshot_xml = ET.Element("domainsnapshot")
        ET.SubElement(snapshot_xml, "name").text = name
        if description:
            ET.SubElement(snapshot_xml, "description").text = description

        # Include memory state if domain is running, otherwise disk-only
        if domain.isActive():
            ET.SubElement(snapshot_xml, "memory", snapshot="internal")
        else:
            ET.SubElement(snapshot_xml, "memory", snapshot="no")

        xml_str = ET.tostring(snapshot_xml, encoding="unicode")

        # Create snapshot
        snapshot = domain.snapshotCreateXML(xml_str, 0)

        # Parse result
        return self._parse_snapshot_info(snapshot, domain.name())

    def list_snapshots(self, domain):
        """List all snapshots for a domain.

        Args:
            domain: virDomain object

        Returns:
            list: [{name, created, state, description, current}, ...]
        """
        try:
            snapshots = domain.listAllSnapshots(0)
            return [self._parse_snapshot_info(snap, domain.name()) for snap in snapshots]
        except libvirt.libvirtError:
            return []

    def restore_snapshot(self, domain, snapshot_name):
        """Restore a domain to a snapshot state.

        Args:
            domain: virDomain object
            snapshot_name: Name of snapshot to restore

        Returns:
            dict: {name, domain, restored_at, previous_state, new_state}
        """
        from datetime import datetime

        # Lookup snapshot
        try:
            snapshot = domain.snapshotLookupByName(snapshot_name)
        except libvirt.libvirtError as err:
            if err.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN_SNAPSHOT:
                available = [s.getName() for s in domain.listAllSnapshots(0)]
                raise ValueError(
                    f"Snapshot '{snapshot_name}' not found for domain {domain.name()}. "
                    f"Available: {', '.join(available) or 'none'}"
                )
            raise

        # Get states
        previous_state = "running" if domain.isActive() else "shutoff"
        snap_info = self._parse_snapshot_info(snapshot, domain.name())

        # Revert
        domain.revertToSnapshot(snapshot, 0)

        return {
            "name": snapshot_name,
            "domain": domain.name(),
            "restored_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "previous_state": previous_state,
            "new_state": snap_info["state"],
        }

    def delete_snapshot(self, domain, snapshot_name):
        """Delete a snapshot.

        Args:
            domain: virDomain object
            snapshot_name: Name of snapshot to delete

        Returns:
            dict: {name, domain, deleted_at}
        """
        from datetime import datetime

        # Lookup snapshot
        try:
            snapshot = domain.snapshotLookupByName(snapshot_name)
        except libvirt.libvirtError as err:
            if err.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN_SNAPSHOT:
                available = [s.getName() for s in domain.listAllSnapshots(0)]
                raise ValueError(
                    f"Snapshot '{snapshot_name}' not found for domain {domain.name()}. "
                    f"Available: {', '.join(available) or 'none'}"
                )
            raise

        # Delete
        snapshot.delete(0)

        return {
            "name": snapshot_name,
            "domain": domain.name(),
            "deleted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _parse_snapshot_info(self, snapshot, domain_name):
        """Parse snapshot XML to extract metadata.

        Args:
            snapshot: virDomainSnapshot object
            domain_name: Name of parent domain

        Returns:
            dict: {name, domain, created, state, description, current, type}
        """
        from datetime import datetime
        from xml.etree import ElementTree as ET

        xml = snapshot.getXMLDesc(0)
        root = ET.fromstring(xml)

        # Parse creation time (unix timestamp)
        created_ts = root.findtext("creationTime", "")
        if created_ts:
            created = datetime.fromtimestamp(int(created_ts)).strftime("%Y-%m-%d %H:%M:%S")
        else:
            created = "N/A"

        return {
            "name": root.findtext("name", ""),
            "domain": domain_name,
            "created": created,
            "state": root.findtext("state", ""),
            "description": root.findtext("description", ""),
            "current": snapshot.isCurrent() == 1,
            "type": "internal",
        }

    def list_domains(self, name_prefix=None):
        """List all domains, optionally filtered by name prefix."""
        domains = self.connection.listAllDomains()
        if name_prefix:
            domains = [d for d in domains if d.name().startswith(name_prefix)]
        return domains

    def __repr__(self):
        """Return a string representation of the object."""
        inner = ", ".join(
            f"{k}={'******' if k in self._sensitive_attrs and v else v}"
            for k, v in self.__dict__.items()
            if not k.startswith("_") and not callable(v)
        )
        return f"{self.__class__.__name__}({inner})"
