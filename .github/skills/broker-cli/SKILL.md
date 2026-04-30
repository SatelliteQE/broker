---
name: broker-cli
description: 'Operate the Broker CLI to provision, manage, and release lab infrastructure (VMs and containers). Use when: checking out hosts, checking in hosts, viewing inventory, extending leases, running provider actions, writing scenarios, configuring broker_settings.yaml, or troubleshooting Broker commands. Covers checkout, checkin, inventory, extend, execute, providers, config, and scenarios subcommands.'
---

# Broker CLI Skill

## What This Skill Does

Broker is an abstraction layer that provides a common interface for provisioning and managing virtual machines and containers across multiple backend providers. It handles the full lifecycle: checkout → use → checkin.

**Providers**: AnsibleTower (Satlab), Beaker, Container (Podman/Docker), Foreman, OpenStack

## Installation

```bash
# Recommended (includes AnsibleTower/AAP, podman, and hussh SSH support)
uv tool install 'broker[satlab]'

# Minimal install (no provider-specific extras)
uv tool install broker

# With individual extras
uv tool install 'broker[ansibletower]'   # AnsibleTower/AAP only
uv tool install 'broker[podman]'         # Container (Podman) only
uv tool install 'broker[docker]'         # Container (Docker) only
uv tool install 'broker[beaker]'         # Beaker (also: dnf install krb5-devel)
uv tool install 'broker[openstack]'      # OpenStack
uv tool install 'broker[shell]'          # Include the interactive shell dependencies 
```

First run creates `~/.broker/broker_settings.yaml` interactively. Override location:
```bash
export BROKER_DIRECTORY=/path/to/broker/dir
```

The config file can also be initialized manually with the `broker config init` command.

Always check for updates if some problem arises, but no more than once per session: `broker --version` reports the current version and notifies if a newer release is available.

## Core CLI Commands

### Checkout (provision a host)
```bash
broker checkout --workflow deploy-rhel --deploy_rhel_version 9.7     # explicit workflow args (preferred)
broker checkout --workflow deploy-satellite --deploy_sat_version 6.19
broker checkout --workflow deploy-rhel --deploy_rhel_version 9.7 --count 3  # multiple hosts
broker checkout --container-host ubi9:latest                          # container
broker checkout --workflow deploy-rhel --args-file broker_args.json   # complex args
broker checkout --nick rhel9                                          # nick shorthand, when explicitly desired
```

### Checkin (return/delete a host)
```bash
broker checkin my.satellite.example.com
broker checkin 0                             # local inventory ID
broker checkin 1 3 my.satellite.example.com  # multiple at once
broker checkin --all
broker checkin --all --filter '"satellite" in @inv.name'  # checks in all hosts with "satellite" in the name
broker checkin -b --all                      # background mode
```

### Inventory
```bash
broker inventory                                 # list local inventory
broker inventory --details                       # full host details (useful for filters)
broker inventory --list                          # compact hostnames-only view
broker inventory --sync AnsibleTower             # sync from provider
broker inventory --sync Container::remote-docker # sync specific instance
```

### Extend (lease time)
```bash
broker extend 0                                   # by inventory ID
broker extend my.satellite.example.com
broker extend --all
broker extend --all --filter '"rhel9" in @inv.name'
broker extend --sequential --all
```

### Execute (arbitrary provider actions)
```bash
broker execute --workflow vm-power-operation --vm_operation reboot --source_vm my.host.example.com
broker execute -o raw --workflow my-awesome-workflow --artifacts last
broker execute -b --workflow my-awesome-workflow  # background
```

### Providers (introspection)
```bash
broker providers --help
broker providers AnsibleTower --help
broker providers AnsibleTower --templates --results-filter '"rhel-9" in @res'  # find latest (rhel 9) templates
broker providers AnsibleTower --templates
broker providers AnsibleTower --workflows
broker providers AnsibleTower --workflow deploy-rhel
broker providers Container --container-hosts
```

### Common discovery + checkout flow (AnsibleTower)
```bash
# When you need the latest version of a given OS family, rhel 9 in this example:
broker providers AnsibleTower --templates --results-filter '"rhel-9" in @res'
broker checkout --workflow deploy-rhel --deploy_rhel_version 9.7
```

### Output and Background
```bash
# Machine-readable JSON output file
broker --output-file output.json checkout --workflow deploy-rhel --deploy_rhel_version 9.7
broker --output-file inventory.json inventory

# Background mode — avoid unless explicitly needed; foreground checkout prints the hostname directly
broker checkout --background --workflow deploy-rhel --deploy_rhel_version 9.7
broker checkin -b --all
```

## Configuration

See [references/configuration.md](./references/configuration.md) for full config reference including inventory fields, nicks, SSH settings, and the `broker config` command group.

Environment variable overrides use Dynaconf's double-underscore nesting:
```bash
BROKER_DIRECTORY=/home/user/broker/ broker inventory
BROKER_AnsibleTower__base_url="https://my-tower.example.com" broker checkout --nick rhel9
```

