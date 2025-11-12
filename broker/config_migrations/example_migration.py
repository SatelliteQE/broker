"""Config migrations for versions older than 0.6.1 to 0.6.1.

Copy this file to a new file in the same directory and modify it to create a new migration.
The new file must be named `vX_Y_Z.py` where X_Y_Z is the version you are migrating to.

e.g. cp example_migration.py v0_6_1.py
"""

import logging

logger = logging.getLogger(__name__)

TO_VERSION = "0.6.1"


def example_migration(config_dict):
    """Migrations should modify the config_dict in place and return it."""
    config_dict["example_key"] = "example_value"
    return config_dict


def run_migrations(config_dict):
    """Run all migrations."""
    logger.info(f"Running config migrations for {TO_VERSION}.")
    config_dict = example_migration(config_dict)
    config_dict["_version"] = TO_VERSION
    return config_dict
