# Broker Workflows & Examples

Real-world CLI patterns and scenario templates.

## CLI Workflows

### Checkout with explicit workflow args (preferred)
```bash
# AnsibleTower/AAP
broker checkout --workflow deploy-rhel --deploy_rhel_version 9.7
broker checkout --workflow deploy-satellite --deploy_sat_version 6.19
broker checkout --workflow deploy-rhel --deploy_rhel_version 9.7 --count 3

# Container
broker checkout --container-host ubi9:latest
broker checkout --container-host quay.io/fedora/fedora:40
broker checkout --container-host ubi8:latest --count 5

# Beaker (requires job XML)
broker checkout --job_xml tests/data/beaker/test_job.xml
```

### Checkout by nick (reusable shorthand, when explicitly desired)
```bash
# First, define a nick in broker_settings.yaml:
# nicks:
#   rhel9:
#     workflow: deploy-rhel
#     deploy_rhel_version: 9.4

broker checkout --nick rhel9
broker checkout --nick sat619
```

### Discover available resources
```bash
# AnsibleTower: find RHEL 9 templates (best way to determine latest version)
broker providers AnsibleTower --templates --results-filter '"rhel-9" in @res'

# AnsibleTower: filter template list for any substring
broker providers AnsibleTower --templates --results-filter '"satellite" in @res'

# AnsibleTower: list all workflows
broker providers AnsibleTower --workflows

# AnsibleTower: filter workflow list
broker providers AnsibleTower --workflows --results-filter '"deploy-rhel" in @res'

# AnsibleTower: see args for a specific workflow
broker providers AnsibleTower --workflow deploy-rhel

# Container: list available images
broker providers Container --container-hosts
```

### Pass complex args from file
```bash
# JSON or YAML file; top-level keys become Broker args
broker checkout --workflow deploy-rhel --args-file broker_args.json

# Pass a file as the value of a specific arg (contents become arg value)
broker checkout --nick rhel9 --extra tests/data/args_file.yaml
```

### Inventory management
```bash
broker inventory                                      # list local inventory
broker inventory --details                            # full host details for filtering
broker inventory --list                               # compact hostnames-only
broker inventory --sync AnsibleTower                 # sync from AnsibleTower
broker inventory --sync Container                    # sync all container instances
broker inventory --sync Container::remote-docker     # sync one instance
```

### Filtering inventory
```bash
# Shell-safe: always wrap filter expressions in single quotes
# the @inv object has the inventory host's nested structure loaded as attributes
broker inventory --filter '"test" in @inv.hostname'
broker inventory --filter '@inv._broker_args.deploy_rhel_version == "9.4"'
broker inventory --filter '@inv._broker_args.template.startswith("deploy-sat")'
# the @inv object can also be treated like a python list
broker inventory --filter '@inv[-1]'         # last entry
broker inventory --filter '@inv[3:7]'        # slice

# Chain filters with |
broker inventory --filter '"rhel" in @inv.hostname | @inv._broker_provider == "AnsibleTower"'
```

### Checkin patterns
```bash
broker checkin 0                                # by local inventory index
broker checkin my.satellite.example.com
broker checkin 1 3 my.satellite.example.com    # multiple at once
broker checkin --all
broker checkin --all --filter '"test" in @inv.name'
broker checkin -b --all                         # background
```

### Extend lease
```bash
broker extend 0                                # by inventory index
broker extend my.satellite.example.com
broker extend --all
broker extend --all --filter '"rhel9" in @inv.name'
broker extend --sequential --all               # one at a time
```

### Execute provider actions (non-provisioning)
```bash
# Reboot a VM
broker execute --workflow vm-power-operation --vm_operation reboot --source_vm my.satellite.example.com

# With raw output
broker execute -o raw --workflow my-awesome-workflow --artifacts last

# In background
broker execute -b --workflow my-awesome-workflow
```

### Machine-readable output (CI/CD)
```bash
broker --output-file output.json checkout --nick rhel9
broker --output-file inventory.json inventory
# Data is written live to the file as operations complete
```

