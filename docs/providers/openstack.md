# OpenStack Provider

The OpenStack provider allows Broker to provision and manage virtual machines on OpenStack clouds. It supports multiple authentication methods, flexible configuration options, and comprehensive resource management.

## Features

- **Multiple Authentication Methods**: clouds.yaml, application credentials, username/password
- **Flexible Configuration**: Template-based, flat config with defaults, direct parameters
- **Smart Resource Resolution**: Automatic name-to-UUID resolution for images, flavors, and networks
- **Configurable Timeouts**: Custom server provisioning timeouts
- **Nick Support**: Easy host aliases for common configurations
- **Full Lifecycle Management**: Checkout, checkin, extend, inventory

## Installation

Install the OpenStack provider dependencies:

```bash
pip install broker[openstack]
```

## Authentication Configuration

### Option 1: clouds.yaml (Recommended)

Most portable and secure method. Create `~/.config/openstack/clouds.yaml`:

```yaml
clouds:
  my-cloud:
    auth:
      auth_url: https://keystone.example.com:5000/v3
      username: myuser
      password: mypassword
      project_name: myproject
      user_domain_name: Default
      project_domain_name: Default
    region_name: RegionOne
    interface: public
```

Broker configuration:
```yaml
OpenStack:
  cloud: my-cloud
  server_timeout: 600
  default_image: rhel-9-Server-nightly-latest
  default_flavor: m1.medium
  default_network: public
  default_key_name: my-ssh-key
```

### Option 2: Application Credentials (Secure for CI/CD)

```yaml
OpenStack:
  auth_url: https://keystone.example.com:5000/v3
  application_credential_id: your-app-cred-id
  application_credential_secret: your-app-cred-secret
  region_name: RegionOne
  interface: public
  server_timeout: 600
  default_image: rhel-9-Server-nightly-latest
  default_flavor: m1.medium
  default_network: public
  default_key_name: my-ssh-key
```

### Option 3: Username/Password (Basic)

```yaml
OpenStack:
  auth_url: https://keystone.example.com:5000/v3
  username: myuser
  password: mypassword
  project_name: myproject
  user_domain_name: Default
  project_domain_name: Default
  region_name: RegionOne
  interface: public
  server_timeout: 600
  default_image: rhel-9-Server-nightly-latest
  default_flavor: m1.medium
  default_network: public
  default_key_name: my-ssh-key
```

## Configuration Options

### Core Settings

| Parameter | Description | Default |
|-----------|-------------|---------|
| `server_timeout` | Server provisioning timeout (seconds) | 600 |
| `region_name` | OpenStack region | - |
| `interface` | API endpoint type (public/internal/admin) | public |
| `identity_api_version` | Keystone API version | "3" |
| `user_domain_name` | User domain name | "Default" |
| `project_domain_name` | Project domain name | "Default" |

### Template-Based Configuration

Define reusable templates for common configurations:

```yaml
OpenStack:
  cloud: my-cloud
  server_timeout: 900
  templates:
    rhel9-dev:
      image: rhel-9-Server-nightly-latest
      flavor: m1.medium
      network: public
      key_name: dev-key
    rhel9-prod:
      image: rhel-9-Server-latest
      flavor: m1.large
      network: private
      key_name: prod-key
    ubuntu-test:
      image: ubuntu-22.04
      flavor: m1.small
      network: public
      key_name: test-key
```

### Flat Configuration with Defaults

Set defaults that apply when parameters aren't specified:

```yaml
OpenStack:
  cloud: my-cloud
  server_timeout: 600
  # Default values used when not specified
  default_image: rhel-9-Server-nightly-latest
  default_flavor: m1.medium
  default_network: public
  default_key_name: my-ssh-key
```

### Nick Configuration

Create aliases for frequently used configurations:

```yaml
OpenStack:
  cloud: my-cloud
  default_image: rhel-9-Server-nightly-latest
  default_flavor: m1.medium
  default_network: public
  default_key_name: my-ssh-key
  templates:
    rhel9-ci:
      image: rhel-9-Server-nightly-latest
      flavor: m1.medium
      network: public
      key_name: ci-key

nicks:
  rhel9-test:
    provider: OpenStack
    template: rhel9-ci
  rhel9-quick:
    provider: OpenStack
    image: rhel-9-Server-nightly-latest
    flavor: m1.small
```

## Usage Examples

### Basic Checkout Commands

```bash
# Using template
broker checkout --template rhel9-dev

# Using direct parameters
broker checkout --image rhel-9-Server-nightly-latest --flavor m1.medium --network public

# Using defaults (with flat config)
broker checkout --image rhel-9-Server-nightly-latest

# Using nick
broker checkout --nick rhel9-test

# With custom SSH key
broker checkout --image rhel-9-Server-nightly-latest --key-name my-custom-key

# With custom server name
broker checkout --image rhel-9-Server-nightly-latest --name my-test-server
```

### Advanced Usage

```bash
# Checkout multiple hosts
broker checkout --template rhel9-dev --count 3

# Checkout with custom timeout
broker checkout --image rhel-9-Server-nightly-latest --timeout 1200

# Checkout and execute commands
broker checkout --image rhel-9-Server-nightly-latest --execute "yum update -y"

# Checkout with specific project
broker checkout --image rhel-9-Server-nightly-latest --project my-project
```

