"""Module providing the functionality powering the `broker config` command."""

import importlib
from importlib.metadata import version
import inspect
import json
import os
from pathlib import Path
import pkgutil
from tempfile import NamedTemporaryFile

import click
from logzero import logger
from packaging.version import Version
import yaml

from broker import exceptions

C_SEP = "."  # chunk separator
GH_CFG = "https://raw.githubusercontent.com/SatelliteQE/broker/master/broker_settings.yaml.example"


def file_name_to_ver(file_name):
    """Convert a version-encoded filename `v0_6_0` to a `Version` object."""
    return Version(file_name[1:].replace("_", "."))


def yaml_format(data):
    """Format the data as yaml.

    Duplicating here to avoid circular imports.
    """
    return yaml.dump(data, default_flow_style=False, sort_keys=False)


class ConfigManager:
    """Class to interact with Broker's configuration file.

    One important concept of these commands is the concept of a "chunk".

    A chunk is a part of the configuration file that can be accessed or updated.
    Chunks are specified by their keys in the configuration file.
    Nested chunks are separated by periods.

    e.g. broker config view AnsibleTower.instances.my_instance
    """

    version = version("broker")

    def __init__(self, settings_path=None):
        self._interactive_mode = None
        self._settings_path = settings_path
        if settings_path:
            if settings_path.exists():
                self._cfg = yaml.safe_load(self._settings_path.read_text())
            else:
                click.secho(
                    f"Broker settings file not found at {settings_path.absolute()}.", fg="red"
                )
                self.init_config_file()

    @property
    def interactive_mode(self):
        """Determine if Broker is running in interactive mode."""
        if self._interactive_mode is None:
            self._interactive_mode = False
            # GitHub action context
            if "GITHUB_WORKFLOW" not in os.environ:
                # determine if we're being ran from a CLI
                for frame in inspect.stack()[::-1]:
                    if "/bin/broker" in frame.filename:
                        self._interactive_mode = True
                        break
        return self._interactive_mode

    def _interactive_edit(self, chunk):
        """Write the chunk data to a temporary file and open it in an editor."""
        with NamedTemporaryFile(mode="w+", suffix=".yaml") as tmp:
            tmp.write(yaml_format(chunk))
            tmp.seek(0)
            click.edit(filename=tmp.name)
            tmp.seek(0)
            new_data = tmp.read()
        # first try to load it as yaml
        try:
            return yaml.safe_load(new_data)
        except yaml.YAMLError:  # then try json
            try:
                return json.loads(new_data)
            except json.JSONDecodeError:  # finally, just return the raw data
                return new_data

    def _import_config(self, source, is_url=False):
        """Initialize the broker settings file from a source."""
        proceed = True
        if self.interactive_mode:
            try:
                proceed = click.confirm(f"Get example file from {source}?")
            except click.core.Abort:
                # We're likely in a different non-interactive environment (container?)
                self._interactive_mode = False
        if not proceed:
            return
        # get example file from source
        if is_url:
            import requests

            click.echo(f"Downloading example file from: {source}")
            return requests.get(source, timeout=60).text
        else:
            return source.read_text()

    def _get_migrations(self, force_version=None):
        """Construct a list of all applicable migrations."""
        from broker import config_migrations

        config_version = Version(self._cfg.get("_version", "0.0.0"))
        if force_version:
            force_version = Version(force_version)
        migrations = []
        for _, name, _ in pkgutil.iter_modules(config_migrations.__path__):
            module = importlib.import_module(f"broker.config_migrations.{name}")
            if hasattr(module, "run_migrations"):
                if force_version and force_version == file_name_to_ver(name):
                    migrations.append(module)
                    break
                elif config_version < file_name_to_ver(name):
                    migrations.append(module)
        return migrations

    def backup(self):
        """Backup the current configuration file."""
        logger.debug(
            f"Backing up the configuration file to {self._settings_path.with_suffix('.bak')}"
        )
        self._settings_path.with_suffix(".bak").write_text(self._settings_path.read_text())

    def restore(self):
        """Restore the configuration file from a backup if it exists."""
        logger.debug(
            f"Restoring the configuration file from {self._settings_path.with_suffix('.bak')}"
        )
        backup_path = self._settings_path.with_suffix(".bak")
        if not backup_path.exists():
            raise exceptions.UserError("No backup file found.")
        self._settings_path.write_text(backup_path.read_text())

    def edit(self, chunk=None, content=None):
        """Open the config file in an editor."""
        if not self.interactive_mode:
            raise exceptions.UserError(
                "Attempted to edit the config in non-interactive mode.\n"
                "Did you mean to use the `set` method instead?"
            )
        content = content or self.get(chunk=chunk)
        new_val = self._interactive_edit(content)
        self.update(chunk, new_val)

    def get(self, chunk=None, curr_chunk=None):
        """Get a chunk of Broker's config or the whole config."""
        if not curr_chunk:
            curr_chunk = self._cfg
        if not chunk:
            return curr_chunk
        if C_SEP in chunk:
            curr, chunk = chunk.split(C_SEP, 1)
            # curr = int(curr) if curr.isdigit() else curr
            return self.get(chunk, curr_chunk=curr_chunk[curr])
        else:
            # chunk = int(chunk) if chunk.isdigit() else chunk
            try:
                return curr_chunk[chunk]
            except KeyError:
                raise exceptions.UserError(f"Chunk '{chunk}' not found in the config.")

    def update(self, chunk, new_val, curr_chunk=None):
        """Update a chunk of Broker's config or the whole config."""
        # Recursive down to find the chunk to update, then propagate the new value back up
        if not curr_chunk:  # we're at the top level, so update the config directly
            if chunk is None:  # the whole config is being updated
                self._cfg = new_val
            elif C_SEP in chunk:  # the update needs to happen at a lower level
                curr, chunk = chunk.split(C_SEP, 1)
                self._cfg[curr] = self.update(chunk, new_val, curr_chunk=self._cfg[curr])
            else:
                self._cfg[chunk] = new_val
            # update the config file if it exists
            if self._settings_path.exists():
                self.backup()
            self._settings_path.write_text(
                yaml.dump(self._cfg, default_flow_style=False, sort_keys=False)
            )
        else:  # we're not at the top level, so keep going down
            if C_SEP in chunk:
                curr, chunk = chunk.split(C_SEP, 1)
                curr_chunk[curr] = self.update(chunk, new_val, curr_chunk=curr_chunk[curr])
            else:
                curr_chunk[chunk] = new_val
            return curr_chunk

    def nicks(self, nick=None):
        """Get a list of nicks or single nick information."""
        nicks = self.get("nicks")
        if nick:
            return nicks[nick]
        return list(nicks.keys())

    def init_config_file(self, chunk=None, _from=None):
        """Check for the existence of the config file and create it if it doesn't exist."""
        if self.interactive_mode and self._settings_path.exists() and not chunk:
            # if the file exists, ask the user if they want to overwrite it
            if (
                click.prompt(
                    f"Overwrite the settings file at {self._settings_path.absolute()}. Overwrite?",
                    type=click.Choice(["y", "n"]),
                    default="n",
                )
                != "y"
            ):
                return
        raw_data = None
        if _from:
            # determine if this is a local file or a URL
            if Path(_from).exists():
                raw_data = self._import_config(Path(_from))
            else:
                raw_data = self._import_config(_from, is_url=True)
        # if we still don't have data, get the example file from the local repo or GitHub
        if not raw_data:
            # get the example file from the local repo or GitHub
            example_path = Path(__file__).parent.parent.joinpath("broker_settings.yaml.example")
            if example_path.exists():
                raw_data = self._import_config(example_path)
            if not raw_data:
                raw_data = self._import_config(GH_CFG, is_url=True)
        if not raw_data:
            raise exceptions.ConfigurationError(
                f"Broker settings file not found at {self._settings_path.absolute()}."
            )
        chunk_data = self.get(chunk, yaml.safe_load(raw_data))
        if self.interactive_mode:
            chunk_data = self._interactive_edit(chunk_data)
        self.update(chunk, chunk_data)

    def migrate(self, force_version=None):
        """Migrate the config from a previous version of Broker."""
        # get all available migrations
        if not (migrations := self._get_migrations(force_version)):
            logger.info("No migrations are applicable to your config.")
            return
        # run all migrations in order
        working_config = self._cfg
        for migration in sorted(migrations, key=lambda m: m.TO_VERSION):
            working_config = migration.run_migrations(working_config)
        self.backup()
        self._settings_path.write_text(
            yaml.dump(working_config, default_flow_style=False, sort_keys=False)
        )
        logger.info("Config migration complete.")

    def validate(self, chunk, providers=None):
        """Validate a top-level chunk of Broker's config."""
        if "." in chunk:  # only validate the top-level chunk
            chunk = chunk.split(".")[0]
        if chunk.lower() == "base":  # validate the base config
            return  # this happens before we're called
        if chunk.lower() == "ssh":
            from broker.settings import settings

            settings.validators.validate(only="SSH")
            return
        if providers is None:
            raise exceptions.UserError(
                "Attempted to validate provider settings without passing providers."
            )
        if ":" in chunk:
            prov_name, instance = chunk.split(":")
            providers[prov_name](**{prov_name: instance})
        if chunk == "all":
            for prov_name, prov_cls in providers.items():
                if prov_name == "TestProvider":
                    continue
                logger.info(f"Validating {prov_name} provider settings.")
                try:  # we want to suppress all exceptions here to allow each provider to validate
                    prov_cls()
                except Exception as err:  # noqa: BLE001
                    logger.warning(f"Provider {prov_name} failed validation: {err}")
            return
        if chunk not in providers:
            raise exceptions.UserError(
                "I don't know how to validate that.\n"
                "If it's important, it is likely covered in the base validations."
            )
        providers[chunk]()