### Background mode
```bash
# Avoid background mode unless non-blocking execution is explicitly needed.
# Foreground is strongly preferred for ordinary checkout — it prints the hostname directly.
broker checkout --background --workflow deploy-rhel --deploy_rhel_version 9.7  # only if explicitly needed
broker checkin -b --all
# Follow progress via the log file when running in background:
tail -f ~/.broker/broker.log
```

### Non-default provider instance
```bash
# Flag must match the provider class name exactly (case-sensitive)
broker checkout --workflow deploy-rhel --deploy_rhel_version 9.7 --AnsibleTower staging
broker checkout --container-host ubi9:latest --Container remote-docker
```

---

## Scenario Examples

### Basic: checkout → SSH → checkin
```yaml
description: Provision a RHEL host, verify OS version, then release.

variables:
  RHEL_VERSION: "9.4"

steps:
  - name: Provision RHEL host
    action: checkout
    arguments:
      workflow: deploy-rhel
      deploy_rhel_version: "{{ RHEL_VERSION }}"

  - name: Verify OS
    action: ssh
    arguments:
      command: "cat /etc/os-release"
    with:
      hosts: scenario_inventory
    capture:
      as: os_info

  - name: Print result
    action: output
    arguments:
      content: "{{ os_info }}"
      destination: stdout

  - name: Release host
    action: checkin
    with:
      hosts: scenario_inventory
```

Run it:
```bash
broker scenarios execute verify-rhel-os
broker scenarios execute verify-rhel-os --RHEL_VERSION 8.10
```

### Container: discover images, deploy all, checkin
```yaml
description: Deploy one container per available image, then check them all in.

steps:
  - name: Discover container hosts
    action: provider_info
    arguments:
      provider: Container
      query: container_hosts
    capture:
      as: container_hosts

  - name: Deploy each container host
    action: checkout
    arguments:
      container_host: "{{ c_host }}"
    loop:
      iterable: container_hosts
      iter_var: c_host

  - name: Checkin all deployed containers
    action: checkin
    with:
      hosts: scenario_inventory
```

### AnsibleTower: list and filter workflows
```yaml
description: Fetch all deploy-* workflow details from AnsibleTower and save to JSON.

steps:
  - name: List all workflows
    action: provider_info
    arguments:
      provider: AnsibleTower
      query: workflows
    capture:
      as: workflow_list

  - name: Get details for deploy workflows
    action: provider_info
    arguments:
      provider: AnsibleTower
      query:
        workflow: "{{ workflow_name }}"
    when: "'deploy' in workflow_name"
    loop:
      iterable: workflow_list
      iter_var: workflow_name
    capture:
      as: workflow_details
      key: workflow_name

  - name: Save to JSON
    action: output
    arguments:
      content: workflow_details
      destination: deploy_workflows.json
```

### Loop with variable override + on_error recovery
```yaml
description: Run a command suite on a provisioned host; ensure checkin happens on error.

variables:
  RHEL_VERSION: "9.4"   # override: broker scenarios execute this-scenario --RHEL_VERSION 8.10
  COMMANDS:
    - "hostname -f"
    - "uptime"
    - "cat /etc/os-release"

steps:
  - name: Checkout host
    action: checkout
    arguments:
      workflow: deploy-rhel
      deploy_rhel_version: "{{ RHEL_VERSION }}"
    on_error:
      - name: Cleanup on checkout failure
        action: checkin
        with:
          hosts: scenario_inventory

  - name: Run commands
    action: ssh
    loop:
      iterable: COMMANDS
      iter_var: cmd
      on_error: continue
    arguments:
      command: "{{ cmd }}"
    with:
      hosts: scenario_inventory
    capture:
      as: cmd_results

  - name: Final checkin
    action: checkin
    with:
      hosts: scenario_inventory
```

Run it:
```bash
broker scenarios validate this-scenario          # check schema first
broker scenarios execute this-scenario
broker scenarios execute this-scenario --RHEL_VERSION 8.10  # override scenario-level variables
```
