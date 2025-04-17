"""Broker settings module.

Useful items:
    settings: The settings object.
    init_settings: Function to initialize the settings file.
    validate_settings: Function to validate the settings file.
    INTERACTIVE_MODE: Whether or not Broker is running in interactive mode.
    BROKER_DIRECTORY: The directory where Broker looks for its files.
    settings_path: The path to the settings file.
    inventory_path: The path to the inventory file.
"""

import os
from pathlib import Path

import click
from dynaconf import Dynaconf, Validator
from dynaconf.validator import ValidationError

from broker.config_manager import ConfigManager
from broker.exceptions import ConfigurationError

INTERACTIVE_MODE = ConfigManager.interactive_mode
BROKER_DIRECTORY = Path.home().joinpath(".broker")

if "BROKER_DIRECTORY" in os.environ:
    envar_location = Path(os.environ["BROKER_DIRECTORY"])
    if envar_location.is_dir():
        BROKER_DIRECTORY = envar_location

# ensure the broker directory exists
BROKER_DIRECTORY.mkdir(parents=True, exist_ok=True)

settings_path = BROKER_DIRECTORY.joinpath("broker_settings.yaml")
inventory_path = BROKER_DIRECTORY.joinpath("inventory.yaml")
cfg_manager = ConfigManager(settings_path)


validators = [
    Validator("SSH", is_type_of=dict, default={}),
    Validator("SSH.HOST_USERNAME", default="root"),
    Validator("SSH.HOST_PASSWORD", default="toor"),
    Validator("SSH.HOST_CONNECTION_TIMEOUT", default=60),
    Validator("SSH.HOST_SSH_PORT", default=22),
    Validator("SSH.HOST_SSH_KEY_FILENAME", default=None),
    Validator("SSH.HOST_IPV6", default=False),
    Validator("SSH.HOST_IPV4_FALLBACK", default=True),
    Validator("SSH.BACKEND", default="ssh2-python312"),
    Validator("LOGGING", is_type_of=dict),
    Validator(
        "LOGGING.CONSOLE_LEVEL",
        is_in=["error", "warning", "info", "debug", "trace", "silent"],
        default="info",
    ),
    Validator(
        "LOGGING.FILE_LEVEL",
        is_in=["error", "warning", "info", "debug", "trace", "silent"],
        default="debug",
    ),
    Validator("THREAD_LIMIT", default=None),
    Validator("INVENTORY_FIELDS", is_type_of=dict),
    Validator("INVENTORY_LIST_VARS", is_type_of=str, default="hostname | name"),
    Validator("LESS_COLORS", default=False),
]


def create_settings(config_dict=None, config_file=None, perform_migrations=True):
    """Create a new settings object with custom configuration.

    Args:
        config_dict: Dictionary containing configuration values to overlay onto settings
        config_file: Path to a settings file to use instead of the default
        perform_migrations: Whether to check for and perform migrations

    Returns:
        A dynaconf settings object
    """
    file_path = config_file or settings_path
    file_exists = Path(file_path).exists() if file_path else False

    # Check for migrations if requested and file exists
    if perform_migrations and file_exists:
        if INTERACTIVE_MODE and cfg_manager._get_migrations():
            click.secho(
                "Broker settings file has pending migrations.\n"
                "Continuing without running the migrations may cause errors.",
                fg="red",
            )
            if click.confirm("Would you like to run the migrations now?"):
                cfg_manager.migrate()
            else:
                click.secho("Continuing without running migrations.", fg="yellow")
        elif cfg_manager._get_migrations():
            cfg_manager.migrate()

    # temporary fix for dynaconf #751
    vault_vars = {k: v for k, v in os.environ.items() if "VAULT_" in k}
    for k in vault_vars:
        del os.environ[k]

    # Create settings object
    new_settings = Dynaconf(
        settings_file=str(file_path) if file_exists else None,
        ENVVAR_PREFIX_FOR_DYNACONF="BROKER",
        validators=validators,
    )

    # to make doubly sure, remove the vault loader if set somehow
    new_settings._loaders = [loader for loader in new_settings._loaders if "vault" not in loader]

    # Add any configuration values passed in
    if config_dict:
        for key, value in config_dict.items():
            new_settings[key] = value

    # Validate the logging settings
    try:
        new_settings.validators.validate(only="LOGGING")
    except ValidationError as err:
        if file_exists:
            raise ConfigurationError(f"Configuration error in {file_path}: {err.args[0]}") from err
        # If no file exists, just ensure defaults are applied

    # Restore environment variables
    os.environ.update(vault_vars)

    if not file_exists and not config_dict and not config_file and INTERACTIVE_MODE:
        click.secho(
            "No Broker settings file found. Using default settings.\n"
            "Run 'broker config init' to create a settings file.",
            fg="yellow",
        )

    return new_settings


# Create the global settings object
settings = create_settings()
