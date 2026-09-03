"""Tests for the Libvirt provider using a stubbed LibvirtBind (no libvirt-python required)."""

import pytest

from broker import exceptions
from broker.broker import Broker
from broker.binds.libvirt import _build_domain_xml, _select_address
from broker.helpers import MockStub
from broker.providers.libvirt import Libvirt


class FakeVolume:
    """A minimal stand-in for a virStorageVol."""

    def __init__(self, name):
        self._name = name
        self.deleted = False

    def name(self):
        return self._name

    def delete(self, flags=0):
        self.deleted = True


class FakeDomain:
    """A minimal stand-in for a virDomain."""

    def __init__(self, name, xml=""):
        self._name = name
        self.active = True
        self._xml = xml

    def name(self):
        return self._name

    def isActive(self):
        return self.active

    def destroy(self):
        self.active = False

    def info(self):
        return [1, 2048 * 1024, 2048 * 1024, 2, 0]

    def XMLDesc(self):
        return self._xml


class LibvirtApiStub(MockStub):
    """Stubs LibvirtBind's public surface.

    Stubbing for:
     - base_volume_exists / list_base_volumes
     - create_overlay_volume / delete_volume
     - build_domain_xml / define_and_start
     - lookup_domain / list_domains
     - get_domain_ip
     - destroy_domain / undefine_domain / get_domain_disk_volumes
     - domain_action
     - list_networks / list_pools
    """

    def __init__(self, **kwargs):
        self.domains = {}
        self.volumes = {}
        self.base_volumes = {"base.qcow2"}
        self.ip = "192.168.122.50"
        self.raise_on_get_ip = False
        self.last_action = None
        super().__init__(in_dict=kwargs)

    def base_volume_exists(self, name):
        return name in self.base_volumes

    def list_base_volumes(self):
        return list(self.base_volumes)

    def list_networks(self):
        return ["default"]

    def list_pools(self):
        return ["default"]

    def create_overlay_volume(self, base_volume_name, overlay_name, size_gib):
        vol = FakeVolume(overlay_name)
        self.volumes[overlay_name] = vol
        return vol

    def build_domain_xml(self, **kwargs):
        return f"<domain><name>{kwargs['name']}</name></domain>"

    def define_and_start(self, domain_xml):
        name = domain_xml.split("<name>")[1].split("</name>")[0]
        dom = FakeDomain(name, xml=domain_xml)
        self.domains[name] = dom
        return dom

    def lookup_domain(self, name):
        return self.domains.get(name)

    def get_domain_ip(self, domain, prefer_ipv6=False, ipv4_fallback=True):
        if self.raise_on_get_ip:
            raise Exception("IP not yet available")
        return self.ip

    def destroy_domain(self, domain):
        domain.destroy()

    def undefine_domain(self, domain):
        self.domains.pop(domain.name(), None)

    def get_domain_disk_volumes(self, domain):
        vol = self.volumes.get(f"{domain.name()}.qcow2")
        return [vol] if vol else []

    def delete_volume(self, vol):
        vol.delete()
        self.volumes.pop(vol.name(), None)

    def domain_action(self, domain, action):
        self.last_action = (domain.name(), action)
        return action

    def list_domains(self, name_prefix=None):
        domains = list(self.domains.values())
        if name_prefix:
            domains = [d for d in domains if d.name().startswith(name_prefix)]
        return domains

    def get_broker_metadata(self, domain):
        """Return broker metadata for domains except those starting with 'external_'."""
        # Non-broker VMs (e.g., manually created) start with 'external_'
        # All others are considered broker-managed for testing purposes
        if domain.name().startswith("external_"):
            # Non-broker domain - no metadata
            return {}
        # Broker-managed domain with metadata
        return {"owner": "testuser", "libvirt_image": "base.qcow2", "cpus": "2", "ram": "2048"}


