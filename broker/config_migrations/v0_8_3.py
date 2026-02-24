"""Config migrations for versions older than 0.8.3 to 0.8.3."""

import logging

logger = logging.getLogger(__name__)

TO_VERSION = "0.8.3"


def add_scenario_import_config(config_dict):
    """Add the SCENARIO_IMPORT config section."""
    if "SCENARIO_IMPORT" not in config_dict:
        logger.debug("Adding SCENARIO_IMPORT section to config.")
        config_dict["SCENARIO_IMPORT"] = {
            "git_hosts": [
                {"url": "https://github.com", "token": "", "type": "github"},
                {"url": "https://gitlab.com", "token": "", "type": "gitlab"},
            ]
        }
    return config_dict


def run_migrations(config_dict):
    """Run all migrations."""
    logger.info(f"Running config migrations for {TO_VERSION}.")
    config_dict = add_scenario_import_config(config_dict)
    config_dict["_version"] = TO_VERSION
    return config_dict
