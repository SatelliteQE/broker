"""Dictionary manipulation utilities."""

from collections.abc import MutableMapping
from copy import deepcopy


def clean_dict(in_dict):
    """Remove entries from a dict where value is None."""
    return {k: v for k, v in in_dict.items() if v is not None}


def merge_dicts(dict1, dict2):
    """Merge two nested dictionaries together.

    :return: merged dictionary
    """
    if not isinstance(dict1, MutableMapping) or not isinstance(dict2, MutableMapping):
        return dict1
    dict1 = clean_dict(dict1)
    dict2 = clean_dict(dict2)
    merged = {}
    dupe_keys = dict1.keys() & dict2.keys()
    for key in dupe_keys:
        merged[key] = merge_dicts(dict1[key], dict2[key])
    for key in dict1.keys() - dupe_keys:
        merged[key] = deepcopy(dict1[key])
    for key in dict2.keys() - dupe_keys:
        merged[key] = deepcopy(dict2[key])
    return merged


def flatten_dict(nested_dict, parent_key="", separator="_"):
    """Flatten a nested dictionary, keeping nested notation in key.

    {
        'key': 'value1',
        'another': {
            'nested': 'value2',
            'nested2': [1, 2, {'deep': 'value3'}]
        }
    }
    becomes
    {
        "key": "value",
        "another_nested": "value2",
        "another_nested2": [1, 2],
        "another_nested2_deep": "value3"
    }
    note that dictionaries nested in lists will be removed from the list.

    :return: dictionary
    """
    flattened = []
    for key, value in nested_dict.items():
        new_key = f"{parent_key}{separator}{key}" if parent_key else key
        if isinstance(value, dict):
            flattened.extend(flatten_dict(value, new_key, separator).items())
        elif isinstance(value, list):
            to_remove = []
            # avoid mutating nested structures
            value = value.copy()  # noqa: PLW2901
            for index, val in enumerate(value):
                if isinstance(val, dict):
                    flattened.extend(flatten_dict(val, new_key, separator).items())
                    to_remove.append(index)
            for index in to_remove[::-1]:  # remove from back to front
                del value[index]
            flattened.append((new_key, value))
        else:
            flattened.append((new_key, value))
    return dict(flattened)


def dict_from_paths(source_dict, paths, sep="/"):
    """Given a dictionary of desired keys and nested paths, return a new dictionary.

    Example:
        source_dict = {
            "key1": "value1",
            "key2": {
                "nested1": "value2",
                "nested2": {
                    "deep": "value3"
                }
            }
        }
        paths = {
            "key1": "key1",
            "key2": "key2/nested2/deep"
        }
        returns {
            "key1": "value1",
            "key2": "value3"
        }
    """
    result = {}
    for key, path in paths.items():
        if sep not in path:
            result[key] = source_dict.get(path)
        else:
            top, rem = path.split(sep, 1)
            result.update(dict_from_paths(source_dict[top], {key: rem}))
    return result
