"""Module providing classes to establish ssh or ssh-like connections to hosts.

Classes:
    Session - Wrapper around ssh2-python's auth/connection system.
    InteractiveShell - Wrapper around ssh2-python's non-blocking channel system.

Note: You typically want to use a Host object instance to create sessions,
      not these classes directly.
"""
from contextlib import contextmanager
from pathlib import Path

from logzero import logger
from ssh2 import sftp as _sftp
from ssh2.exceptions import SocketSendError
from ssh2.session import Session as _Session

from broker import exceptions, helpers
from broker.binds.utils import _create_connect_socket

SFTP_MODE = (
    _sftp.LIBSSH2_SFTP_S_IRUSR
    | _sftp.LIBSSH2_SFTP_S_IWUSR
    | _sftp.LIBSSH2_SFTP_S_IRGRP
    | _sftp.LIBSSH2_SFTP_S_IROTH
)
FILE_FLAGS = _sftp.LIBSSH2_FXF_CREAT | _sftp.LIBSSH2_FXF_WRITE | _sftp.LIBSSH2_FXF_TRUNC


class Session:
    """Wrapper around ssh2-python's auth/connection system."""

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

        self.session.handshake(self.sock)
        try:
            if key_filename:
                auth_type = "Key"
                if not Path(key_filename).exists():
                    raise FileNotFoundError(f"Key not found in '{key_filename}'")
                self.session.userauth_publickey_fromfile(user, key_filename)
            elif password:
                auth_type = "Password"
                self.session.userauth_password(user, password)
            elif user:
                auth_type = "Session"
                self.session.agent_auth(user)
            else:
                raise exceptions.AuthenticationError("No password or key file provided.")
        except Exception as err:  # noqa: BLE001
            raise exceptions.AuthenticationError(
                f"{auth_type}-based authentication failed."
            ) from err

    @staticmethod
    def _read(channel):
        """Read the contents of a channel."""
        size, data = channel.read()
        results = ""
        while size > 0:
            try:
                results += data.decode("utf-8")
            except UnicodeDecodeError as err:
                logger.error(f"Skipping data chunk due to {err}\nReceived: {data}")
            size, data = channel.read()
        return helpers.Result.from_ssh(
            stdout=results,
            channel=channel,
        )

    @staticmethod
    def _set_destination(source, destination):
        dest = destination or source
        if dest.endswith("/"):
            dest = dest + Path(source).name
        return dest

    def disconnect(self):
        """Disconnect session."""
        self.session.disconnect()

    def remote_copy(self, source, dest_host, dest_path=None, ensure_dir=True):
        """Copy a file from this host to another."""
        dest_path = dest_path or source
        sftp_down = self.session.sftp_init()
        sftp_up = dest_host.session.session.sftp_init()
        if ensure_dir:
            dest_host.session.run(f"mkdir -p {Path(dest_path).absolute().parent}")
        with sftp_down.open(
            source, _sftp.LIBSSH2_FXF_READ, _sftp.LIBSSH2_SFTP_S_IRUSR
        ) as download, sftp_up.open(dest_path, FILE_FLAGS, SFTP_MODE) as upload:
            for _size, data in download:
                upload.write(data)

    def run(self, command, timeout=0):
        """Run a command on the host and return the results."""
        self.session.set_timeout(helpers.translate_timeout(timeout))
        try:
            channel = self.session.open_session()
        except SocketSendError as err:
            logger.warning(
                f"Encountered connection issue. Attempting to reconnect and retry.\n{err}"
            )
            # FIXME _session is on the Host, not Session
            del self._session
            channel = self.session.open_session()
        channel.execute(
            command,
        )
        channel.wait_eof()
        channel.close()
        channel.wait_closed()
        results = self._read(channel)
        return results

    def scp_write(self, source, destination=None, ensure_dir=True):
        """SCP write a local file to a remote destination."""
        destination = self._set_destination(source, destination)
        fileinfo = (source := Path(source).stat())

        chan = self.session.scp_send64(
            destination,
            fileinfo.st_mode & 0o777,
            fileinfo.st_size,
            fileinfo.st_mtime,
            fileinfo.st_atime,
        )
        if ensure_dir:
            self.run(f"mkdir -p {Path(destination).absolute().parent}")
        with source.open("rb") as local:
            for data in local:
                chan.write(data)

    def sftp_read(self, source, destination=None, return_data=False):
        """Read a remote file into a local destination or return a bytes object if return_data is True."""
        if not return_data:
            destination = self._set_destination(source, destination)

            # create the destination path if it doesn't exist
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)

        # initiate the sftp session, read data, write it to a local destination
        sftp = self.session.sftp_init()
        with sftp.open(source, _sftp.LIBSSH2_FXF_READ, _sftp.LIBSSH2_SFTP_S_IRUSR) as remote:
            captured_data = b""
            for _rc, data in remote:
                captured_data += data
            if return_data:
                return captured_data
            destination.write_bytes(captured_data)

    def sftp_write(self, source, destination=None, ensure_dir=True):
        """Sftp write a local file to a remote destination."""
        destination = self._set_destination(source, destination)

        data = Path(source).read_bytes()
        if ensure_dir:
            self.run(f"mkdir -p {Path(destination).absolute().parent}")

        sftp = self.session.sftp_init()
        with sftp.open(destination, FILE_FLAGS, SFTP_MODE) as remote:
            remote.write(data)

    def shell(self, pty=False):
        """Create and return an interactive shell instance."""
        channel = self.session.open_session()
        return InteractiveShell(channel, pty)

    @contextmanager
    def tail_file(self, filename):
        """Simulate tailing a file on the remote host.

        Example:
            with my_host.session.tail_file("/var/log/messages") as res:
                # do something that creates new messages
            print(res.stdout)

        yields a FileTailer object with contents attr set to the string output
        """
        # TODO refactor to use SFTP instead
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

    with InteractiveShell(channel=my_channel) as shell:
        shell.send("some-command --argument")
        shell.send("another-command")
        time.sleep(5)  # give time for things to complete
    assert "expected text" in shell.result.stdout

    """

    def __init__(self, channel, pty=False):
        self._channel = channel
        if pty:
            self._channel.pty()
        self._channel.shell()

    def __enter__(self):
        """Return the shell object."""
        return self

    def __exit__(self, *exc_args):
        """Close the channel and read stdout/stderr and status."""
        self._channel.close()
        self.result = Session._read(self._channel)

    def __getattribute__(self, name):
        """Expose non-duplicate attributes from the channel."""
        try:
            return object.__getattribute__(self, name)
        except AttributeError:
            return getattr(self._channel, name)

    def send(self, cmd):
        """Send a command to the channel, ensuring a newline character."""
        if not cmd.endswith("\n"):
            cmd += "\n"
        self._channel.write(cmd)

    def stdout(self):
        """Read the contents of a channel's stdout."""
        if not self._channel.eof():
            _, data = self._channel.read(65535)
            results = data.decode("utf-8")
        else:
            results = None
            size, data = self._channel.read()
            while size > 0:
                results += data.decode("utf-8")
                size, data = self._channel.read()
        return results