### Host Management

```bash
# List OpenStack hosts
broker inventory --provider OpenStack

# Check in specific host
broker checkin <hostname>

# Check in all OpenStack hosts
broker checkin --all --provider OpenStack

# Extend host lease (if supported by OpenStack deployment)
broker extend <hostname> --hours 24
```

### Resource Discovery

```bash
# List available templates
broker checkout --help

# Show provider information
broker providers

# Debug connection
broker checkout --image rhel-9-Server-nightly-latest --debug
```

## Configuration Examples

### Development Environment

```yaml
# broker_settings.yaml
OpenStack:
  cloud: dev-cloud
  server_timeout: 300  # 5 minutes for quick testing
  default_image: rhel-9-Server-nightly-latest
  default_flavor: m1.small
  default_network: public
  default_key_name: dev-key
  templates:
    quick-test:
      image: rhel-9-Server-nightly-latest
      flavor: m1.small
      network: public
      key_name: dev-key

nicks:
  dev-rhel9:
    provider: OpenStack
    template: quick-test
```

### CI/CD Environment

```yaml
# broker_settings.yaml
OpenStack:
  # Application credentials for secure CI/CD
  auth_url: https://keystone.prod.example.com:5000/v3
  application_credential_id: ${OPENSTACK_APP_CRED_ID}
  application_credential_secret: ${OPENSTACK_APP_CRED_SECRET}
  region_name: RegionOne
  interface: public
  server_timeout: 900  # 15 minutes for reliable provisioning

  templates:
    ci-rhel9:
      image: rhel-9-Server-nightly-latest
      flavor: ci.standard.medium
      network: ci-network
      key_name: ci-jenkins-key
    ci-ubuntu:
      image: ubuntu-22.04-server
      flavor: ci.standard.medium
      network: ci-network
      key_name: ci-jenkins-key

nicks:
  ci-rhel:
    provider: OpenStack
    template: ci-rhel9
  ci-ubuntu:
    provider: OpenStack
    template: ci-ubuntu
```

### Production Environment

```yaml
# broker_settings.yaml
OpenStack:
  cloud: prod-cloud
  server_timeout: 1200  # 20 minutes for large instances

  templates:
    prod-rhel9-web:
      image: rhel-9-Server-latest
      flavor: prod.web.large
      network: prod-web-network
      key_name: prod-web-key
    prod-rhel9-db:
      image: rhel-9-Server-latest
      flavor: prod.db.xlarge
      network: prod-db-network
      key_name: prod-db-key

nicks:
  prod-web:
    provider: OpenStack
    template: prod-rhel9-web
  prod-db:
    provider: OpenStack
    template: prod-rhel9-db
```

## Troubleshooting

### Common Issues

**Authentication Plugin Not Found**
```bash
# Error: The plugin application_credential could not be found
# Solution: Install missing dependencies
pip install keystoneauth1

# Or use username/password authentication temporarily
```

**Image/Flavor/Network Not Found**
```bash
# The provider automatically resolves names to UUIDs
# Check available resources in your OpenStack deployment
openstack image list
openstack flavor list
openstack network list
```

**Timeout Issues**
```yaml
# Increase timeout for slow environments
OpenStack:
  server_timeout: 1800  # 30 minutes
```

**Network Configuration Issues**
```bash
# Ensure network exists and is accessible
openstack network show <network-name>

# Check network permissions for your project
openstack network list --project <project-name>
```

### Debug Mode

Enable debug logging:

```yaml
# broker_settings.yaml
logging:
  console_level: debug
  file_level: debug

# Or use command line
broker checkout --image rhel-9-Server-nightly-latest --debug
```

### Verification Commands

```bash
# Test OpenStack connectivity
openstack server list

# Test broker OpenStack provider
broker providers | grep -i openstack

# Test specific configuration
broker checkout --image rhel-9-Server-nightly-latest --dry-run
```

## Resource Management

### Automatic Cleanup

```bash
# Clean up all OpenStack resources
broker checkin --all --provider OpenStack

# Clean up resources older than 24 hours
broker checkin --all --provider OpenStack --older-than 24h
```

### Resource Monitoring

```bash
# Show current OpenStack usage
broker inventory --provider OpenStack --show-resources

# Export inventory to file
broker inventory --provider OpenStack --export inventory.json
```

## Best Practices

1. **Use clouds.yaml** for credentials management
2. **Set appropriate timeouts** based on your OpenStack performance
3. **Use templates** for consistent, reusable configurations
4. **Implement proper cleanup** in CI/CD pipelines
5. **Monitor resource usage** to avoid quota issues
6. **Use application credentials** for automated systems
7. **Test with small flavors** during development

## Compatibility

- **OpenStack SDK**: 3.x, 4.x, 5.x+
- **OpenStack Versions**: Queens+ (2018+)
- **Authentication**: Keystone v3.x
- **Python**: 3.10+

## Support

For issues and questions:
- Check the [Broker documentation](https://broker.readthedocs.io/)
- Review [OpenStack SDK documentation](https://docs.openstack.org/openstacksdk/)
- File issues on [GitHub](https://github.com/SatelliteQE/broker)