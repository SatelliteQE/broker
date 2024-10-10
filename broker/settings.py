"""Broker settings module.

Useful items:
    settings: The settings object.
    init_settings: Function to initialize the settings file.
    validate_settings: Function to validate the settings file.
    INTERACTIVE_MODE: Whether or not Broker is running in interactive mode.
    BROKER_DIRECTORY: The directory where Broker looks for its files.
    TEST_MODE: Whether or not Broker is running in a pytest session.
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
TEST_MODE = os.environ.get("BROKER_TEST_MODE", False)

if TEST_MODE:  # when in test mode, don't use the real broker directory
    BROKER_DIRECTORY = Path("tests/data/")
elif "BROKER_DIRECTORY" in os.environ:
    envar_location = Path(os.environ["BROKER_DIRECTORY"])
    if envar_location.is_dir():
        BROKER_DIRECTORY = envar_location

# ensure the broker directory exists
BROKER_DIRECTORY.mkdir(parents=True, exist_ok=True)

settings_path = BROKER_DIRECTORY.joinpath("broker_settings.yaml")
inventory_path = BROKER_DIRECTORY.joinpath("inventory.yaml")
cfg_manager = ConfigManager(settings_path)


if cfg_manager._get_migrations() and not TEST_MODE:
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

validators = [
    Validator("SSH", is_type_of=dict),
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

# temporary fix for dynaconf #751
vault_vars = {k: v for k, v in os.environ.items() if "VAULT_" in k}
for k in vault_vars:
    del os.environ[k]

settings = Dynaconf(
    settings_file=str(settings_path.absolute()),
    ENVVAR_PREFIX_FOR_DYNACONF="BROKER",
    validators=validators,
)
# to make doubly sure, remove the vault loader if set somehow
settings._loaders = [loader for loader in settings._loaders if "vault" not in loader]

try:
    settings.validators.validate(only="LOGGING")
except ValidationError as err:
    raise ConfigurationError(
        f"Configuration error in {settings_path.absolute()}: {err.args[0]}"
    ) from err

os.environ.update(vault_vars)
