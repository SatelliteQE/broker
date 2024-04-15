"""Module providing classes to establish ssh or ssh-like connections to hosts.

Classes:
    Session - Wrapper around ansible-pylibssh auth/connection system.
    InteractiveShell - Wrapper around ansible-pylibssh non-blocking channel system.

Note: You typically want to use a Host object instance to create sessions,
      not these classes directly.
"""
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

from pylibsshext.session import Session as _Session

from broker import exceptions, helpers
from broker.binds.utils import _create_connect_socket


class Session:
    """Wrapper around ansible-pylibssh's auth/connection system."""

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
        key_filename = kwargs.get("key_filename")
        password = kwargs.get("password")
        timeout = kwargs.get("timeout", 60)

        # Create the socket
        self.sock, self.is_ipv6 = _create_connect_socket(
            host,
            port,
            timeout,
            ipv6=kwargs.get("ipv6", False),
            ipv4_fallback=kwargs.get("ipv4_fallback", True),
        )

        self.session = _Session()
        try:
            if key_filename:
                auth_type = "Key"
                key_path = Path(key_filename)
                if not key_path.exists():
                    raise FileNotFoundError(f"Key not found in '{key_filename}'")
                self.session.connect(
                    fd=self.sock.fileno(),
                    host=host,
                    host_key_checking=False,
                    port=port,
                    private_key=key_path.read_bytes(),
                    timeout=timeout,
                    user=user,
                )
            elif password:
                auth_type = "Password"
                self.session.connect(
                    fd=self.sock.fileno(),
                    host=host,
                    host_key_checking=False,
                    password=password,
                    port=port,
                    timeout=timeout,
                    user=user,
                )
            elif user:
                auth_type = "Session"
                raise exceptions.NotImplementedError("Session-based auth for ansible-pylibssh")
            else:
                raise exceptions.AuthenticationError("No password or key file provided.")
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

        # TODO read/write without local dest_path intermediate
        sftp_down = self.session.sftp()
        sftp_up = dest_host.session.session.sftp()
        try:
            with NamedTemporaryFile() as tmp:
                sftp_down.get(source, tmp.file.name)
                sftp_up.put(tmp.file.name, dest_path)
        finally:
            sftp_down.close()
            sftp_up.close()

    def run(self, command, timeout=0):
        """Run a command on the host and return the results."""
        channel = self.session.new_channel()
        try:
            res = channel.exec_command(command)
            return helpers.Result(
                status=res.returncode,
                stdout=res.stdout.decode("utf-8"),
                stderr=res.stderr.decode("utf-8"),
            )
        finally:
            channel.close()

    def scp_write(self, source, destination=None, ensure_dir=True):
        """SCP write a local file to a remote destination."""
        destination = self._set_destination(source, destination)
        if ensure_dir:
            self.run(f"mkdir -p {Path(destination).absolute().parent}")

        scp = self.session.scp()
        scp.put(destination, source)

    def sftp_read(self, source, destination=None, return_data=False):
        """Read a remote file into a local destination or return a bytes object if return_data is True."""
        # TODO read contents directly into bytes object if return_data is True
        destination = self._set_destination(source, destination)
        # Create the destination path if it doesn't exist
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        # Initiate the sftp session, read data, write it to a local destination
        sftp = self.session.sftp()
        try:
            sftp.get(source, destination)
            if return_data:
                return destination.read_bytes()
        finally:
            if return_data:
                destination.unlink()
            sftp.close()

    def sftp_write(self, source, destination=None, ensure_dir=True):
        """Sftp write a local file to a remote destination."""
        destination = self._set_destination(source, destination)
        if ensure_dir:
            self.run(f"mkdir -p {Path(destination).absolute().parent}")

        sftp = self.session.sftp()
        try:
            sftp.put(source, destination)
        finally:
            sftp.close()

    def shell(self, pty=False):
        """Create and return an interactive shell instance."""
        return InteractiveShell(self.session, pty=pty)

    @contextmanager
    def tail_file(self, filename):
        """Tail a file on the remote host."""
        # TODO re-factor to use SFTP instead
        initial_size = int(self.run(f"stat -c %s {filename}").stdout.strip())
        yield (tailer := FileTailer(session=self.session, filename=filename))
        tailer.contents = self.run(f"tail -c +{initial_size} {filename}").stdout


class FileTailer:
    """FileTailer class."""

    def __init__(self, **kwargs):
        self.session = kwargs.get("session")
        self.filename = kwargs.get("filename")


class InteractiveShell:
    """A helper class that provides an interactive shell interface.

    Preferred use of this class is via its context manager

    with InteractiveShell(my_session) as shell:
        shell.send("some-command --argument")
        shell.send("another-command")
        time.sleep(5)  # give time for things to complete
    assert "expected text" in shell.result.stdout

    """

    def __init__(self, session, pty=False):
        # FIXME: invoke_shell() always requests pty
        # self._channel = session.invoke_shell(pty=pty)
        if pty:
            self._channel = session.invoke_shell()
        else:
            raise exceptions.NotImplementedError("Interactive shell with pty=False")

    def __enter__(self):
        """Return the shell object."""
        return self

    def __exit__(self, *exc_args):
        """Close the channel and read stdout/stderr and status."""
        self.send("exit")  # ensure shell has exited
        self._channel.send_eof()

        stdout = self._channel.read_bulk_response(timeout=0.5)
        stderr = self._channel.read_bulk_response(stderr=1)
        status = self._channel.get_channel_exit_status()

        self._channel.close()

        self.result = helpers.Result(
            status=status,
            stdout=stdout.decode("utf-8"),
            stderr=stderr.decode("utf-8"),
        )

    def __getattribute__(self, name):
        """Expose non-duplicate attributes from the Channel instance."""
        try:
            return object.__getattribute__(self, name)
        except AttributeError:
            return getattr(self._channel, name)

    def send(self, cmd):
        """Send a command to the channel, ensuring a newline character."""
        if not cmd.endswith("\n"):
            cmd += "\n"
        self._channel.write(cmd.encode("utf-8"))

    def stdout(self):
        """Read the contents of a channel's stdout."""
        # FIXME handle read on open channel
        res = self._channel.read_bulk_response()
        return res.stdout.decode("utf-8")
