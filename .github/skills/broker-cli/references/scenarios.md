# Broker Scenarios Reference

Scenarios chain multiple Broker actions in a YAML file with Jinja2 templating, looping, variable capture, and error handling.

Scenarios live in `$BROKER_DIRECTORY/scenarios/` (default: `./scenarios/`).

## Running Scenarios

```bash
broker scenarios list                                      # list available scenarios
broker scenarios execute my-scenario                       # run by name (must be in scenarios dir)
broker scenarios execute /path/to/scenario.yaml            # run by path
broker scenarios execute my-scenario --RHEL_VERSION 9.4   # pass variable overrides
broker scenarios execute my-scenario --config.settings.Container.runtime podman  # config overrides
broker scenarios execute my-scenario --background          # run in background
broker scenarios info my-scenario                          # inspect without running
broker scenarios info my-scenario --no-syntax              # plain text output
broker scenarios validate my-scenario                      # schema/syntax check only
```

## Scenario Structure

```yaml
# description: optional, shown in broker scenarios info
description: Deploy a RHEL host, run tests, then clean up.

# config: optional global settings for this scenario
config:
  log_path: my_scenario.log
  inventory_path: /path/to/custom_inventory.yaml
  settings:
    AnsibleTower:
      workflow_timeout: 600

# variables: optional key-value pairs, all overridable via CLI
variables:
  RHEL_VERSION: "9.4"
  HOST_COUNT: 1

# steps: required list of actions
steps:
  - name: Provision host
    action: checkout
    arguments:
      workflow: deploy-rhel
      deploy_rhel_version: "{{ RHEL_VERSION }}"
      count: "{{ HOST_COUNT }}"

  - name: Run a command
    action: ssh
    arguments:
      command: "hostname -f && cat /etc/os-release"
    with:
      hosts: scenario_inventory
    capture:
      as: os_info

  - name: Release hosts
    action: checkin
    with:
      hosts: scenario_inventory
```

## Available Actions

| Action | Purpose |
|--------|---------|
| `checkout` | Provision VMs or containers |
| `checkin` | Release hosts back to the provider |
| `ssh` | Run shell commands on target hosts |
| `scp` | Upload files to remote hosts |
| `sftp` | Transfer files (upload or download) |
| `execute` | Run arbitrary provider actions |
| `provider_info` | Query provider resources (workflows, images, etc.) |
| `inventory` | Query or sync Broker's inventory |
| `output` | Write content to stdout, stderr, or a file |
| `exit` | Terminate the scenario with a return code |
| `run_scenarios` | Chain other scenario files |

## Step Configuration Options

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Human-readable label; used in logs and step references |
| `action` | yes | One of the 11 actions above |
| `arguments` | no | Key-value pairs for the action; supports Jinja2 templating |
| `with.hosts` | no | Target host selection: `scenario_inventory`, `inventory`, filter expression, or index/slice |
| `when` | no | Conditional expression; step runs only when true |
| `parallel` | no | `true` (default) or `false` to run multi-host actions sequentially |
| `exit_on_error` | no | Set `false` to continue after step failure without a recovery block |
| `on_error` | no | List of recovery steps to run on failure, or `continue` to silently proceed |
| `capture.as` | no | Variable name to store the step result in; in loops, becomes a dict keyed by iteration |
| `capture.transform` | no | Jinja2 expression to transform the result before storing |
| `loop.iterable` | no | List or dict to iterate the step over |
| `loop.iter_var` | no | Variable name for the current item (default: `item`) |
| `loop.on_error` | no | Set `continue` to skip failed loop iterations |

## Jinja2 Templating Context

| Variable | Meaning |
|----------|---------|
| `scenario_inventory` | List of hosts checked out by this scenario |
| `inventory` | All hosts from the main Broker inventory |
| `step` | Current step's memory (`name`, `output`, `status`) |
| `previous_step` | Previous step's memory |
| `steps` | Dict of all steps by name |
| Any `variables` entry | User-defined variables (overridable via CLI) |
| Any `capture.as` name | Captured results from prior steps |

## Error Handling

```yaml
steps:
  - name: Optional cleanup step
    action: execute
    arguments:
      workflow: optional-cleanup-workflow
    exit_on_error: false    # continue even if this step fails

  - name: Conditional step
    action: ssh
    when: "{{ os_info is defined }}"
    arguments:
      command: "echo got info: {{ os_info }}"
    with:
      hosts: scenario_inventory

  - name: Risky action with recovery
    action: execute
    arguments:
      workflow: risky-workflow
    on_error:
      - name: Emergency checkin
        action: checkin
        with:
          hosts: scenario_inventory
```

**Always include a `checkin` step or an `on_error` recovery block.** Without one, hosts remain running indefinitely if the scenario fails.

## Looping

```yaml
steps:
  - name: Run command on each host
    action: ssh
    loop:
      iterable: scenario_inventory
      iter_var: host
      on_error: continue    # skip hosts that fail
    with:
      hosts: "{{ host }}"
    arguments:
      command: "echo hello from {{ host.hostname }}"
```

## Importing Scenarios from Git

Configure in `broker_settings.yaml`:
```yaml
SCENARIO_IMPORT:
    git_hosts:
        - url: "https://github.com"
          token: "ghp_mytoken"
          type: "github"
```

Import a scenario:

By default, Broker will reference the SatelliteQE/broker-scenarios repo on GitHub when an alternate source isn't provided.

```bash
broker scenarios import --list                      # list the scenarios avaiable to import
broker scenarios import --name deploy_sat_with_cap  # import a scenario by name (may have duplicates)
broker scenarios import --category ansibletower     # import all scenarios under a category
broker scenarios import --update                    # re-download all imported scenarios
# Using an alternate source
broker scenarios import https://github.com/myorg/myrepo/scenarios/my-scenario.yaml
broker scenarios import https://github.com/myorg/myrepo/scenarios/my-scenario.yaml --ref main
```

Imported scenarios are tracked in `$BROKER_DIRECTORY/scenarios/.broker-imports.yaml`.