@pytest.fixture
def config_stub():
    libvirt_settings = MockStub(
        in_dict={
            "uri": "qemu:///system",
            "storage_pool": "default",
            "network": "default",
            "default_memory": 2048,
            "default_cpus": 2,
            "default_disk_size": 10,
            "virt_type": "kvm",
            "firmware": "bios",
            "dangling_behavior": "checkin",
            "default_username": None,
            "default_password": None,
            "default_key_filename": None,
            "auth_override": None,
            "name_prefix": None,
            "sync_unmanaged": False,
        }
    )
    ssh_settings = MockStub(
        in_dict={
            "HOST_IPV6": False,
            "HOST_IPV4_FALLBACK": True,
            "HOST_SSH_PORT": 22,
            "HOST_USERNAME": "root",
            "HOST_PASSWORD": "password",
            "HOST_SSH_KEY_FILENAME": "~/.ssh/id_rsa",
        }
    )
    return MockStub(in_dict={"LIBVIRT": libvirt_settings, "SSH": ssh_settings})


@pytest.fixture
def api_stub():
    return LibvirtApiStub()


@pytest.fixture
def libvirt_stub(api_stub, config_stub, monkeypatch):
    # Avoid real socket connections during checkout's SSH-readiness poll.
    monkeypatch.setattr(Libvirt, "_wait_for_ssh", lambda self, ip: None)
    return Libvirt(bind=api_stub, broker_settings=config_stub)


def test_construct_host_from_checkout_result(libvirt_stub):
    bx = Broker()
    provider_params = {"hostname": "192.168.122.50", "name": "broker_test_1234"}
    host = libvirt_stub.construct_host(provider_params, bx.host_classes)
    assert isinstance(host, bx.host_classes["host"])
    assert host.hostname == "192.168.122.50"
    assert host.name == "broker_test_1234"
    assert host._broker_provider == "Libvirt"
    assert host._prov_inst is libvirt_stub
    assert callable(host.release)


def test_construct_host_reconstruction(libvirt_stub):
    bx = Broker()
    host = libvirt_stub.construct_host(
        None,
        bx.host_classes,
        name="broker_test_5678",
        hostname="192.168.122.51",
        _broker_provider="Libvirt",
    )
    assert host.name == "broker_test_5678"
    assert host.hostname == "192.168.122.51"


def test_checkout_image_overlay_success(libvirt_stub, api_stub):
    result = libvirt_stub.checkout(libvirt_image="base.qcow2")
    assert result["hostname"] == api_stub.ip
    assert result["ip"] == api_stub.ip
    assert result["name"] in api_stub.domains
    assert f"{result['name']}.qcow2" in api_stub.volumes


def test_checkout_custom_xml_success(libvirt_stub, api_stub, tmp_path):
    xml_file = tmp_path / "custom.xml"
    xml_file.write_text("<domain><name>placeholder</name></domain>")
    result = libvirt_stub.checkout(libvirt_xml=str(xml_file))
    assert result["name"] in api_stub.domains
    assert result["name"] != "placeholder"


def test_checkout_missing_image_raises_user_error(libvirt_stub):
    with pytest.raises(exceptions.UserError):
        libvirt_stub.checkout(libvirt_image="nope.qcow2")


def test_dangling_behavior_checkin_on_failure(libvirt_stub, api_stub):
    libvirt_stub._ip_poll_timeout = 0  # fail on first attempt, no retry delay
    api_stub.raise_on_get_ip = True
    with pytest.raises(Exception):
        libvirt_stub.checkout(libvirt_image="base.qcow2")
    assert api_stub.domains == {}
    assert api_stub.volumes == {}


def test_dangling_behavior_store_on_failure(libvirt_stub, api_stub, config_stub, monkeypatch):
    libvirt_stub._ip_poll_timeout = 0  # fail on first attempt, no retry delay
    config_stub.LIBVIRT.dangling_behavior = "store"
    api_stub.raise_on_get_ip = True
    stored = []
    monkeypatch.setattr(
        "broker.providers.libvirt.helpers.update_inventory",
        lambda add=None, remove=None: stored.extend(add or []),
    )
    with pytest.raises(Exception):
        libvirt_stub.checkout(libvirt_image="base.qcow2")
    assert len(stored) == 1
    assert stored[0]["deploy_failed"] is True
    assert stored[0]["name"] in api_stub.domains


def test_release_checkin(libvirt_stub, api_stub):
    result = libvirt_stub.checkout(libvirt_image="base.qcow2")
    host = MockStub(in_dict={})
    host.name = result["name"]
    host._broker_args = {}
    libvirt_stub.release(host)
    assert api_stub.domains == {}
    assert api_stub.volumes == {}


def test_release_idempotent_when_domain_missing(libvirt_stub):
    host = MockStub(in_dict={})
    host.name = "does-not-exist"
    host._broker_args = {}
    libvirt_stub.release(host)  # should not raise


