"""Inventory management utilities."""

import threading

from ruamel.yaml import YAML

from broker.helpers.dict_utils import dict_from_paths, merge_dicts
from broker.helpers.file_utils import load_file

yaml = YAML()
yaml.default_flow_style = False
yaml.sort_keys = False

INVENTORY_LOCK = threading.Lock()
SPECIAL_INVENTORY_FIELDS = {}  # use the _special_inventory_field decorator to add new fields


def _special_inventory_field(action_name):
    """Register inventory field actions."""

    def decorator(func):
        SPECIAL_INVENTORY_FIELDS[action_name] = func
        return func

    return decorator


def load_inventory(filter=None):
    """Load all local hosts in inventory.

    :param filter: A filter string to apply to the inventory.

    :return: list of dictionaries
    """
    from broker.helpers.results import eval_filter
    from broker.settings import inventory_path

    inv_data = load_file(inventory_path, warn=False)
    if inv_data and filter:
        inv_data = eval_filter(inv_data, filter)
    return inv_data or []


def update_inventory(add=None, remove=None):
    """Update list of local hosts in the checkout interface.

    :param add: list of dictionaries representing new hosts
    :param remove: list of strings representing hostnames or names to be removed

    :return: no return value
    """
    from broker.settings import inventory_path

    if add and not isinstance(add, list):
        add = [add]
    elif not add:
        add = []
    if remove and not isinstance(remove, list):
        remove = [remove]
    with INVENTORY_LOCK:
        inv_data = load_inventory()
        if inv_data:
            inventory_path.unlink()

        if remove:
            for host in inv_data[::-1]:
                if host["hostname"] in remove or host.get("name") in remove:
                    # iterate through new hosts and update with old host data if it would nullify
                    for new_host in add:
                        if host["hostname"] == new_host["hostname"] or host.get(
                            "name"
                        ) == new_host.get("name"):
                            # update missing data in the new_host with the old_host data
                            # new_host values take precedence over old host data
                            new_host.update(merge_dicts(host, new_host))
                    inv_data.remove(host)
        if add:
            inv_data.extend(add)

        inventory_path.touch()
        yaml.dump(inv_data, inventory_path)


def flip_provider_actions(provider_actions):
    """Flip the mapping of actions->provider to provider->actions."""
    flipped = {}
    for action, (provider, _) in provider_actions.items():
        provider_name = provider.__name__
        if provider_name not in flipped:
            flipped[provider_name] = []
        flipped[provider_name].append(action)
    return flipped


def inventory_fields_to_dict(inventory_fields, host_dict, **extras):
    """Convert a dicionary-like representation of inventory fields to a resolved dictionary.

    inventory fields, as set in the config look like this, in yaml:
    inventory_fields:
        Host: hostname | name
        Provider: _broker_provider
        Action: $action
        OS: os_distribution os_distribution_version

    We then process that into a dictionary with inventory values like this:
    {
        "Host": "some.test.host",
        "Provider": "AnsibleTower",
        "Action": "deploy-rhel",
        "OS": "RHEL 8.4"
    }

    Notes: The special syntax use in Host and Action fields <$action> is a special keyword that
    represents a more complex field resolved by Broker.
    Also, the Host field represents a priority  order of single values,
    so if hostname is not present, name will be used.
    Finally, spaces between values are preserved. This lets us combine multiple values in a single field.
    """
    return {
        name: _resolve_inv_field(field, host_dict, **extras)
        for name, field in inventory_fields.items()
    }


def _resolve_inv_field(field, host_dict, **extras):
    """Real functionality for inventory_fields_to_dict, allows recursive evaluation."""
    # Users can specify multiple values to try in order of priority, so evaluate each
    if "|" in field:
        resolved = [_resolve_inv_field(f.strip(), host_dict, **extras) for f in field.split("|")]
        for val in resolved:
            if val and val != "Unknown":
                return val
        return "Unknown"
    # Users can combine multiple values in a single field, so evaluate each
    if " " in field:
        return " ".join(_resolve_inv_field(f, host_dict, **extras) for f in field.split())
    # Some field values require special handling beyond what the existing syntax allows
    if special_field_func := SPECIAL_INVENTORY_FIELDS.get(field):
        return special_field_func(host_dict, **extras)
    # Otherwise, try to get the value from the host dictionary
    return dict_from_paths(host_dict, {"_": field}, sep=".")["_"] or "Unknown"


@_special_inventory_field("$action")
def get_host_action(host_dict, provider_actions=None, **_):
    """Get a more focused set of fields from the host inventory."""
    if not provider_actions:
        return "$actionError"
    # Flip the mapping of actions->provider to provider->actions
    flipped_actions = {}
    for action, (provider, _) in provider_actions.items():
        provider_name = provider.__name__
        if provider_name not in flipped_actions:
            flipped_actions[provider_name] = []
        flipped_actions[provider_name].append(action)
    # Get the host's action, based on its provider
    provider = host_dict["_broker_provider"]
    for opt in flipped_actions[provider]:
        if action := host_dict["_broker_args"].get(opt):
            return action
    return "Unknown"
