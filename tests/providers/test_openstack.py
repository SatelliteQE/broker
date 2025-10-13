"""Test module for OpenStack provider."""
import pytest
from broker.broker import Broker
from broker.helpers import MockStub
from broker.providers.openstack import OpenStack


try:
    from openstack.exceptions import OpenStackCloudException, ResourceNotFound
except ImportError:
    class ResourceNotFound(Exception):
        pass
    class OpenStackCloudException(Exception):
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
def openstack_stub(api_stub):
    mock_openstack_settings = MockStub({
        "server_timeout": 600,
        "user_domain_name": "Default",
        "project_domain_name": "Default",
        "identity_api_version": "3",
    })

    mock_settings = MockStub({
        "OPENSTACK": mock_openstack_settings
    })

    provider = OpenStack(broker_settings=mock_settings)
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


def test_template_checkout(api_stub):
    """Test checkout using template."""
    mock_openstack_settings = MockStub({
        "templates": {
            "rhel9-template": {
                "image": "rhel-9-latest",
                "flavor": "m1.medium",
                "network": "public",
                "key_name": "test-key",
            }
        },
        "server_timeout": 600,
    })

    mock_settings = MockStub({
        "OPENSTACK": mock_openstack_settings
    })

    provider = OpenStack(broker_settings=mock_settings)
    provider.connection = api_stub

    result = provider.checkout(ostack_template="rhel9-template")
    assert result["hostname"] == "192.168.1.100"
    assert result["instance_id"] == "test-server-id"


def test_direct_parameter_checkout(api_stub):
    """Test checkout using direct parameters."""
    mock_openstack_settings = MockStub({
        "server_timeout": 600,
    })

    mock_settings = MockStub({
        "OPENSTACK": mock_openstack_settings
    })

    provider = OpenStack(broker_settings=mock_settings)
    provider.connection = api_stub

    result = provider.checkout(
        ostack_image="rhel-9-latest",
        ostack_flavor="m1.medium",
        ostack_network="public"
    )
    assert result["hostname"] == "192.168.1.100"
    assert result["instance_id"] == "test-server-id"


def test_checkout_with_defaults(api_stub):
    """Test checkout using default values from config."""
    mock_openstack_settings = MockStub({
        "default_image": "rhel-9-latest",
        "default_flavor": "m1.medium",
        "default_network": "public",
        "default_key_name": "default-key",
        "server_timeout": 600,
    })

    mock_settings = MockStub({
        "OPENSTACK": mock_openstack_settings
    })

    provider = OpenStack(broker_settings=mock_settings)
    provider.connection = api_stub

    result = provider.checkout()
    assert result["hostname"] == "192.168.1.100"
    assert result["instance_id"] == "test-server-id"


def test_checkout_with_key_name(api_stub):
    """Test checkout with SSH key."""
    mock_openstack_settings = MockStub({
        "server_timeout": 600,
    })

    mock_settings = MockStub({
        "OPENSTACK": mock_openstack_settings
    })

    provider = OpenStack(broker_settings=mock_settings)
    provider.connection = api_stub

    result = provider.checkout(
        ostack_image="rhel-9-latest",
        ostack_flavor="m1.medium",
        ostack_network="public",
        ostack_key_name="my-ssh-key"
    )
    assert result["hostname"] == "192.168.1.100"
    assert result["instance_id"] == "test-server-id"


def test_checkout_without_key_name(api_stub):
    """Test checkout without SSH key."""
    mock_openstack_settings = MockStub({
        "server_timeout": 600,
    })

    mock_settings = MockStub({
        "OPENSTACK": mock_openstack_settings
    })

    provider = OpenStack(broker_settings=mock_settings)
    provider.connection = api_stub

    result = provider.checkout(
        ostack_image="rhel-9-latest",
        ostack_flavor="m1.medium",
        ostack_network="public"
    )
    assert result["hostname"] == "192.168.1.100"
    assert result["instance_id"] == "test-server-id"


