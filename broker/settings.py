"""Broker settings module.

Useful items:
    settings: The settings object.
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
from broker.helpers import merge_dicts

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


BASE_VALIDATORS = [
    Validator("SSH", is_type_of=dict, default={}),
    Validator("SSH.HOST_USERNAME", default="root"),
    Validator("SSH.HOST_PASSWORD", default="toor"),
    Validator("SSH.HOST_CONNECTION_TIMEOUT", default=60),
    Validator("SSH.HOST_SSH_PORT", default=22),
    Validator("SSH.HOST_SSH_KEY_FILENAME", default=None),
    Validator("SSH.HOST_IPV6", default=False),
    Validator("SSH.HOST_IPV4_FALLBACK", default=True),
    Validator("SSH.BACKEND", default="hussh"),
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
    Validator("LOGGING.LOG_PATH", default="logs/broker.log"),
    Validator("LOGGING.STRUCTURED", default=False),
    Validator("THREAD_LIMIT", default=None),
    Validator("INVENTORY_FIELDS", is_type_of=dict),
    Validator("INVENTORY_LIST_VARS", is_type_of=str, default="hostname | name"),
    Validator("LESS_COLORS", default=False),
]


def _handle_migrations(cfg_manager, file_exists):
    """Handle settings file migrations if needed."""
    if not (file_exists and cfg_manager._get_migrations()):
        return

    if INTERACTIVE_MODE:
        click.secho(
            "Broker settings file has pending migrations.\n"
            "Continuing without running the migrations may cause errors.",
            fg="red",
        )
        if click.confirm("Would you like to run the migrations now?"):
            cfg_manager.migrate()
        else:
            click.secho("Continuing without running migrations.", fg="yellow")
    else:
        cfg_manager.migrate()


def _handle_vault_env_vars():
    """Temporarily remove vault environment variables and return them."""
    vault_vars = {k: v for k, v in os.environ.items() if "VAULT_" in k}
    for k in vault_vars:
        del os.environ[k]
    return vault_vars


def _create_and_configure_settings(file_path, file_exists, config_dict):
    """Create settings object and apply configuration."""
    new_settings = Dynaconf(
        settings_file=str(file_path) if file_exists else None,
        ENVVAR_PREFIX_FOR_DYNACONF="BROKER",
        validators=BASE_VALIDATORS,
    )

    # Remove vault loader if set somehow
    new_settings._loaders = [loader for loader in new_settings._loaders if "vault" not in loader]

    # Add any configuration values passed in, merging nested dicts
    if config_dict:
        for key, value in config_dict.items():
            # Use the original key for settings lookup and assignment
            existing = new_settings.get(key)
            if existing is not None and isinstance(existing, dict) and isinstance(value, dict):
                # Deep merge the nested dictionaries
                new_settings[key] = merge_dicts(existing, value)
            else:
                new_settings[key] = value

    return new_settings


def _validate_settings(new_settings, skip_validation, file_exists, file_path):
    """Validate settings and handle errors."""
    try:
        if skip_validation:
            new_settings.validators.validate(only="LOGGING")
        else:
            new_settings.validators.validate()
    except ValidationError as err:
        if file_exists:
            raise ConfigurationError(f"Configuration error in {file_path}: {err.args[0]}") from err
        # If no file exists, just ensure defaults are applied


def create_settings(
    config_dict=None, config_file=None, perform_migrations=False, skip_validation=False
):
    """Create a new settings object with custom configuration.

    Args:
        config_dict: Dictionary containing configuration values to overlay onto settings
        config_file: Path to a settings file to use instead of the default
        perform_migrations: Whether to check for and perform migrations
        skip_validation: Whether to skip all (but logging) validations

    Returns:
        A dynaconf settings object
    """
    file_path = config_file or settings_path
    file_exists = Path(file_path).exists() if file_path else False
    cfg_manager = ConfigManager(settings_path)

    # Handle migrations
    if perform_migrations:
        _handle_migrations(cfg_manager, file_exists)

    # Handle vault environment variables
    vault_vars = _handle_vault_env_vars()

    # Create and configure settings
    new_settings = _create_and_configure_settings(file_path, file_exists, config_dict)

    # Validate settings
    _validate_settings(new_settings, skip_validation, file_exists, file_path)

    # Restore environment variables
    os.environ.update(vault_vars)

    if not file_exists and not config_dict and not config_file and INTERACTIVE_MODE:
        click.secho(
            "No Broker settings file found. Using default settings.\n"
            "Run 'broker config init' to create a settings file.",
            fg="yellow",
        )

    return new_settings


def clone_global_settings():
    """Get broker settings by cloning the current global settings object."""
    return settings.dynaconf_clone()


class _SettingsProxy:
    """Proxy object that creates settings on first access."""

    def __init__(self):
        self._settings = None

    def _ensure_settings(self):
        if self._settings is None:
            self._settings = create_settings(perform_migrations=True)
        return self._settings

    def __getattr__(self, name):
        return getattr(self._ensure_settings(), name)

    def __getitem__(self, key):
        return self._ensure_settings()[key]

    def __setitem__(self, key, value):
        self._ensure_settings()[key] = value

    def __contains__(self, key):
        return key in self._ensure_settings()


# Create the global settings object (deferred)
settings = _SettingsProxy()
