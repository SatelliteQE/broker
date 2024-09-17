"""Config migrations for versions older than 0.6.0 to 0.6.0."""
from logzero import logger

TO_VERSION = "0.6.0"


def migrate_instances(config_dict):
    """Migrate instances from a list of dicts to a dict of dicts."""
    logger.debug("Migrating instances from a list to a dict.")
    for key, val in config_dict.items():
        if not isinstance(val, dict):
            continue
        if "instances" in val and isinstance(val["instances"], list):
            old_instances = val.pop("instances")
            val["instances"] = {}
            for inst in old_instances:
                val["instances"].update(inst)
        config_dict[key] = val
    return config_dict


def remove_testprovider(config_dict):
    """Remove the testprovider from the config."""
    logger.debug("Removing the testprovider from the config.")
    config_dict.pop("TestProvider", None)
    return config_dict


def remove_test_nick(config_dict):
    """Remove the test nick from the config."""
    logger.debug("Removing the test nick from the config.")
    nicks = config_dict.get("nicks", {})
    nicks.pop("test_nick", None)
    config_dict["nicks"] = nicks
    return config_dict


def move_ssh_settings(config_dict):
    """Move SSH settings from the top leve into its own chunk."""
    logger.debug("Moving SSH settings into their own section.")
    ssh_settings = {
        "backend": config_dict.pop("ssh_backend", "ssh2-python312"),
        "host_username": config_dict.pop("host_username", "root"),
        "host_password": config_dict.pop("host_password", "toor"),
        "host_ipv6": config_dict.pop("host_ipv6", False),
        "host_ipv4_fallback": config_dict.pop("host_ipv4_fallback", True),
    }
    if ssh_port := config_dict.pop("host_ssh_port", None):
        ssh_settings["ssh_port"] = ssh_port
    if ssh_key := config_dict.pop("host_ssh_key_filename", None):
        ssh_settings["host_ssh_key_filename"] = ssh_key
    config_dict["ssh"] = ssh_settings
    return config_dict


def add_thread_limit(config_dict):
    """Add a thread limit to the config."""
    logger.debug("Adding a thread limit to the config.")
    config_dict["thread_limit"] = None
    return config_dict


def run_migrations(config_dict):
    """Run all migrations."""
    logger.info(f"Running config migrations for {TO_VERSION}.")
    config_dict = migrate_instances(config_dict)
    config_dict = remove_testprovider(config_dict)
    config_dict = remove_test_nick(config_dict)
    config_dict = move_ssh_settings(config_dict)
    config_dict = add_thread_limit(config_dict)
    config_dict["_version"] = TO_VERSION
    return config_dict
