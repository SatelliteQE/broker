"""Config migrations for versions older than 0.8.10 to 0.8.10."""

import logging

logger = logging.getLogger(__name__)

TO_VERSION = "0.8.10"


def add_version_snap_inventory_fields(config_dict):
    """Add Version and Snap to inventory_fields if not already present."""
    inv_fields = config_dict.get("inventory_fields", {})
    if "Version" not in inv_fields:
        logger.debug("Adding Version to inventory_fields.")
        inv_fields["Version"] = "_broker_args.deploy_sat_version"
    if "Snap" not in inv_fields:
        logger.debug("Adding Snap to inventory_fields.")
        inv_fields["Snap"] = "_broker_args.deploy_snap_version"
    config_dict["inventory_fields"] = inv_fields
    return config_dict


def run_migrations(config_dict):
    """Run all migrations."""
    logger.info(f"Running config migrations for {TO_VERSION}.")
    config_dict = add_version_snap_inventory_fields(config_dict)
    config_dict["_version"] = TO_VERSION
    return config_dict
