# Expire Time Conversion

Two ways to convert Unix timestamps to human-readable dates in broker inventory:

## Option 1: Standalone Script

Use the `convert_expire_time.py` script to convert timestamps:

```bash
# Convert single timestamp
./scripts/convert_expire_time.py 1781010451

# Convert multiple timestamps
./scripts/convert_expire_time.py 1781010451 1781096907 1785334073

# Interactive mode (paste timestamps, Ctrl+D when done)
./scripts/convert_expire_time.py
```

Example output:
```
1781010451 -> 2026-06-05 14:27:31
```

## Option 2: Integrated Inventory Field (Recommended)

Add `$expire_time_human` to your broker settings to automatically display human-readable dates in inventory.

### Configuration

Edit your broker settings file (`~/.config/broker/broker.yaml` or similar):

```yaml
inventory_fields:
  Host: hostname | name
  Provider: _broker_provider
  Action: $action
  OS: os_distribution os_distribution_version
  Description: description
  SAT version: _broker_args.satellite_version | Unknown
  snap: _broker_args.snap | Unknown
  expire_time: $expire_time_human  # Add this line
```

### Example

With this configuration, running `broker inventory` will show:

```
┏━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃ Id ┃ Host                     ┃ Provider     ┃ Action         ┃ OS          ┃ expire_time       ┃
┡━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│ 0  │ ip-10-0-168-49...        │ AnsibleTower │ deploy-rhel    │ RedHat 10.1 │ 2026-06-05 14:27  │
│ 1  │ ip-10-0-198-85...        │ AnsibleTower │ deploy-rhel    │ RedHat 10.1 │ 2026-06-06 14:28  │
└────┴──────────────────────────┴──────────────┴────────────────┴─────────────┴───────────────────┘
```

### Note

The special field `$expire_time_human` automatically converts the `expire_time` Unix timestamp field to a readable format: `YYYY-MM-DD HH:MM`.
