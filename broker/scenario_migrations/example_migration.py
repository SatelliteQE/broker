"""Example scenario migration module.

This is a template for creating new scenario migrations.
Copy this file, rename it to vX.py (e.g., v2.py), and implement the run_migrations function.

Each migration module must have:
1. TO_VERSION constant - integer target version
2. run_migrations(scenario_dict) function - modifies scenario_dict in place and returns it
"""

# Target version this migration upgrades to
TO_VERSION = 2


def run_migrations(scenario_dict):
    """Run all migrations to upgrade a scenario to TO_VERSION.

    Args:
        scenario_dict: The parsed scenario dictionary to migrate

    Returns:
        The modified scenario dictionary with _spec_ver set to TO_VERSION
    """
    # Example transformation functions would go here
    # scenario_dict = add_new_field(scenario_dict)
    # scenario_dict = rename_old_field(scenario_dict)

    # Always set the _spec_ver at the end
    scenario_dict["_spec_ver"] = TO_VERSION

    return scenario_dict


# Example helper function for a specific transformation
def add_new_field(scenario_dict):
    """Add a new field if it doesn't exist."""
    if "new_field" not in scenario_dict:
        scenario_dict["new_field"] = "default_value"
    return scenario_dict
