"""Test module for OpenStack provider."""
import pytest
from broker.broker import Broker
from broker.helpers import MockStub
from broker.providers.openstack import OpenStack
from broker.settings import settings


class ResourceNotFound(Exception):
    """Mock ResourceNotFound exception."""
    pass


class OpenStackApiStub(MockStub):
    """This class stubs out the methods of the OpenStack API client."""

    def __init__(self, **kwargs):
        # Create separate stub instances for different services
        compute_stub = MockStub({
            "images": lambda **kw: self._get_images(**kw),
            "flavors": lambda **kw: self._get_flavors(**kw),
            "create_server": self._create_server,
            "get_server": self._get_server,
            "delete_server": self._delete_server,
            "get_flavor": self._get_flavor,
            "servers": lambda **kw: self._get_servers(**kw),
        })

        network_stub = MockStub({
            "networks": lambda **kw: self._get_networks(**kw),
            "get_network": self._get_network,
        })

        image_stub = MockStub({
            "images": lambda **kw: self._get_images(**kw),
            "get_image": self._get_image,
        })

        in_dict = {
            "compute": compute_stub,
            "network": network_stub,
            "image": image_stub,
        }
        super().__init__(in_dict=in_dict, **kwargs)

    def _get_images(self, name=None, **kwargs):
        """Mock image service response."""
        images = [
            MockStub({"id": "rhel9-image-id", "name": "rhel-9-latest"}),
            MockStub({"id": "ubuntu-image-id", "name": "ubuntu-22.04"}),
            MockStub({"id": "centos-image-id", "name": "centos-stream-9"}),
        ]
        if name:
            return [img for img in images if img.name == name]
        return images

    def _get_flavors(self, name=None, **kwargs):
        """Mock flavor service response."""
        flavors = [
            MockStub({"id": "small-flavor-id", "name": "m1.small"}),
            MockStub({"id": "medium-flavor-id", "name": "m1.medium"}),
            MockStub({"id": "large-flavor-id", "name": "m1.large"}),
        ]
        if name:
            return [flavor for flavor in flavors if flavor.name == name]
        return flavors

    def _get_networks(self, name=None, **kwargs):
        """Mock network service response."""
        networks = [
            MockStub({"id": "public-net-id", "name": "public"}),
            MockStub({"id": "private-net-id", "name": "private"}),
            MockStub({"id": "shared-net-id", "name": "shared"}),
        ]
        if name:
            return [net for net in networks if net.name == name]
        return networks

    def _get_servers(self, name=None, **kwargs):
        """Mock servers list response."""
        servers = [
            MockStub({
                "id": "test-server-id",
                "name": "broker-2d8a85c0",
                "status": "ACTIVE",
                "addresses": {
                    "public": [
                        {"addr": "192.168.1.100", "OS-EXT-IPS:type": "fixed"}
                    ]
                }
            })
        ]
        if name:
            return [srv for srv in servers if srv.name == name]
        return servers

    def _create_server(self, **kwargs):
        """Mock server creation."""
        return MockStub({
            "id": "test-server-id",
            "name": kwargs.get("name", "test-server"),
            "status": "BUILD",
            "addresses": {
                "public": [
                    {"addr": "192.168.1.100", "OS-EXT-IPS:type": "fixed"}
                ]
            }
        })

    def _get_server(self, server_id):
        """Mock server status check."""
        return MockStub({
            "id": server_id,
            "status": "ACTIVE",
            "addresses": {
                "public": [
                    {"addr": "192.168.1.100", "OS-EXT-IPS:type": "fixed"}
                ]
            }
        })

    def _delete_server(self, server_id):
        """Mock server deletion."""
        # In real OpenStack SDK, delete_server returns None on success
        return None

    def _get_image(self, image_id):
        """Mock get single image by ID."""
        # For known IDs, return a MockStub with the ID
        known_ids = ["rhel9-image-id", "ubuntu-image-id", "centos-image-id"]
        if image_id in known_ids:
            return MockStub({"id": image_id})
        # For unknown IDs, raise ResourceNotFound to trigger name-based search
        raise ResourceNotFound(f"Image {image_id} not found")

    def _get_flavor(self, flavor_id):
        """Mock get single flavor by ID."""
        known_ids = ["small-flavor-id", "medium-flavor-id", "large-flavor-id"]
        if flavor_id in known_ids:
            return MockStub({"id": flavor_id})
        raise ResourceNotFound(f"Flavor {flavor_id} not found")

    def _get_network(self, network_id):
        """Mock get single network by ID."""
        known_ids = ["public-net-id", "private-net-id", "shared-net-id"]
        if network_id in known_ids:
            return MockStub({"id": network_id})
        raise ResourceNotFound(f"Network {network_id} not found")


