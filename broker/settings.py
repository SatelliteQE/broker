import os
from pathlib import Path
from dynaconf import Dynaconf
from logzero import logger

settings_file = "broker_settings.yaml"
BROKER_DIRECTORY = Path()

if "BROKER_DIRECTORY" in os.environ:
    envar_location = Path(os.environ["BROKER_DIRECTORY"])
    if envar_location.is_dir():
        BROKER_DIRECTORY = envar_location

settings = Dynaconf(settings_file=str(BROKER_DIRECTORY.joinpath("broker_settings.yaml")))
