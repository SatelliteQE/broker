# Broker Providers Reference

## AnsibleTower / Satlab

**Install**: `uv tool install 'broker[satlab]'` (recommended) or `uv tool install 'broker[ansibletower]'`
> AnsibleTower support requires the `awxkit` package. It is **not** included in the base `broker` install.

Workflows are the primary mechanism — Broker calls AnsibleTower/AAP workflows to provision and release hosts.

```yaml
AnsibleTower:
    base_url: "https://sat-aap.example.com/"
    aap_version: null           # override AAP version detection if needed
    username: "myuser"
    token: "my-personal-access-token"   # preferred over password
    # password: "plaintext-password"    # fallback if token not available
    # inventory: "My Inventory"         # target inventory name
    release_workflow: "remove-vm"
    extend_workflow: "extend-vm"
    new_expire_time: "+172800"          # extend by 48h (in seconds)
    workflow_timeout: 3600
    max_resilient_wait: 7200
    results_limit: 50
```

**CLI introspection**:
```bash
# Template discovery is the preferred way to find the latest available OS version
broker providers AnsibleTower --templates --results-filter '"rhel-9" in @res'
broker providers AnsibleTower --templates --results-filter '"satellite" in @res'
broker providers AnsibleTower --templates                              # list all templates
broker providers AnsibleTower --workflows                              # list all workflows
broker providers AnsibleTower --workflow deploy-rhel                   # workflow details/args
broker providers AnsibleTower --results-filter '"deploy" in @res'      # filter any result list
```

**Checkout examples**:
```bash
broker checkout --workflow deploy-rhel --deploy_rhel_version 9.7      # explicit args (preferred)
broker checkout --workflow deploy-satellite --deploy_sat_version 6.19
broker checkout --nick rhel10                                          # nick shorthand, if desired
broker checkout --workflow deploy-rhel --AnsibleTower stage            # non-default instance
```

**Notes**:
- Prefer explicit workflow args for one-off provisioning. Nicks are useful for repeated, stable workflows.
- Prefer foreground checkout for normal host requests; it returns the hostname directly.
- Use background mode only when non-blocking execution is explicitly needed.
- Broker is designed around the SatLab standard for AAP; environments not following that pattern may have workflow compatibility issues.
- A username may be present alongside a token for inventory sync behavior.
- Common settings: `base_url`, `inventory`, `release_workflow`, `extend_workflow`, `new_expire_time`, `workflow_timeout`, `results_limit`.
- All checkouts must use workflows, while executes can use either workflows or job-templates.
- If a user asks for "stream" satellite/capsule, that is the very latest build so you can either not pass a version parameter or pass "stream" explicitly.
- Checkouts and executes can take a long time, so don't proactively timeout.

## Container (Docker/Podman)

**Install**: `uv tool install 'broker[podman]'` or `uv tool install 'broker[docker]'`

Containers are ephemeral — `broker checkin` fully deletes them, regardless of running state.

```yaml
Container:
    instances:
        local-podman:
            runtime: podman
            default: True
        remote-docker:
            host: "remote.host.example.com"
            host_username: "deploy-user"
            host_password: "secret"
            runtime: docker
    runtime: podman        # top-level default runtime
    # name_prefix: myteam  # prefix container names (default: local username)
    results_limit: 50
    auto_map_ports: False
```

**CLI introspection**:
```bash
broker providers Container --container-hosts                   # list compatible images
broker providers Container --container-host ubi9:latest        # get information about an image
broker providers Container --help
```

**Checkout examples**:
```bash
broker checkout --container-host ubi9:latest
broker checkout --container-host quay.io/fedora/fedora:40
broker checkout --container-host ubi9:latest --count 3
broker checkout --container-host ubi8:latest --Container remote-docker  # specific instance
```

**Notes**:
- If no `host` is set for a container instance, Broker treats it as localhost.
- `auto_map_ports: True` maps exposed container ports and stores those mappings in inventory and host objects.
- Remote container access may rely on the local user's SSH key being present on the remote runtime host.

## Foreman

**Install**: No extra dependency required.

Supports multiple Foreman instances. Broker provisions hosts via Foreman's compute resources.

```yaml
Foreman:
    instances:
        production:
            foreman_url: https://foreman.example.com
            foreman_username: admin
            foreman_password: secret
            organization: MyOrg
            location: MyLocation
            verify: /path/to/ca.crt   # path to CA cert, or False to skip TLS verify
            default: true
        staging:
            foreman_url: https://foreman-staging.example.com
            foreman_username: admin
            foreman_password: secret
            organization: MyOrg
            location: MyLocation
            verify: False
    name_prefix: broker   # prefix for host names created by broker
```

**Checkout examples**:
```bash
broker providers Foreman --help                  # see available args
broker checkout --Foreman production --<foreman-specific-args>
```

## Beaker

**Install**: `dnf install krb5-devel && uv tool install 'broker[beaker]'`

Uses Kerberos authentication by default. Submits Beaker jobs for host provisioning.

```yaml
Beaker:
    hub_url: https://beaker.example.com
    max_job_wait: 24h
```

**Checkout examples**:
```bash
broker providers Beaker --help
broker checkout --job_xml tests/data/beaker/test_job.xml
```

## OpenStack

**Install**: `uv tool install 'broker[openstack]'`
