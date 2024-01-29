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
import inspect
import os
from pathlib import Path

import click
from dynaconf import Dynaconf, Validator
from dynaconf.validator import ValidationError

from broker.exceptions import ConfigurationError


def init_settings(settings_path, interactive=False):
    """Initialize the broker settings file."""
    raw_url = (
        "https://raw.githubusercontent.com/SatelliteQE/broker/master/broker_settings.yaml.example"
    )
    proceed = not False
    if interactive:
        try:
            proceed = (
                click.prompt(
                    f"Download example file from GitHub?\n{raw_url}",
                    type=click.Choice(["y", "n"]),
                    default="y",
                )
                == "y"
            )
        except click.core.Abort:
            # We're likely in a different non-interactive environment (container?)
            global INTERACTIVE_MODE
            proceed, INTERACTIVE_MODE = True, False
    if proceed:
        # download example file from github
        import requests

        click.echo(f"Downloading example file from: {raw_url}")
        raw_file = requests.get(raw_url, timeout=60)
        settings_path.write_text(raw_file.text)
        if INTERACTIVE_MODE:
            try:
                click.edit(filename=str(settings_path.absolute()))
            except click.exceptions.ClickException:
                click.secho(
                    f"Please edit the file {settings_path.absolute()} and add your settings.",
                    fg="yellow",
                )
    else:
        raise ConfigurationError(f"Broker settings file not found at {settings_path.absolute()}.")


INTERACTIVE_MODE = False
# GitHub action context
if "GITHUB_WORKFLOW" not in os.environ:
    # determine if we're being ran from a CLI
    for frame in inspect.stack()[::-1]:
        if "/bin/broker" in frame.filename:
            INTERACTIVE_MODE = True
            break


BROKER_DIRECTORY = Path.home().joinpath(".broker")

if "BROKER_DIRECTORY" in os.environ:
    envar_location = Path(os.environ["BROKER_DIRECTORY"])
    if envar_location.is_dir():
        BROKER_DIRECTORY = envar_location

# ensure the broker directory exists
BROKER_DIRECTORY.mkdir(parents=True, exist_ok=True)

settings_path = BROKER_DIRECTORY.joinpath("broker_settings.yaml")
inventory_path = BROKER_DIRECTORY.joinpath("inventory.yaml")

if not settings_path.exists():
    click.secho(f"Broker settings file not found at {settings_path.absolute()}.", fg="red")
    init_settings(settings_path, interactive=INTERACTIVE_MODE)

validators = [
    Validator("HOST_USERNAME", default="root"),
    Validator("HOST_PASSWORD", default="toor"),
    Validator("HOST_CONNECTION_TIMEOUT", default=60),
    Validator("HOST_SSH_PORT", default=22),
    Validator("HOST_SSH_KEY_FILENAME", default=None),
    Validator("HOST_IPV6", default=False),
    Validator("HOST_IPV4_FALLBACK", default=True),
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
    settings.validators.validate()
except ValidationError as err:
    raise ConfigurationError(
        f"Configuration error in {settings_path.absolute()}: {err.args[0]}"
    ) from err

os.environ.update(vault_vars)
