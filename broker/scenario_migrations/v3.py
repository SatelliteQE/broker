"""Scenario migration to version 3.

This migration adds the _spec_ver field for version 3 scenarios.
Version 3 adds:
- local_exec action for executing commands on the local machine

Note: This migration doesn't modify any existing content - it only updates
the _spec_ver field. All new features are optional and backward compatible.
"""

# Target version this migration upgrades to
TO_VERSION = 3


def run_migrations(scenario_dict):
    """Run all migrations to upgrade a scenario to version 3.

    Args:
        scenario_dict: The parsed scenario dictionary to migrate

    Returns:
        The modified scenario dictionary with _spec_ver set to 3
    """
    # Simply update the version field - no other changes needed
    # All v3 features are optional additions
    scenario_dict["_spec_ver"] = TO_VERSION

    return scenario_dict
