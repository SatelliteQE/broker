import os
import sys
from pathlib import Path
from dynaconf import Dynaconf, Validator
from dynaconf.validator import ValidationError
from logzero import logger

settings_file = "broker_settings.yaml"
BROKER_DIRECTORY = Path()

if "BROKER_DIRECTORY" in os.environ:
    envar_location = Path(os.environ["BROKER_DIRECTORY"])
    if envar_location.is_dir():
        BROKER_DIRECTORY = envar_location

settings_path = BROKER_DIRECTORY.joinpath("broker_settings.yaml")
must_exist = [
    "ANSIBLETOWER.base_url",
    "HOST_PASSWORD",
]
validators = [
    Validator(*must_exist, must_exist=True),
    Validator("ANSIBLETOWER.release_workflow", default="remove-vm"),
    Validator("ANSIBLETOWER.extend_workflow", default="extend-vm"),
    Validator("ANSIBLETOWER.workflow_timeout", is_type_of=int, default=3600),
    Validator("ANSIBLETOWER.results_limit", is_type_of=int, default=20),
    Validator("HOST_USERNAME", default="root"),
    # Validator combination for username+password or token
    ((Validator("ANSIBLETOWER.username", must_exist=True) & Validator("ANSIBLETOWER.password", must_exist=True))
        | Validator("ANSIBLETOWER.token", must_exist=True))
]
settings = Dynaconf(settings_file=str(settings_path.absolute()), validators=validators,)

try:
    settings.validators.validate()
except ValidationError as err:
    logger.error(f"Configuration error in {settings_path.absolute()}: {err}")
    sys.exit()
