"""Config migrations for versions older than 0.6.3 to 0.6.3."""

import logging

logger = logging.getLogger(__name__)

TO_VERSION = "0.6.3"


def add_dangling_behavior(config_dict):
    """Add the dangling_behavior config to AnsibleTower."""
    if "AnsibleTower" in config_dict:
        if "dangling_behavior" not in config_dict["AnsibleTower"]:
            logger.debug("Adding dangling_behavior to AnsibleTower.")
            config_dict["AnsibleTower"]["dangling_behavior"] = "checkin"
    return config_dict


def run_migrations(config_dict):
    """Run all migrations."""
    logger.info(f"Running config migrations for {TO_VERSION}.")
    config_dict = add_dangling_behavior(config_dict)
    config_dict["_version"] = TO_VERSION
    return config_dict