def test_get_inventory(libvirt_stub, api_stub):
    libvirt_stub.checkout(libvirt_image="base.qcow2")
    # Get inventory without prefix filter (will use default username prefix)
    inventory = libvirt_stub.get_inventory()
    assert len(inventory) == 1
    assert inventory[0]["_broker_provider"] == "Libvirt"
    assert inventory[0]["hostname"] == api_stub.ip
    assert inventory[0]["ip"] == api_stub.ip


@pytest.mark.parametrize(
    "flag,expected",
    [
        ("images", ["base.qcow2"]),
        ("networks", ["default"]),
        ("pools", ["default"]),
    ],
)
def test_provider_help_flags(libvirt_stub, flag, expected):
    results = libvirt_stub.provider_help(**{flag: True})
    assert results == expected


def test_provider_help_domains_flag(libvirt_stub, api_stub):
    libvirt_stub.checkout(libvirt_image="base.qcow2")
    name = next(iter(api_stub.domains))
    results = libvirt_stub.provider_help(domains=True)
    assert results == [name]


@pytest.mark.parametrize(
    "action",
    ["power-off", "power-on", "hard-stop", "reboot", "reset", "pause", "resume"],
)
def test_execute_action_dispatch(libvirt_stub, api_stub, action):
    libvirt_stub.checkout(libvirt_image="base.qcow2")
    name = next(iter(api_stub.domains))
    result = libvirt_stub.execute(libvirt_action=action, host=name)
    assert result == action
    assert api_stub.last_action == (name, action)


def test_execute_unknown_domain_raises(libvirt_stub):
    with pytest.raises(exceptions.ProviderError):
        libvirt_stub.execute(libvirt_action="reboot", host="does-not-exist")


def test_execute_missing_target_raises(libvirt_stub):
    with pytest.raises(exceptions.UserError):
        libvirt_stub.execute(libvirt_action="reboot")


@pytest.mark.parametrize(
    "addresses,prefer_ipv6,ipv4_fallback,expected",
    [
        ([{"addr": "192.168.122.10", "family": "ipv4"}], False, True, "192.168.122.10"),
        (
            [
                {"addr": "192.168.122.10", "family": "ipv4"},
                {"addr": "fe80::1", "family": "ipv6"},
            ],
            False,
            True,
            "192.168.122.10",
        ),
        (
            [{"addr": "2001:db8::1", "family": "ipv6"}, {"addr": "127.0.0.1", "family": "ipv4"}],
            False,
            True,
            "2001:db8::1",
        ),
        (
            [{"addr": "192.168.122.10", "family": "ipv4"}, {"addr": "2001:db8::1", "family": "ipv6"}],
            True,
            True,
            "2001:db8::1",
        ),
        ([{"addr": "169.254.1.1", "family": "ipv4"}], False, True, None),
        (
            [{"addr": "2001:db8::1", "family": "ipv6"}],
            False,
            False,
            "2001:db8::1",
        ),
    ],
)
def test_select_address(addresses, prefer_ipv6, ipv4_fallback, expected):
    assert _select_address(addresses, prefer_ipv6=prefer_ipv6, ipv4_fallback=ipv4_fallback) == expected


@pytest.mark.parametrize("firmware", ["bios", "efi"])
def test_build_domain_xml_portable(firmware):
    xml_str = _build_domain_xml(
        name="broker_test_1234",
        uuid_str="11111111-1111-1111-1111-111111111111",
        memory_mb=2048,
        vcpus=2,
        disk_name="broker_test_1234.qcow2",
        storage_pool="default",
        arch="x86_64",
        machine="q35",
        emulator="/usr/bin/qemu-system-x86_64",
        virt_type="kvm",
        firmware=firmware,
        network_name="default",
    )
    assert 'cpu mode="host-passthrough"' in xml_str
    if firmware == "efi":
        assert "<loader" in xml_str
        assert "<nvram" in xml_str
    else:
        assert "<loader" not in xml_str
        assert "<nvram" not in xml_str


