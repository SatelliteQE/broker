"""Config migrations for versions older than 0.6.12 to 0.6.12."""

from logzero import logger

TO_VERSION = "0.6.12"


def add_max_resilient_wait_setting(config_dict):
    """Add the max_resilient_wait setting to AnsibleTower provider."""
    logger.debug("Adding max_resilient_wait setting to AnsibleTower provider.")
    ansible_tower = config_dict.get("AnsibleTower", {})
    if "max_resilient_wait" not in ansible_tower:
        ansible_tower["max_resilient_wait"] = 7200  # 2 hours default
    config_dict["AnsibleTower"] = ansible_tower
    return config_dict


def run_migrations(config_dict):
    """Run all migrations."""
    logger.info(f"Running config migrations for {TO_VERSION}.")
    config_dict = add_max_resilient_wait_setting(config_dict)
    config_dict["_version"] = TO_VERSION
    return config_dict
