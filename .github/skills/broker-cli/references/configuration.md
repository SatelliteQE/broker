# Broker Configuration Reference

Config file: `~/.broker/broker_settings.yaml` (or `$BROKER_DIRECTORY/broker_settings.yaml`)
You can check `broker --version` to get the file paths Broker is looking for.

## Full Example

```yaml
_version: 0.7.0
less_colors: False
logging:
    console_level: info    # debug, info, warning, error
    file_level: debug
    # log_path: /custom/path/broker.log
    # structured: false    # JSON structured logging

inventory_fields:                 # these all correspong to fields in the inventory file for each host entry
  Host: hostname | name           # | = fallback if first field is empty
  Provider: _broker_provider
  Action: $action                 # special: $action, $timestamp
  OS: os_distribution os_distribution_version  # space = concatenate two fields

inventory_list_vars: hostname | name | ip

thread_limit: null    # integer to cap parallel threads

ssh:
    backend: hussh                          # hussh | ssh2-python | paramiko
    host_username: root
    host_password: "mysecret"
    host_ssh_port: 22
    host_ssh_key_filename: "/home/user/.ssh/id_rsa"
    host_ipv6: False
    host_ipv4_fallback: True

nicks:
    rhel9:
        workflow: deploy-rhel
        deploy_rhel_version: 9.4
        notes: "Requested by broker"        # You can pass any arbitrary key/value pair
    rhel8:
        workflow: deploy-rhel
        deploy_rhel_version: 8.10
    sat619:
        workflow: deploy-satellite
        deploy_sat_version: 6.19

SCENARIO_IMPORT:
    git_hosts:
        - url: "https://github.com"
          token: "ghp_mytoken"
          type: "github"
        - url: "https://gitlab.com"
          token: "glpat-mytoken"
          type: "gitlab"
```

## Inventory Field Special Values

| Value | Description |
|-------|-------------|
| `field1 | field2` | Use field2 if field1 is empty (fallback) |
| `field1 field2` | Concatenate both fields with a space |
| `$action` | Show the action that resulted in the host being checked out |
| `_broker_provider` | The provider that owns the host |

## Config CLI Commands

From Broker 0.6.x onward, use the `broker config` command group for all config interactions.

### View config

```bash
broker config view
broker config view AnsibleTower
broker config view Container.instances.remote-docker
```

A "chunk" is any subsection of the YAML config accessed with dotted notation for nested keys.

### Edit config

This will open an editor, so you likely want to use set (below)

```bash
broker config edit
broker config edit AnsibleTower
broker config edit Container.instances.remote-docker
```

### Set config values

```bash
broker config set Container.host_name my-container-host.example.com
broker config set Foreman foreman_settings.yaml       # copy file contents into chunk
broker config set Container.instances.local local_podman.json
```

Each `edit` or `set` operation creates a single backup. `restore` reverts only the most recent change:

```bash
broker config restore
```

### Nicks

Nicks are reusable shorthand bundles of Broker arguments. They are useful for repeated, stable workflows but are not required for ordinary one-off provisioning — explicit workflow args are clearer and don't need a config lookup. `broker checkout --nick rhel9` expands to the stored arguments plus any extra CLI args provided.

```bash
broker config nicks              # list all nicks
broker config nick rhel9         # show a specific nick
broker config view nicks.rhel9   # dotted path view
```

### Initialize config

```bash
broker config init               # initialize the config from the example file hosted in the official Broker GitHub repo
broker config init Container     # initialize just the Container "chunk" of the config
broker config init Container --from /path/to/settings.yaml   # copy the relevant contents of a local settings file
broker config init --from https://raw.githubusercontent.com/SatelliteQE/broker/master/broker_settings.yaml.example
```

### Migrate and validate config

```bash
broker config migrate
broker config migrate --force-version 0.6.0

broker config validate
broker config validate AnsibleTower
broker config validate Container:remote-docker
broker config validate all
```

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `BROKER_DIRECTORY` | Override default `~/.broker/` config location |
| `BROKER_SSH__BACKEND` | Override SSH backend (hussh, ssh2-python, paramiko) |
| `BROKER_AnsibleTower__base_url` | Override any nested config value |

Dynaconf env var format: `BROKER_<SECTION>__<KEY>` (double underscore for nesting)

Environment variables override instance-level config by default. An instance can set `override_envars: True` to take precedence over environment variables.

## Provider Config Details

### AnsibleTower

Authentication can use username/password or a personal access token (tokens are preferred). A username may still be present alongside a token for inventory sync behavior.

```yaml
AnsibleTower:
    base_url: "https://sat-aap.example.com/"
    username: "myuser"
    token: "my-personal-access-token"
    release_workflow: "remove-vm"
    extend_workflow: "extend-vm"
    new_expire_time: "+172800"
    workflow_timeout: 3600
    results_limit: 50
```

### Container

Provider instances may be local or remote. `runtime` selects `docker` or `podman`. If no `host` is set, Broker treats it as localhost.

Simplified config (single instance, no `instances:` nesting):

```yaml
Container:
    runtime: podman
    auto_map_ports: False
    results_limit: 50
```

Full multi-instance config: see [providers.md](./providers.md).

### Top-level vs instance-level settings

Instance-level settings are merged upward at runtime. Shared credentials can live at the provider top level, while per-instance differences stay nested.