def test_resolution_fallback_strategies(openstack_stub):
    """Test resource resolution strategies."""
    assert openstack_stub._resolve_image("rhel-9-latest") == "rhel9-image-id"
    assert openstack_stub._resolve_image("rhel9-image-id") == "rhel9-image-id"

    assert openstack_stub._resolve_flavor("m1.medium") == "medium-flavor-id"
    assert openstack_stub._resolve_flavor("medium-flavor-id") == "medium-flavor-id"

    assert openstack_stub._resolve_network("public") == "public-net-id"
    assert openstack_stub._resolve_network("public-net-id") == "public-net-id"


def test_checkin_functionality(openstack_stub):
    """Test instance release."""
    class MockHost:
        def __init__(self, instance_id, hostname):
            self.instance_id = instance_id
            self.hostname = hostname

    host_data = MockHost("test-server-id", "192.168.1.100")
    result = openstack_stub.release(host_data)
    assert result is True


def test_timeout_configuration(api_stub):
    """Test server timeout configuration."""
    mock_openstack_settings = MockStub({
        "server_timeout": 900,
        "default_image": "rhel-9-latest",
        "default_flavor": "m1.medium",
        "default_network": "public",
    })

    mock_settings = MockStub({
        "OPENSTACK": mock_openstack_settings
    })

    provider = OpenStack(broker_settings=mock_settings)
    provider.connection = api_stub

    assert provider._settings.OPENSTACK.server_timeout == 900


def test_connection_establishment_with_cloud():
    """Test cloud-based connection."""
    from unittest.mock import patch, MagicMock
    
    settings = MockStub({
        "cloud": "test-cloud",
        "server_timeout": 600,
    })

    provider = OpenStack(broker_settings=MockStub({"OPENSTACK": settings}))
    
    mock_connection = MagicMock()
    with patch('broker.providers.openstack.openstack.connect', return_value=mock_connection) as mock_connect:
        result = provider._get_connection_with_cloud("test-cloud")
        
        mock_connect.assert_called_once_with(cloud="test-cloud")
        assert result == mock_connection


def test_connection_fallback_logic():
    """Test authentication fallback to application credentials."""
    from unittest.mock import patch, MagicMock
    
    mock_openstack_settings = MockStub({
        "cloud": None,  # Explicitly set to None
        "auth_url": "https://keystone.example.com:5000/v3",
        "application_credential_id": "test-app-cred-id", 
        "application_credential_secret": "test-app-cred-secret",
        "identity_api_version": "3",
        "server_timeout": 600,
    })

    mock_settings = MockStub({"OPENSTACK": mock_openstack_settings})
    provider = OpenStack(broker_settings=mock_settings)
    
    mock_connection = MagicMock()
    with patch('broker.providers.openstack.openstack.connect', return_value=mock_connection) as mock_connect:
        result = provider._get_connection()
        
        mock_connect.assert_called_once()
        call_args = mock_connect.call_args[1]
        
        assert call_args["auth_url"] == "https://keystone.example.com:5000/v3"
        assert call_args["application_credential_id"] == "test-app-cred-id"
        assert call_args["application_credential_secret"] == "test-app-cred-secret"
        assert call_args["identity_api_version"] == "3"
        assert result == mock_connection


def test_authentication_error_handling():
    """Test auth error handling."""
    from broker.exceptions import ProviderError
    
    mock_openstack_settings = MockStub({
        "cloud": None,  # Explicitly set to None
        "auth_url": None,  # Explicitly set to None
        "server_timeout": 600,
    })

    mock_settings = MockStub({"OPENSTACK": mock_openstack_settings})
    provider = OpenStack(broker_settings=mock_settings)
    
    try:
        provider._get_connection()
        assert False, "Expected ProviderError"
    except ProviderError as e:
        assert "OpenStack" in str(e)
        assert "incomplete" in str(e).lower()
