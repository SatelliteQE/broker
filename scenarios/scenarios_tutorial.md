# Broker Scenarios Tutorial

Scenarios are a powerful feature that allows you to chain multiple Broker actions together in YAML files. Instead of running separate `broker checkout`, `broker checkin`, and shell commands, you can define a complete workflow in a single scenario file.

This tutorial covers everything you need to know to write effective scenarios.

## Table of Contents

1. [Quick Start](#quick-start)
2. [Scenario Structure](#scenario-structure)
3. [Available Actions](#available-actions)
4. [Step Configuration Options](#step-configuration-options)
5. [Variables and Templating](#variables-and-templating)
6. [Host Selection and Inventory Filters](#host-selection-and-inventory-filters)
7. [Loops](#loops)
8. [Error Handling](#error-handling)
9. [Capturing Output](#capturing-output)
10. [Complete Examples](#complete-examples)
11. [CLI Reference](#cli-reference)

---

## Quick Start

Here's a minimal scenario that checks out a container, runs a command, and checks it back in:

```yaml
# my_first_scenario.yaml
steps:
  - name: Provision a container
    action: checkout
    arguments:
      container_host: ubi8

  - name: Run a command
    action: ssh
    arguments:
      command: "cat /etc/os-release"
    with:
      hosts: scenario_inventory

  - name: Release the container
    action: checkin
    with:
      hosts: scenario_inventory
```

Save this file to `~/.broker/scenarios/my_first_scenario.yaml`, then run:

```bash
broker scenarios execute my_first_scenario
```

---

## Scenario Structure

A scenario file has three main sections:

```yaml
# config: Optional global settings
config:
  inventory_path: /path/to/custom_inventory.yaml  # Custom inventory file
  log_path: my_scenario.log                        # Custom log file
  settings:                                        # Provider-specific settings
    Container:
      runtime: podman

# variables: Optional key-value pairs for use in steps
variables:
  MY_IMAGE: ubi8
  INSTALL_PACKAGES: true
  TIMEOUT: 60

# steps: Required list of actions to execute
steps:
  - name: Step 1
    action: checkout
    arguments:
      container_host: "{{ MY_IMAGE }}"
```

### Config Section

The `config` section allows you to customize scenario behavior:

| Key | Description |
|-----|-------------|
| `inventory_path` | Path to a custom inventory file for this scenario |
| `log_path` | Custom log file path (see path resolution rules below) |
| `settings` | Nested map of provider-specific settings that override `broker_settings.yaml` |

**Log Path Resolution Rules:**
- **Not specified**: Uses the default `broker.log` file in `{BROKER_DIRECTORY}/logs/`
- **Filename only** (e.g., `my_scenario.log`): Creates file in `{BROKER_DIRECTORY}/logs/`
- **Absolute path with filename** (e.g., `/var/log/broker/custom.log`): Uses as-is
- **Absolute directory** (e.g., `/var/log/broker/`): Creates `{scenario_name}.log` in that directory

**Example: Override AnsibleTower timeout**
```yaml
config:
  settings:
    AnsibleTower:
      workflow_timeout: 600
```

### Variables Section

Variables defined here are available throughout your scenario via Jinja2 templating:

```yaml
variables:
  RHEL_VERSION: "9.4"
  HOST_COUNT: 3
  DEPLOY_CONFIG:
    memory: 4096
    cpus: 2

steps:
  - name: Deploy hosts
    action: checkout
    arguments:
      workflow: deploy-rhel
      rhel_version: "{{ RHEL_VERSION }}"
      count: "{{ HOST_COUNT }}"
```

Variables can be overridden via CLI:
```bash
broker scenarios execute my_scenario --RHEL_VERSION 8.10 --HOST_COUNT 5
```

---

## Available Actions

Scenarios support 11 different actions:

### `checkout` - Provision Hosts

Checks out hosts from a provider (VMs, containers, etc.).

```yaml
- name: Provision RHEL VM
  action: checkout
  arguments:
    workflow: deploy-rhel           # AnsibleTower workflow
    rhel_version: "9.4"
    count: 2                        # Number of hosts
    note: "Testing new feature"

- name: Provision container
  action: checkout
  arguments:
    container_host: ubi8            # Container image
    ports: "22:2222 80:8080"        # Port mappings
    environment: "DEBUG=1"          # Environment variables
```

**Output:** List of host objects, automatically added to `scenario_inventory`.

### `checkin` - Release Hosts

Releases hosts back to the provider.

```yaml
- name: Release all scenario hosts
  action: checkin
  with:
    hosts: scenario_inventory

- name: Release specific hosts
  action: checkin
  with:
    hosts: "@scenario_inv[0:2]"     # First two hosts only
```

**Output:** `true` on success.

### `ssh` - Execute Remote Commands

Runs shell commands on target hosts.

```yaml
- name: Check disk space
  action: ssh
  arguments:
    command: "df -h"
    timeout: 30                     # Optional timeout in seconds
  with:
    hosts: scenario_inventory
```

**Output:**
- Single host: A Result object with `stdout`, `stderr`, and `status` attributes
- Multiple hosts: Dictionary mapping hostname to Result object

### `scp` - Copy Files to Hosts

Uploads files to remote hosts.

```yaml
- name: Upload config file
  action: scp
  arguments:
    source: /local/path/config.yaml
    destination: /etc/myapp/config.yaml
  with:
    hosts: scenario_inventory
```

**Output:** Result object with success message.

### `sftp` - Transfer Files

Transfers files via SFTP (supports both upload and download).

```yaml
- name: Upload script
  action: sftp
  arguments:
    source: ./scripts/setup.sh
    destination: /root/setup.sh
    direction: upload               # Default

- name: Download logs
  action: sftp
  arguments:
    source: /var/log/app.log
    destination: ./logs/app.log
    direction: download
  with:
    hosts: scenario_inventory
```

**Output:** Result object with success message.

### `execute` - Run Provider Actions

Executes arbitrary provider actions (like power operations, extend lease, etc.).

```yaml
- name: Extend VM lease
  action: execute
  arguments:
    workflow: extend-lease
    source_vm: "{{ host.name }}"
    extend_days: 7
```

**Output:** Provider-specific result.

### `provider_info` - Query Provider Resources

Queries a provider for available resources (workflows, images, inventories, etc.).

```yaml
# Flag-style query: list all workflows
- name: List available workflows
  action: provider_info
  arguments:
    provider: AnsibleTower
    query: workflows
    tower_inventory: my-inventory

# Value-style query: get specific workflow details
- name: Get workflow details
  action: provider_info
  arguments:
    provider: AnsibleTower
    query:
      workflow: deploy-rhel
```

**Available queries by provider:**

| Provider | Flag Queries | Value Queries |
|----------|--------------|---------------|
| AnsibleTower | `workflows`, `inventories`, `job_templates`, `templates`, `flavors` | `workflow`, `inventory`, `job_template` |
| Container | `container_hosts`, `container_apps` | `container_host`, `container_app` |
| Beaker | `jobs` | `job` |
| Foreman | `hostgroups` | `hostgroup` |
| OpenStack | `images`, `flavors`, `networks`, `templates` | - |

**Output:** Dictionary or list of provider resource data.

### `inventory` - Query or Sync Inventory

Works with Broker's inventory system.

```yaml
# Sync inventory from a provider
- name: Sync Tower inventory
  action: inventory
  arguments:
    sync: AnsibleTower

# Filter inventory
- name: Get RHEL hosts
  action: inventory
  arguments:
    filter: "'rhel' in @inv.name"
```

**Output:** Inventory data (list of host dictionaries).

### `output` - Write Content

Writes content to stdout, stderr, or a file.

```yaml
# Write to stdout
- name: Display message
  action: output
  arguments:
    content: "Deployment complete!"
    destination: stdout             # Default

# Write to file (format auto-detected by extension)
- name: Save results to JSON
  action: output
  arguments:
    content: "{{ workflow_results }}"
    destination: /tmp/results.json
    mode: overwrite                 # or "append"

# Write to YAML file
- name: Save hosts list
  action: output
  arguments:
    content: "{{ scenario_inventory }}"
    destination: ./hosts.yaml
```

**Output:** The content that was written.

### `exit` - Exit Scenario

Terminates scenario execution with a return code.

```yaml
- name: Exit on failure condition
  action: exit
  arguments:
    return_code: 1
    message: "Required condition not met"
  when: not deployment_successful

- name: Exit successfully
  action: exit
  arguments:
    return_code: 0
    message: "All tests passed"
```

### `run_scenarios` - Execute Other Scenarios

Chains other scenario files for modular workflows.

```yaml
- name: Run cleanup scenarios
  action: run_scenarios
  arguments:
    paths:
      - /path/to/cleanup_vms.yaml
      - /path/to/cleanup_containers.yaml
```

**Output:** List of `{path, success}` dictionaries.

---

## Step Configuration Options

Each step supports several configuration options:

### `name` (required)

Human-readable identifier for the step. Used for logging and step memory references.

```yaml
- name: Deploy production servers
```

### `action` (required)

One of the 11 available actions.

### `arguments` (optional)

Key-value pairs passed to the action. Supports templating.

### `with` - Target Host Selection

Specifies which hosts an action should target.

```yaml
with:
  hosts: scenario_inventory         # All hosts from this scenario
  hosts: inventory                  # All hosts from main Broker inventory
  hosts: "@scenario_inv[0]"         # First scenario host
  hosts: "'rhel' in @inv.name"      # Filtered hosts
```

### `when` - Conditional Execution

Execute step only if condition is true.

```yaml
- name: Install packages
  action: ssh
  arguments:
    command: "dnf install -y httpd"
  with:
    hosts: scenario_inventory
  when: INSTALL_PACKAGES == true

- name: Cleanup only if failed
  action: checkin
  with:
    hosts: scenario_inventory
  when: previous_step.status == 'failed'
```

### `parallel` - Control Parallel Execution

For multi-host actions, control whether they run in parallel or sequentially.

```yaml
- name: Run migrations sequentially
  action: ssh
  arguments:
    command: "./migrate.sh"
  with:
    hosts: scenario_inventory
  parallel: false                   # Run one at a time (default: true)
```

### `exit_on_error` - Continue on Failure

By default, scenarios stop on step failure. Set to `false` to continue.

```yaml
- name: Optional cleanup
  action: ssh
  arguments:
    command: "rm -rf /tmp/cache"
  with:
    hosts: scenario_inventory
  exit_on_error: false              # Continue even if this fails
```

---

## Variables and Templating

Scenarios use Jinja2 templating for dynamic values.

### Template Syntax

```yaml
# Simple variable
"{{ variable_name }}"

# Attribute access
"{{ host.hostname }}"

# Method calls
"{{ result.stdout.strip() }}"

# String interpolation
"Host {{ hostname }} returned: {{ result.stdout }}"

# Expressions
"{{ count * 2 }}"
```

### Available Context Variables

| Variable | Description |
|----------|-------------|
| `step` | Current step's memory (name, output, status) |
| `previous_step` | Previous step's memory |
| `steps` | Dictionary of all steps by name |
| `scenario_inventory` | List of hosts checked out by this scenario |
| User variables | Any variable from `variables` section or CLI |
| Captured variables | Any variable captured via `capture` |

### Step Memory Attributes

Access previous step results:

```yaml
- name: Run command
  action: ssh
  arguments:
    command: "ls /root"
  with:
    hosts: scenario_inventory
  capture:
    as: ls_result

- name: Check if file exists
  action: ssh
  arguments:
    command: "cat /root/config.yaml"
  with:
    hosts: scenario_inventory
  when: "'config.yaml' in ls_result.stdout"
```

Step memory has these attributes:
- `name` - Step name
- `output` - Action result
- `status` - "pending", "running", "completed", "skipped", or "failed"

---

## Host Selection and Inventory Filters

### Inventory References

| Expression | Description |
|------------|-------------|
| `scenario_inventory` | All hosts checked out by this scenario |
| `inventory` | All hosts from main Broker inventory |
| `@scenario_inv` | Scenario inventory (for filtering) |
| `@inv` | Main inventory (for filtering) |

### Filter Expressions

Filter hosts using Python-like expressions:

```yaml
# Index access
"@scenario_inv[0]"                  # First host
"@scenario_inv[-1]"                 # Last host

# Slicing
"@scenario_inv[0:3]"                # First three hosts
"@scenario_inv[1:]"                 # All except first
"@inv[:]"                           # All hosts (as list)

# Attribute filtering
"'rhel' in @inv.name"               # Hosts with 'rhel' in name
"'satellite' in @inv.hostname"      # Hosts with 'satellite' in hostname
"@inv._broker_provider == 'AnsibleTower'"  # By provider
```

---

## Loops

Execute a step multiple times over an iterable.

### Basic Loop

```yaml
- name: Process each host
  action: ssh
  arguments:
    command: "hostname"
  loop:
    iterable: "@scenario_inv[:]"    # Loop over all scenario hosts
    iter_var: current_host
  with:
    hosts: "{{ current_host }}"     # Use loop variable
```

### Loop Over Variables

```yaml
variables:
  PACKAGES:
    - httpd
    - postgresql
    - redis

steps:
  - name: Install packages
    action: ssh
    arguments:
      command: "dnf install -y {{ package }}"
    loop:
      iterable: PACKAGES
      iter_var: package
    with:
      hosts: scenario_inventory
```

### Loop with Dictionary Items

Use tuple unpacking to iterate over dictionary items:

```yaml
- name: Get command output from each host
  action: ssh
  arguments:
    command: "uptime"
  with:
    hosts: scenario_inventory
  capture:
    as: uptime_results              # Dict: {hostname: Result}

- name: Process each result
  action: output
  arguments:
    content: "{{ hostname }}: {{ result.stdout }}"
    destination: stdout
  loop:
    iterable: uptime_results.items()
    iter_var: hostname, result      # Tuple unpacking
```

### Loop with Conditional

The `when` condition is evaluated for each iteration:

```yaml
- name: Only process successful results
  action: output
  arguments:
    content: "{{ hostname }} is healthy"
  loop:
    iterable: check_results.items()
    iter_var: hostname, result
  when: result.status == 0
```

### Loop Error Handling

Continue loop even if some iterations fail:

```yaml
- name: Run risky command on each host
  action: ssh
  arguments:
    command: "risky-operation"
  loop:
    iterable: "@scenario_inv[:]"
    iter_var: host
    on_error: continue              # Don't stop on failure
  with:
    hosts: "{{ host }}"
```

---

## Error Handling

### Simple Continue

Proceed to next step even if current step fails:

```yaml
- name: Optional step
  action: ssh
  arguments:
    command: "optional-command"
  with:
    hosts: scenario_inventory
  on_error: continue
```

### Recovery Steps

Execute cleanup or recovery actions when a step fails:

```yaml
- name: Critical operation
  action: ssh
  arguments:
    command: "critical-operation"
  with:
    hosts: scenario_inventory
  on_error:
    - name: Log failure
      action: output
      arguments:
        content: "Critical operation failed, cleaning up..."
        destination: stderr

    - name: Cleanup resources
      action: checkin
      with:
        hosts: scenario_inventory

    - name: Exit with error
      action: exit
      arguments:
        return_code: 1
        message: "Critical operation failed"
```

### Exit on Error Control

For non-critical steps without recovery:

```yaml
- name: Try to gather metrics
  action: ssh
  arguments:
    command: "collect-metrics"
  with:
    hosts: scenario_inventory
  exit_on_error: false              # Continue regardless of outcome
```

---

## Capturing Output

Store step results in variables for later use.

### Basic Capture

```yaml
- name: Get hostname
  action: ssh
  arguments:
    command: "hostname -f"
  with:
    hosts: scenario_inventory
  capture:
    as: hostname_result

- name: Display hostname
  action: output
  arguments:
    content: "FQDN: {{ hostname_result.stdout }}"
```

### Capture with Transform

Extract or transform the output before storing:

```yaml
- name: Get OS version
  action: ssh
  arguments:
    command: "cat /etc/redhat-release"
  with:
    hosts: scenario_inventory
  capture:
    as: os_version
    transform: "{{ step.output.stdout.strip() }}"
```

### Capture in Loops

When capturing loop results, each iteration's result is stored in a dictionary:

```yaml
- name: Check each service
  action: ssh
  arguments:
    command: "systemctl is-active {{ service }}"
  loop:
    iterable: SERVICES
    iter_var: service
  with:
    hosts: scenario_inventory
  capture:
    as: service_status              # Dict: {"httpd": Result, "nginx": Result, ...}
```

Use custom keys for better organization:

```yaml
- name: Get workflow details
  action: provider_info
  arguments:
    provider: AnsibleTower
    query:
      workflow: "{{ workflow_name }}"
  loop:
    iterable: workflow_list
    iter_var: workflow_name
  capture:
    as: workflow_details
    key: result.name                # Use workflow name as dict key
```

---

## Complete Examples

### Example 1: CI/CD Pipeline Test

```yaml
# ci_pipeline_test.yaml
# Tests deployment and runs verification on containers

variables:
  TEST_IMAGE: ubi9
  HOST_COUNT: 2
  TEST_PACKAGES:
    - python3
    - git
    - make

config:
  inventory_path: ~/.broker/ci_test_inventory.yaml

steps:
  - name: Provision test containers
    action: checkout
    arguments:
      container_host: "{{ TEST_IMAGE }}"
      count: "{{ HOST_COUNT }}"

  - name: Install test dependencies
    action: ssh
    arguments:
      command: "dnf install -y {{ TEST_PACKAGES | join(' ') }}"
    with:
      hosts: scenario_inventory
    parallel: true

  - name: Clone test repository
    action: ssh
    arguments:
      command: "git clone https://github.com/example/tests.git /root/tests"
    with:
      hosts: scenario_inventory

  - name: Run tests
    action: ssh
    arguments:
      command: "cd /root/tests && make test"
      timeout: 300
    with:
      hosts: scenario_inventory
    capture:
      as: test_results

  - name: Save test results
    action: output
    arguments:
      content: "{{ test_results }}"
      destination: ./test_results.yaml

  - name: Cleanup containers
    action: checkin
    with:
      hosts: scenario_inventory
```

### Example 2: Multi-Provider Inventory Management

```yaml
# sync_and_report.yaml
# Syncs inventory from multiple providers and generates a report

variables:
  PROVIDERS:
    - AnsibleTower
    - Container
    - Beaker

steps:
  - name: Sync all provider inventories
    action: inventory
    arguments:
      sync: "{{ provider }}"
    loop:
      iterable: PROVIDERS
      iter_var: provider
      on_error: continue
    capture:
      as: sync_results

  - name: Load full inventory
    action: inventory
    arguments: {}
    capture:
      as: full_inventory

  - name: Generate inventory report
    action: output
    arguments:
      content: |
        # Broker Inventory Report
        Generated: {{ now }}
        Total hosts: {{ full_inventory | length }}
        
        Hosts by provider:
        {% for host in full_inventory %}
        - {{ host.hostname }} ({{ host._broker_provider }})
        {% endfor %}
      destination: ./inventory_report.md

  - name: Display summary
    action: output
    arguments:
      content: "Synced {{ PROVIDERS | length }} providers. Total hosts: {{ full_inventory | length }}"
```

### Example 3: Deployment with Rollback

```yaml
# deploy_with_rollback.yaml
# Deploys application with automatic rollback on failure

variables:
  WORKFLOW: deploy-application
  APP_VERSION: "2.1.0"
  ROLLBACK_VERSION: "2.0.0"

steps:
  - name: Deploy new version
    action: checkout
    arguments:
      workflow: "{{ WORKFLOW }}"
      app_version: "{{ APP_VERSION }}"
    on_error:
      - name: Log deployment failure
        action: output
        arguments:
          content: "Deployment of {{ APP_VERSION }} failed, initiating rollback..."
          destination: stderr

      - name: Deploy rollback version
        action: checkout
        arguments:
          workflow: "{{ WORKFLOW }}"
          app_version: "{{ ROLLBACK_VERSION }}"
        on_error:
          - name: Critical failure
            action: exit
            arguments:
              return_code: 2
              message: "Both deployment and rollback failed!"

      - name: Rollback successful
        action: output
        arguments:
          content: "Rollback to {{ ROLLBACK_VERSION }} successful"

      - name: Exit with warning
        action: exit
        arguments:
          return_code: 1
          message: "Deployed rollback version due to failure"

  - name: Verify deployment
    action: ssh
    arguments:
      command: "curl -s localhost:8080/health"
    with:
      hosts: scenario_inventory
    capture:
      as: health_check

  - name: Deployment complete
    action: output
    arguments:
      content: "Successfully deployed {{ APP_VERSION }}"
```

---

## CLI Reference

### List Available Scenarios

```bash
broker scenarios list
```

Shows all scenarios in `~/.broker/scenarios/`.

### Execute a Scenario

```bash
# By name (from scenarios directory)
broker scenarios execute my_scenario

# By path
broker scenarios execute /path/to/scenario.yaml

# With variable overrides
broker scenarios execute my_scenario --MY_VAR value --COUNT 5

# With config overrides
broker scenarios execute my_scenario --config.settings.Container.runtime docker

# Run in background
broker scenarios execute my_scenario --background
```

### Get Scenario Information

```bash
broker scenarios info my_scenario

# Without syntax highlighting
broker scenarios info my_scenario --no-syntax
```

### Validate a Scenario

```bash
broker scenarios validate my_scenario
```

Checks syntax and schema validation without executing.

---

## Tips and Best Practices

1. **Always clean up**: Include a `checkin` step at the end of your scenarios, preferably with error handling to ensure cleanup even on failure.

2. **Use meaningful names**: Step names appear in logs and can be referenced via `steps['Step Name']`.

3. **Capture intermediate results**: Use `capture` liberally to store results for debugging and conditional logic.

4. **Test with `--background`**: Long-running scenarios can be run in the background.

5. **Validate before running**: Use `broker scenarios validate` to catch syntax errors early.

6. **Modularize complex workflows**: Split large scenarios into smaller ones and use `run_scenarios` to chain them.

7. **Use variables for flexibility**: Define configurable values in the `variables` section so they can be overridden via CLI.

8. **Handle errors gracefully**: Use `on_error` blocks for critical steps that need cleanup on failure.

---

## Troubleshooting

### Common Issues

**"Undefined variable in template"**
- Check that the variable is defined in `variables` section or captured by a previous step
- Variables are case-sensitive
- Use `broker scenarios info` to see available variables

**"Scenario not found"**
- Ensure file is in `~/.broker/scenarios/` or provide full path
- File must have `.yaml` or `.yml` extension

**"SSH action requires target hosts"**
- Add a `with.hosts` specification to the step
- Ensure a previous `checkout` step has added hosts to `scenario_inventory`

**"Step failed but no on_error defined"**
- Add `on_error: continue` to ignore failures
- Add `exit_on_error: false` to continue without error handling
- Add an `on_error` block with recovery steps

### Debugging

Enable verbose logging:
```bash
broker --log-level debug scenarios execute my_scenario
```

Check the scenario structure:
```bash
broker scenarios info my_scenario
```

Validate without executing:
```bash
broker scenarios validate my_scenario
```
