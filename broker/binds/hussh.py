"""Module providing classes to establish ssh or ssh-like connections to hosts.

Classes:
    Session - Wrapper around hussh's auth/connection system.

Note: You typically want to use a Host object instance to create sessions,
      not these classes directly.
"""
from contextlib import contextmanager
from pathlib import Path

from hussh import Connection

from broker import exceptions, helpers


class Session:
    """Wrapper around hussh's auth/connection system."""

    def __init__(self, **kwargs):
        """Initialize a Session object.

        kwargs:
            hostname (str): The hostname or IP address of the remote host. Defaults to 'localhost'.
            username (str): The username to authenticate with. Defaults to 'root'.
            timeout (float): The timeout for the connection in seconds. Defaults to 60.
            port (int): The port number to connect to. Defaults to 22.
            key_filename (str): The path to the private key file to use for authentication.
            password (str): The password to use for authentication.
            ipv6 (bool): Whether or not to use IPv6. Defaults to False.
            ipv4_fallback (bool): Whether or not to fallback to IPv4 if IPv6 fails. Defaults to True.

        Raises:
            AuthException: If no password or key file is provided.
            ConnectionError: If the connection fails.
            FileNotFoundError: If the key file is not found.
        """
        host = kwargs.get("hostname", "localhost")
        user = kwargs.get("username", "root")
        port = kwargs.get("port", 22)
        timeout = kwargs.get("timeout", 60) * 1000

        key_filename = kwargs.get("key_filename")
        password = kwargs.get("password")

        # TODO Create and use socket if hussh allows user to specify one
        self.session = None

        conn_kwargs = {"username": user, "port": port, "timeout": timeout}
        try:
            if key_filename:
                auth_type = "Key"
                if not Path(key_filename).exists():
                    raise FileNotFoundError(f"Key not found in '{key_filename}'")
                conn_kwargs["private_key"] = key_filename
            elif password:
                auth_type = "Password"
                conn_kwargs["password"] = password
            elif user:
                auth_type = "Session"
            else:
                raise exceptions.AuthenticationError("No password or key file provided.")

            self.session = Connection(host, **conn_kwargs)

        except Exception as err:  # noqa: BLE001
            raise exceptions.AuthenticationError(
                f"{auth_type}-based authentication failed."
            ) from err

    @staticmethod
    def _set_destination(source, destination):
        dest = destination or source
        if dest.endswith("/"):
            dest = dest + Path(source).name
        return dest

    def disconnect(self):
        """Disconnect session."""

    def remote_copy(self, source, dest_host, dest_path=None, ensure_dir=True):
        """Copy a file from this host to another."""
        dest_path = dest_path or source
        if ensure_dir:
            dest_host.session.run(f"mkdir -p {Path(dest_path).absolute().parent}")

        # Copy from this host to destination host
        self.session.remote_copy(
            source_path=source, dest_conn=dest_host.session.session, dest_path=dest_path
        )

    def run(self, command, timeout=0):
        """Run a command on the host and return the results."""
        result = self.session.execute(command, timeout=helpers.translate_timeout(timeout))
        # Create broker Result from hussh SSHResult
        return helpers.Result(
            status=result.status,
            stderr=result.stderr,
            stdout=result.stdout,
        )

    def scp_read(self, source, destination=None, return_data=False):
        """SCP read a remote file into a local destination or return a bytes object if return_data is True."""
        destination = self._set_destination(source, destination)
        if return_data:
            return self.session.scp_read(remote_path=source)
        self.session.scp_read(remote_path=source, local_path=destination)

    def scp_write(self, source, destination=None, ensure_dir=True):
        """SCP write a local file to a remote destination."""
        destination = self._set_destination(source, destination)
        if ensure_dir:
            self.run(f"mkdir -p {Path(destination).absolute().parent}")
        self.session.scp_write(source, destination)

    def sftp_read(self, source, destination=None, return_data=False):
        """Read a remote file into a local destination or return a bytes object if return_data is True."""
        if return_data:
            return self.session.sftp_read(remote_path=source).encode("utf-8")

        destination = self._set_destination(source, destination)

        # Create the destination path if it doesn't exist
        Path(destination).parent.mkdir(parents=True, exist_ok=True)

        self.session.sftp_read(remote_path=source, local_path=destination)

    def sftp_write(self, source, destination=None, ensure_dir=True):
        """Sftp write a local file to a remote destination."""
        destination = self._set_destination(source, destination)
        if ensure_dir:
            self.run(f"mkdir -p {Path(destination).absolute().parent}")
        self.session.sftp_write(local_path=source, remote_path=destination)

    def shell(self, pty=False):
        """Create and return an interactive shell instance."""
        return self.session.shell(pty=pty)

    @contextmanager
    def tail_file(self, filename):
        """Tail a file on the remote host."""
        with self.session.tail(filename) as _tailer:
            yield (tailer := FileTailer(tailer=_tailer))
        tailer.contents = _tailer.contents


class FileTailer:
    """Wrapper for hussh's FileTailer class."""

    def __init__(self, **kwargs):
        self.tailer = kwargs.get("tailer")