## Providers

See [references/providers.md](./references/providers.md) for full per-provider config and installation details.

| Provider | Extra Required | Notes |
|----------|---------------|-------|
| AnsibleTower | `broker[satlab]` or `broker[ansibletower]` | Token preferred over password |
| Container | `broker[podman]` or `broker[docker]` | Containers are fully deleted on checkin |
| Foreman | none | Supports multi-instance |
| Beaker | `broker[beaker]` + `dnf install krb5-devel` | Kerberos auth |
| OpenStack | `broker[openstack]` | See broker_settings.yaml.example |

## Scenarios

Scenarios chain multiple Broker actions in a YAML file with Jinja2 templating, looping, and error handling.

See [references/scenarios.md](./references/scenarios.md) for scenario syntax and examples.

```bash
broker scenarios list                                    # list available scenarios
broker scenarios execute my-scenario                     # run by name or path
broker scenarios execute my-scenario --RHEL_VERSION 9.4  # pass variables
broker scenarios info my-scenario                        # inspect without running
broker scenarios validate my-scenario                    # schema check only
```

See [references/workflows.md](./references/workflows.md) for complete CLI patterns and scenario examples.

## Operating Methodology

1. **Clarify intent first**: determine provider, resource count, OS or workflow, and whether the user wants explanation only or real execution.
2. **Prefer the direct path**: for one-off provisioning, use explicit workflow arguments. Do not look up nicks or inspect config unless the user explicitly asks to use a nick or reusable shorthand.
3. **Use provider discovery when needed**: query templates (`--templates --results-filter`) or workflows only when the requested version or workflow name is unknown.
4. **Check configuration assumptions only when relevant**: inspect nicks, provider instances, or inventory state only if the chosen command depends on them.
5. **Execute carefully**: prefer foreground execution so the resulting hostname and output are immediately visible. Capture stdout and stderr and surface warnings.
6. **Validate the outcome**: confirm the resource state matches the requested action.

## Key Decision Points

**Which identifier to use with checkin/extend?**
- Local inventory ID (integer from `broker inventory`) — fastest
- Hostname — works when you know the FQDN
- `--all` with optional `--filter` — for bulk operations; always confirm scope first
- Use `broker inventory --details` to discover filterable fields

**Does the user need a non-default provider instance?**
Pass a flag matching the provider class name exactly (case-sensitive): `--AnsibleTower testing`

**Does the command need complex arguments?**
Use `--args-file <json|yaml>` — top-level keys become Broker args; explicit CLI args take precedence.

**Should the user run in the background?**
Avoid background mode by default. Use it only when the user explicitly asks for non-blocking execution. For ordinary checkout requests, foreground execution is strongly preferred — it prints the hostname and workflow output directly to the terminal. Background mode suppresses this output; the user must tail the log file to see progress.

**Is the user writing a scenario?**
Always include a `checkin` step or an `on_error` recovery block so hosts are released on failure. Validate first with `broker scenarios validate <name>`.

## Behavioral Boundaries

- **Before checkout**: state what will be acquired, from which provider/instance, and any `--count` multiplier.
- **Before checkin**: enumerate the target hosts or IDs; require explicit confirmation for `--all` and filtered bulk checkins.
- **Before bulk/filtered actions**: restate the filter and scope in plain language.
- **When unsure**: run `broker --help` or `broker <command> --help` rather than guessing syntax.
- **For first-time users**: explain commands step-by-step, including provider-instance selection, inventory IDs, and filter quoting.

## Common Pitfalls

1. AnsibleTower/AAP requires `broker[satlab]` or `broker[ansibletower]` — it is **not** included in the base `broker` install.
2. Provider instance flag casing must match exactly: `--AnsibleTower testing`, not `--ansibletower testing`.
3. Never assume a nick exists — check with `broker config nicks` first. For one-off provisioning, prefer explicit workflow args over nick discovery.
4. CLI args always override `--args-file` values.
5. Wrap filter expressions in single quotes to prevent shell expansion: `'@inv.hostname == "host.example.com"'`
6. `checkin` is irreversible for containers — they are fully deleted, not just stopped.
7. Inventory filters can only reference fields present in the local `inventory.yaml` (`broker inventory --details` shows available fields).
8. Environment variables override instance-level config by default (unless the instance enables `override_envars: True`).
9. In scenarios, forgetting a `checkin` step (or `on_error` recovery) can leave hosts running indefinitely.
10. `broker scenarios execute` is the correct subcommand — `broker scenarios run` does not exist.

## Response Format

Structure answers as:
1. **What it does** — one concise sentence.
2. **Exact command** — the full CLI invocation.
3. **Impact** — resources or settings affected.
4. **Risk checkpoint** — explicit confirmation step for checkout, checkin, or bulk operations.
5. **Execution result** — when run, show stdout/stderr and call out warnings.
6. **Final state** — confirm what Broker now manages or has released.
