"""Module providing classes to establish ssh or ssh-like connections to hosts.

Classes:
    Session - Wrapper around ssh2-python's auth/connection system.
    InteractiveShell - Wrapper around ssh2-python's non-blocking channel system.
    ContainerSession - Wrapper around docker-py's exec system.

Note: You typically want to use a Host object instance to create sessions,
      not these classes directly.
"""

from contextlib import contextmanager
from importlib.metadata import entry_points
from pathlib import Path
import tempfile

from logzero import logger

from broker import helpers
from broker.exceptions import NotImplementedError
from broker.settings import clone_global_settings

SSH_NOT_INSTALLED_MSG = (
    "{backend} is not installed.\n"
    "ssh actions will not work.\n"
    "To use ssh, run 'pip install broker[{backend}]'."
)


def make_session(broker_settings=None, **kwargs):
    """Create a Session instance using the configured backend.

    Args:
        broker_settings: Optional settings object to use instead of global settings
        **kwargs: Additional arguments to pass to the Session constructor

    Returns:
        A Session instance from the configured backend
    """
    _settings = broker_settings or clone_global_settings()
    backend = _settings.SSH.BACKEND

    logger.debug(f"Attempting to load SSH backend: {backend}")

    try:
        # Look up the session class from entry points
        session_eps = entry_points(group="broker.ssh.session")
        # Get the specific backend requested or the first one registered
        session_cls = None
        for ep in session_eps:
            if ep.name == backend:
                session_cls = ep.load()
                break

        if not session_cls:
            backends = [ep.name for ep in session_eps]
            error_msg = f"SSH backend '{backend}' not supported. Supported backends: {backends}"
            logger.error(error_msg)
            raise ImportError(error_msg)

        # Create and return the session instance
        return session_cls(broker_settings=_settings, **kwargs)
    except ImportError:
        error_msg = SSH_NOT_INSTALLED_MSG.format(backend=backend)
        logger.warning(error_msg)

        # Create and return a dummy session that won't work but won't crash
        class DummySession:
            def __init__(self, broker_settings=None, **kwargs):
                self._settings = broker_settings or clone_global_settings()

        return DummySession(broker_settings=_settings, **kwargs)


def get_interactive_shell(broker_settings=None):
    """Get the InteractiveShell class for the configured backend.

    Args:
        broker_settings: Optional settings object to use instead of global settings

    Returns:
        The InteractiveShell class from the configured backend or None if not available
    """
    _settings = broker_settings or clone_global_settings()
    backend = _settings.SSH.BACKEND

    try:
        # Look up the interactive shell class from entry points
        shell_eps = entry_points(group="broker.ssh.interactive_shell")

        # Get the specific backend requested
        for ep in shell_eps:
            if ep.name == backend:
                return ep.load()

        # If no matching backend was found for interactive shell
        logger.debug(f"No interactive shell available for backend: {backend}")
        return None
    except ImportError:
        return None


# Expose the InteractiveShell for backwards compatibility
InteractiveShell = get_interactive_shell()

# Expose Session class (for backwards compatibility)
Session = make_session


class ContainerSession:
    """An approximation of ssh-based functionality from the Session class."""

    def __init__(self, cont_inst, runtime=None, broker_settings=None):
        self._cont_inst = cont_inst
        self._settings = broker_settings or clone_global_settings()
        if not runtime:
            runtime = self._settings.CONTAINER.runtime
        self.runtime = runtime

    def run(self, command, demux=True, **kwargs):
        """Container approximation of Session.run."""
        kwargs.pop("timeout", None)  # Timeouts are set at the client level
        kwargs["demux"] = demux
        if "'" in command:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sh") as tmp:
                tmp.write(command)
                tmp.seek(0)
                command = f"/bin/bash {tmp.name}"
                self.sftp_write(tmp.name)
        if any(s in command for s in "|&><"):
            # Containers don't handle pipes, redirects, etc well in a bare exec_run
            command = f"/bin/bash -c '{command}'"
        result = self._cont_inst._cont_inst.exec_run(command, **kwargs)
        if demux:
            result = helpers.Result.from_duplexed_exec(result, self.runtime)
        else:
            result = helpers.Result.from_nonduplexed_exec(result)
        return result

    def disconnect(self):
        """Needed for simple compatibility with Session."""

    @contextmanager
    def tail_file(self, filename):
        """Simulate tailing a file on the remote host."""
        initial_size = int(self.run(f"stat -c %s {filename}").stdout.strip())
        yield (res := helpers.Result())
        # get the contents of the file from the initial size to the end
        result = self.run(f"tail -c +{initial_size} {filename}")
        res.__dict__.update(result.__dict__)

    def sftp_write(self, source, destination=None, ensure_dir=True):
        """Add one of more files to the container."""
        # ensure source is a list of Path objects
        if not isinstance(source, list):
            source = [Path(source)]
        else:
            source = [Path(src) for src in source]
        # validate each source's existenence
        for src in source:
            if not Path(src).exists():
                raise FileNotFoundError(src)
        destination = destination or f"{source[0].parent}/"
        # Files need to be added to a tarfile
        with helpers.temporary_tar(source) as tar:
            logger.debug(f"{self._cont_inst.hostname} adding file(s) {source} to {destination}")
            if ensure_dir:
                if destination.endswith("/"):
                    self.run(f"mkdir -m 666 -p {destination}")
                else:
                    self.run(f"mkdir -m 666 -p {Path(destination).parent}")
            self._cont_inst._cont_inst.put_archive(str(destination), tar.read_bytes())

    def sftp_read(self, source, destination=None, return_data=False):
        """Get a file or directory from the container."""
        destination = Path(destination or source)
        logger.debug(f"{self._cont_inst.hostname} getting file {source}")
        data, status = self._cont_inst._cont_inst.get_archive(source)
        logger.debug(f"{self._cont_inst.hostname}: {status}")
        data = b"".join(d for d in data)
        if destination.name == "_raw":
            return data
        with helpers.data_to_tempfile(data, as_tar=True) as tar:
            del data
            if len(tar.getmembers()) == 1:
                f = tar.extractfile(tar.getmember(Path(source).name))
                if return_data:
                    logger.debug(f"Extracting {source}")
                    return f.read()
                else:
                    logger.debug(f"Extracting {source} to {destination}")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(f.read())
            else:
                logger.warning("More than one member was found in the tar file.")
                tar.extractall(destination.parent if destination.is_file() else destination)

    def shell(self, pty=False):
        """Create and return an interactive shell instance."""
        raise NotImplementedError("ContainerSession.shell has not been implemented")