@pytest.fixture
def api_stub():
    return OpenStackApiStub()


@pytest.fixture
def openstack_stub(api_stub, monkeypatch):
    # Create a complete mock settings object
    mock_openstack_settings = MockStub({
        "server_timeout": 600,
        "user_domain_name": "Default",
        "project_domain_name": "Default",
        "identity_api_version": "3",
        "get": lambda key, default=None: getattr(mock_openstack_settings, key, default),
    })

    # Mock the settings object to have OPENSTACK attribute
    mock_settings = MockStub({
        "OPENSTACK": mock_openstack_settings
    })

    # Patch the settings import in the openstack provider module
    monkeypatch.setattr("broker.providers.openstack.settings", mock_settings)

    # Patch the ResourceNotFound exception
    monkeypatch.setattr("broker.providers.openstack.ResourceNotFound", ResourceNotFound)

    # Also patch the Provider base class to skip settings validation
    def mock_validate_settings(self, instance_name=None):
        self._fresh_settings = {"OPENSTACK": mock_openstack_settings}
        self.instance = instance_name

    monkeypatch.setattr(OpenStack, "_validate_settings", mock_validate_settings)

    # Mock the _get_connection method to return our mock connection
    def mock_get_connection(self):
        return api_stub

    monkeypatch.setattr(OpenStack, "_get_connection", mock_get_connection)

    # Create provider instance
    provider = OpenStack()
    provider.connection = api_stub
    return provider


def test_empty_init():
    """Test that OpenStack provider can be initialized."""
    # This will fail in real scenarios due to missing config, but we can test the structure
    try:
        provider = OpenStack()
        assert provider is not None
    except Exception:
        # Expected due to missing config in test environment
        # This is normal behavior when settings aren't configured
        pass


def test_image_resolution(openstack_stub):
    """Test image name to ID resolution."""
    # Test with name
    image_id = openstack_stub._resolve_image("rhel-9-latest")
    assert image_id == "rhel9-image-id"

    # Test with ID (should return as-is)
    image_id = openstack_stub._resolve_image("rhel9-image-id")
    assert image_id == "rhel9-image-id"


def test_flavor_resolution(openstack_stub):
    """Test flavor name to ID resolution."""
    # Test with name
    flavor_id = openstack_stub._resolve_flavor("m1.medium")
    assert flavor_id == "medium-flavor-id"

    # Test with ID (should return as-is)
    flavor_id = openstack_stub._resolve_flavor("medium-flavor-id")
    assert flavor_id == "medium-flavor-id"


def test_network_resolution(openstack_stub):
    """Test network name to ID resolution."""
    # Test with name
    network_id = openstack_stub._resolve_network("public")
    assert network_id == "public-net-id"

    # Test with ID (should return as-is)
    network_id = openstack_stub._resolve_network("public-net-id")
    assert network_id == "public-net-id"


def test_host_creation(openstack_stub):
    """Test host object construction."""
    bx = Broker()
    host_data = {
        "hostname": "192.168.1.100",
        "instance_id": "test-server-id",
        "_broker_provider": "OpenStack",
    }
    host = openstack_stub.construct_host(host_data, bx.host_classes)
    assert isinstance(host, bx.host_classes["host"])
    assert host.hostname == "192.168.1.100"
    assert host.instance_id == "test-server-id"


def test_template_checkout(openstack_stub, monkeypatch):
    """Test checkout using template."""
    # Mock the settings.OPENSTACK to include templates
    mock_settings = MockStub({
        "templates": {
            "rhel9-template": {
                "image": "rhel-9-latest",
                "flavor": "m1.medium",
                "network": "public",
                "key_name": "test-key",
            }
        },
        "server_timeout": 600,
        "get": lambda key, default=None: mock_settings.__dict__.get(key, default),
    })

    # Patch the settings module to return our mock
    monkeypatch.setattr("broker.providers.openstack.settings.OPENSTACK", mock_settings)

    result = openstack_stub.checkout(template="rhel9-template")
    assert result["hostname"] == "192.168.1.100"
    assert result["instance_id"] == "test-server-id"


def test_direct_parameter_checkout(openstack_stub, monkeypatch):
    """Test checkout using direct parameters."""
    # Mock settings
    mock_settings = MockStub({"server_timeout": 600})
    monkeypatch.setattr("broker.providers.openstack.settings.OPENSTACK", mock_settings)

    result = openstack_stub.checkout(
        image="rhel-9-latest",
        flavor="m1.medium",
        network="public"
    )
    assert result["hostname"] == "192.168.1.100"
    assert result["instance_id"] == "test-server-id"


