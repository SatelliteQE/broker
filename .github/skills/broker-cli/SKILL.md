---
name: broker
description: 'Operate the Broker CLI to provision, manage, and release lab infrastructure (VMs and containers). Use when: checking out hosts, checking in hosts, viewing inventory, extending leases, running provider actions, writing scenarios, configuring broker_settings.yaml, or troubleshooting Broker commands. Covers checkout, checkin, inventory, extend, execute, providers, config, and scenarios subcommands.'
---

# Broker Skill

## When to Use

Load this skill when the user asks about:
- Acquiring or releasing VMs/containers via Broker
- The `broker checkout`, `checkin`, `inventory`, `extend`, `execute`, `providers`, `config`, or `scenarios` commands
- Writing or debugging Broker scenario YAML files
- Configuring `broker_settings.yaml`, nicks, or provider instances
- Filtering Broker inventory or provider results

## Operating Methodology

1. **Clarify intent**: determine provider, resource count, OS/workflow, desired duration, whether a nick exists, and whether the user wants guidance or direct execution.
2. **Map to Broker primitives**: translate the request to the correct CLI command and arguments.
3. **Check configuration assumptions**: inspect nicks, provider instances, inventory state, or config chunks before assuming anything.
4. **Assess impact**: state what will be acquired, modified, extended, or released.
5. **Execute carefully**: capture stdout and stderr; surface warnings and partial failures.
6. **Validate outcome**: confirm the resource state matches the requested action.

## Command Quick Reference

| Goal | Command |
|---|---|
| Provision a host | `broker checkout --nick <name>` or `broker checkout --workflow <name>` |
| Check inventory | `broker inventory` |
| Return a host | `broker checkin <id|hostname>` or `broker checkin --all` |
| Extend a lease | `broker extend <id|hostname|--all>` |
| Run a provider action | `broker execute --workflow <name>` |
| Get a list of supported providers | `broker providers --help` |
| Inspect provider options | `broker providers AnsibleTower --help` or `broker providers AnsibleTower --workflows` |
| Run a scenario | `broker scenarios execute <name>` |
| View/edit config | `broker config view`, `broker config set` |

## Key Decision Points

**Which identifier to use with checkin/extend?**
- Local inventory ID (integer from `broker inventory`) — fastest
- Hostname — works when you know the FQDN
- `--all` with optional `--filter` — for bulk operations; always confirm scope first
- Get all information about hosts with `broker inventory --details` and use the FQDN or other unique field as a filter

**Does the user need a non-default provider instance?**  
Pass a flag matching the provider class name exactly (case-sensitive): `--AnsibleTower testing`

**Does the command need complex arguments?**  
Use `--args-file <json|yaml>` — top-level keys become Broker args; explicit CLI args take precedence.

**Should the user run in the background?**  
Only for `checkout`, `checkin`, and `execute`. Background mode suppresses stderr — direct the user to the Broker log file for progress. Do not suggest it when they need immediate stdout output.

**Is the user writing a scenario?**  
Always include a `checkin` step in an `on_error` recovery block so hosts are released on failure. Validate first with `broker scenarios validate <name>`.

## Behavioral Boundaries

- **Before checkout**: state what will be acquired, from which provider/instance, and any `--count` multiplier.
- **Before checkin**: enumerate the target hosts or IDs; require explicit confirmation for `--all` and filtered bulk checkins.
- **Before bulk/filtered actions**: restate the filter and scope in plain language.
- **When unsure**: run `broker --help` or `broker <command> --help` rather than guessing syntax.

## Common Pitfalls

1. Provider instance flag casing must match exactly: `--AnsibleTower testing`, not `--ansibletower testing`.
2. Never assume a nick exists — check with `broker config nicks` first.
3. CLI args always override `--args-file` values.
4. Wrap filter expressions in single quotes to prevent shell expansion.
5. `checkin` is irreversible for containers — they are fully deleted.
6. Inventory filters can only reference fields present in the local `inventory.yaml` (`broker inventory --details`).
7. Environment variables override instance-level config by default.

## Response Format

Structure answers as:
1. **What it does** — one concise sentence.
2. **Exact command** — the full CLI invocation.
3. **Impact** — resources or settings affected.
4. **Risk checkpoint** — explicit confirmation step for checkout, checkin, or bulk operations.
5. **Execution result** — when run, show stdout/stderr and call out warnings.
6. **Final state** — confirm what Broker now manages or has released.

## Reference

For full command syntax, scenario YAML structure, configuration options, and provider-specific details, see the broker-agent agent at [`.github/agents/broker-agent.agent.md`](../../agents/broker-agent.agent.md).