def test_get_inventory_sync_unmanaged_disabled(libvirt_stub, api_stub):
    """Test that sync_unmanaged=False (default) excludes non-broker VMs from inventory."""
    # Create a broker-managed VM
    libvirt_stub.checkout(libvirt_image="base.qcow2")
    # Manually add a non-broker VM to the stub's domains
    non_broker_dom = FakeDomain("external_vm_1234")
    api_stub.domains["external_vm_1234"] = non_broker_dom

    # Get inventory - should only return broker-managed VM
    inventory = libvirt_stub.get_inventory()
    assert len(inventory) == 1
    # Verify it's the broker-managed VM (not the external one)
    assert not inventory[0]["name"].startswith("external_")
    assert "_read_only" not in inventory[0]
    assert "libvirt_image" in inventory[0]["_broker_args"]
    # Verify IP field is present
    assert "ip" in inventory[0]
    assert inventory[0]["ip"] == api_stub.ip


def test_get_inventory_sync_unmanaged_enabled(api_stub, monkeypatch):
    """Test that sync_unmanaged=True includes non-broker VMs with _read_only flag."""
    # Create config with sync_unmanaged enabled
    libvirt_settings = MockStub(
        in_dict={
            "uri": "qemu:///system",
            "storage_pool": "default",
            "network": "default",
            "default_memory": 2048,
            "default_cpus": 2,
            "default_disk_size": 10,
            "virt_type": "kvm",
            "firmware": "bios",
            "dangling_behavior": "checkin",
            "default_username": None,
            "default_password": None,
            "default_key_filename": None,
            "auth_override": None,
            "name_prefix": None,
            "sync_unmanaged": True,  # Enable sync_unmanaged
        }
    )
    ssh_settings = MockStub(
        in_dict={
            "HOST_IPV6": False,
            "HOST_IPV4_FALLBACK": True,
            "HOST_SSH_PORT": 22,
            "HOST_USERNAME": "root",
            "HOST_PASSWORD": "password",
            "HOST_SSH_KEY_FILENAME": "~/.ssh/id_rsa",
        }
    )
    config_stub = MockStub(in_dict={"LIBVIRT": libvirt_settings, "SSH": ssh_settings})

    # Create libvirt provider with updated config
    monkeypatch.setattr(Libvirt, "_wait_for_ssh", lambda self, ip: None)
    libvirt_stub = Libvirt(bind=api_stub, broker_settings=config_stub)

    # Create a broker-managed VM
    libvirt_stub.checkout(libvirt_image="base.qcow2")
    # Manually add a non-broker VM to the stub's domains
    non_broker_dom = FakeDomain("external_vm_1234")
    api_stub.domains["external_vm_1234"] = non_broker_dom

    # Get inventory - should return both VMs
    inventory = libvirt_stub.get_inventory()
    assert len(inventory) == 2

    # Find broker and non-broker VMs in inventory
    broker_vm = next((vm for vm in inventory if not vm["name"].startswith("external_")), None)
    non_broker_vm = next((vm for vm in inventory if vm["name"] == "external_vm_1234"), None)

    # Verify broker VM has normal structure
    assert broker_vm is not None
    assert "_read_only" not in broker_vm
    assert "libvirt_image" in broker_vm["_broker_args"]
    assert "ip" in broker_vm
    assert broker_vm["ip"] == api_stub.ip

    # Verify non-broker VM is marked as read-only
    assert non_broker_vm is not None
    assert non_broker_vm["_read_only"] is True
    assert non_broker_vm["_broker_args"]["_synced_vm"] is True
    assert non_broker_vm["_broker_args"]["name"] == "external_vm_1234"
    assert "ip" in non_broker_vm
    assert non_broker_vm["ip"] == api_stub.ip


def test_checkin_read_only_host(libvirt_stub, api_stub, monkeypatch, capsys):
    """Test that checking in a read-only host skips release() and shows warning."""
    # Create a broker instance and a fake host with _read_only flag
    from broker.broker import Broker
    from broker.hosts import Host

    bx = Broker()
    host = Host(hostname="192.168.122.100", name="external_vm", _read_only=True)
    host._prov_inst = libvirt_stub

    # Mock the release method to track if it's called
    release_called = []

    def mock_release():
        release_called.append(True)

    host.release = mock_release

    # Perform checkin
    result = bx._checkin(host)

    # Verify release() was NOT called
    assert len(release_called) == 0

    # Verify warning message was logged
    captured = capsys.readouterr()
    assert "read-only" in captured.out.lower() or "read-only" in captured.err.lower()

    # Verify host was still returned (for inventory removal)
    assert result is host