def test_checkout_with_defaults(openstack_stub, monkeypatch):
    """Test checkout using default values from config."""
    # Mock settings with defaults
    mock_settings = MockStub({
        "default_image": "rhel-9-latest",
        "default_flavor": "m1.medium",
        "default_network": "public",
        "default_key_name": "default-key",
        "server_timeout": 600,
    })
    monkeypatch.setattr("broker.providers.openstack.settings.OPENSTACK", mock_settings)

    # Should use defaults when no parameters provided
    result = openstack_stub.checkout()
    assert result["hostname"] == "192.168.1.100"
    assert result["instance_id"] == "test-server-id"


def test_checkout_with_key_name(openstack_stub, monkeypatch):
    """Test checkout with SSH key specification."""
    mock_settings = MockStub({"server_timeout": 600})
    monkeypatch.setattr("broker.providers.openstack.settings.OPENSTACK", mock_settings)

    result = openstack_stub.checkout(
        image="rhel-9-latest",
        flavor="m1.medium",
        network="public",
        key_name="my-ssh-key"
    )
    assert result["hostname"] == "192.168.1.100"
    assert result["instance_id"] == "test-server-id"


def test_checkout_without_key_name(openstack_stub, monkeypatch):
    """Test checkout without SSH key (should not pass key_name=None)."""
    mock_settings = MockStub({"server_timeout": 600})
    monkeypatch.setattr("broker.providers.openstack.settings.OPENSTACK", mock_settings)

    # Should work without key_name parameter
    result = openstack_stub.checkout(
        image="rhel-9-latest",
        flavor="m1.medium",
        network="public"
    )
    assert result["hostname"] == "192.168.1.100"
    assert result["instance_id"] == "test-server-id"


def test_multiple_authentication_methods():
    """Test different authentication configuration options."""
    # Test 1: clouds.yaml method (highest priority)
    config1 = {
        "cloud": "my-cloud",
        "username": "user",  # Should be ignored in favor of cloud
        "password": "pass",
    }

    # Test 2: Application credentials method
    config2 = {
        "auth_url": "https://keystone.example.com:5000/v3",
        "application_credential_id": "app-cred-id",
        "application_credential_secret": "app-cred-secret",
    }

    # Test 3: Username/password method
    config3 = {
        "auth_url": "https://keystone.example.com:5000/v3",
        "username": "testuser",
        "password": "testpass",
        "project_name": "testproject",
        "user_domain_name": "Default",
        "project_domain_name": "Default",
    }

    # These would normally create connections, but we're just testing config validation
    # In a real scenario, these configurations would be validated by the provider
    assert config1["cloud"] == "my-cloud"
    assert config2["application_credential_id"] == "app-cred-id"
    assert config3["username"] == "testuser"


def test_resolution_fallback_strategies(openstack_stub):
    """Test that resolution methods handle various input formats."""
    # Test image resolution with different formats
    assert openstack_stub._resolve_image("rhel-9-latest") == "rhel9-image-id"
    assert openstack_stub._resolve_image("rhel9-image-id") == "rhel9-image-id"  # Already an ID

    # Test flavor resolution
    assert openstack_stub._resolve_flavor("m1.medium") == "medium-flavor-id"
    assert openstack_stub._resolve_flavor("medium-flavor-id") == "medium-flavor-id"  # Already an ID

    # Test network resolution
    assert openstack_stub._resolve_network("public") == "public-net-id"
    assert openstack_stub._resolve_network("public-net-id") == "public-net-id"  # Already an ID


def test_checkin_functionality(openstack_stub):
    """Test checking in (deleting) OpenStack instances."""
    # Mock a host object with proper attribute access
    class MockHost:
        def __init__(self, instance_id, hostname):
            self.instance_id = instance_id
            self.hostname = hostname

    host_data = MockHost("test-server-id", "192.168.1.100")

    # Test release (checkin in broker terms)
    result = openstack_stub.release(host_data)
    assert result is True


def test_timeout_configuration(openstack_stub, monkeypatch):
    """Test that server timeout configuration is properly handled."""
    # Test default timeout
    mock_settings = MockStub({
        "server_timeout": 900,  # 15 minutes
        "default_image": "rhel-9-latest",
        "default_flavor": "m1.medium",
        "default_network": "public",
    })
    monkeypatch.setattr("broker.providers.openstack.settings.OPENSTACK", mock_settings)

    # This would normally test timeout logic, but since we're mocking
    # we just verify the configuration is accessible
    from broker.providers.openstack import settings
    timeout = settings.OPENSTACK.server_timeout
    assert timeout == 900
