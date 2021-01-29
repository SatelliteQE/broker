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

validators = [
    Validator("HOST_USERNAME", default="root"),
    Validator("HOST_PASSWORD", must_exist=True),
]
settings = Dynaconf(
    settings_file=str(settings_path.absolute()),
    ENVVAR_PREFIX_FOR_DYNACONF="BROKER",
    validators=validators,
)

try:
    settings.validators.validate()
except ValidationError as err:
    logger.error(f"Configuration error in {settings_path.absolute()}: {err}")
    sys.exit()
