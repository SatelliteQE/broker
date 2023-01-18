import os
from pathlib import Path
from dynaconf import Dynaconf, Validator
from dynaconf.validator import ValidationError
from broker.exceptions import ConfigurationError

settings_file = "broker_settings.yaml"
BROKER_DIRECTORY = Path()

if "BROKER_DIRECTORY" in os.environ:
    envar_location = Path(os.environ["BROKER_DIRECTORY"])
    if envar_location.is_dir():
        BROKER_DIRECTORY = envar_location

settings_path = BROKER_DIRECTORY.joinpath("broker_settings.yaml")
inventory_path = BROKER_DIRECTORY.joinpath("inventory.yaml")

validators = [
    Validator("HOST_USERNAME", default="root"),
    Validator("HOST_PASSWORD", must_exist=True),
    Validator("HOST_CONNECTION_TIMEOUT", default=None),
    Validator("HOST_SSH_PORT", default=22),
    Validator("HOST_SSH_KEY_FILENAME", default=None),
]

# temportary fix for dynaconf #751
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
    )

os.environ.update(vault_vars)
