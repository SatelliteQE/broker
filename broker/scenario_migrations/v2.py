"""Scenario migration to version 2.

This migration adds the _spec_ver field to scenarios that don't have it,
setting them to version 2 to indicate they support the new features:
- Retry support
- Step-level timeouts
- Assert action
- Arbitrary metadata with filtering
- Flexible time format (e.g., "5m", "2h")

Note: This migration doesn't modify any existing content - it only adds
the _spec_ver field. All new features are optional and backward compatible.
"""

# Target version this migration upgrades to
TO_VERSION = 2


def run_migrations(scenario_dict):
    """Run all migrations to upgrade a scenario to version 2.

    Args:
        scenario_dict: The parsed scenario dictionary to migrate

    Returns:
        The modified scenario dictionary with _spec_ver set to 2
    """
    # Simply add the version field - no other changes needed
    # All v2 features are optional additions
    scenario_dict["_spec_ver"] = TO_VERSION

    return scenario_dict
