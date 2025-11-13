"""Config migrations for versions older than 0.6.9 to 0.6.9."""

import logging

logger = logging.getLogger(__name__)

TO_VERSION = "0.6.9"


def add_aap_version_setting(config_dict):
    """Add the AAP_VERSION setting to AnsibleTower provider."""
    logger.debug("Adding aap_version setting to AnsibleTower provider.")
    ansible_tower = config_dict.get("AnsibleTower", {})
    if "aap_version" not in ansible_tower:
        ansible_tower["aap_version"] = None
    config_dict["AnsibleTower"] = ansible_tower
    return config_dict


def run_migrations(config_dict):
    """Run all migrations."""
    logger.info(f"Running config migrations for {TO_VERSION}.")
    config_dict = add_aap_version_setting(config_dict)
    config_dict["_version"] = TO_VERSION
    return config_dict
