"""Module providing classes to establish ssh or ssh-like connections to hosts.

Classes:
    Session - Wrapper around ssh2-python's auth/connection system.
    InteractiveShell - Wrapper around ssh2-python's non-blocking channel system.
    ContainerSession - Wrapper around docker-py's exec system.

Note: You typically want to use a Host object instance to create sessions,
      not these classes directly.
"""
from contextlib import contextmanager
from pathlib import Path
import socket
import tempfile

from logzero import logger

from broker import exceptions, helpers

try:
    from ssh2 import sftp as ssh2_sftp
    from ssh2.exceptions import SocketSendError
    from ssh2.session import Session as ssh2_Session

    SFTP_MODE = (
        ssh2_sftp.LIBSSH2_SFTP_S_IRUSR
        | ssh2_sftp.LIBSSH2_SFTP_S_IWUSR
        | ssh2_sftp.LIBSSH2_SFTP_S_IRGRP
        | ssh2_sftp.LIBSSH2_SFTP_S_IROTH
    )
    FILE_FLAGS = (
        ssh2_sftp.LIBSSH2_FXF_CREAT | ssh2_sftp.LIBSSH2_FXF_WRITE | ssh2_sftp.LIBSSH2_FXF_TRUNC
    )
except ImportError:
    logger.warning(
        "ssh2-python is not installed, ssh actions will not work.\n"
        "To use ssh, run pip install broker[ssh2]."
    )


def _create_connect_socket(host, port, timeout, ipv6=False, ipv4_fallback=True, sock=None):
    """Create a socket and establish a connection to the specified host and port.

    Args:
        host (str): The hostname or IP address of the remote server.
        port (int): The port number to connect to.
        timeout (float): The timeout value in seconds for the socket connection.
        ipv6 (bool, optional): Whether to use IPv6. Defaults to False.
        ipv4_fallback (bool, optional): Whether to fallback to IPv4 if IPv6 fails. Defaults to True.
        sock (socket.socket, optional): An existing socket object to use. Defaults to None.

    Returns:
        socket.socket: The connected socket object.
        bool: True if IPv6 was used, False otherwise.

    Raises:
        exceptions.ConnectionError: If unable to establish a connection to the host.
    """
    if ipv6 and not sock:
        try:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        except OSError as err:
            if ipv4_fallback:
                logger.warning(f"IPv6 failed with {err}. Falling back to IPv4.")
                return _create_connect_socket(host, port, timeout, ipv6=False)
            else:
                raise exceptions.ConnectionError(
                    f"Unable to establish IPv6 connection to {host}."
                ) from err
    elif not sock:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    if ipv6:
        try:
            sock.connect((host, port))
        except socket.gaierror as err:
            if ipv4_fallback:
                logger.warning(f"IPv6 connection failed to {host}. Falling back to IPv4.")
                return _create_connect_socket(host, port, timeout, ipv6=False, sock=sock)
            else:
                raise exceptions.ConnectionError(
                    f"Unable to establish IPv6 connection to {host}."
                ) from err
    else:
        sock.connect((host, port))
    return sock, ipv6


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
        # create the socket
        self.sock, self.is_ipv6 = _create_connect_socket(
            host,
            port,
            timeout,
            ipv6=kwargs.get("ipv6", False),
            ipv4_fallback=kwargs.get("ipv4_fallback", True),
        )
        self.session = ssh2_Session()
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

    def run(self, command, timeout=0):
        """Run a command on the host and return the results."""
        self.session.set_timeout(helpers.translate_timeout(timeout))
        try:
            channel = self.session.open_session()
        except SocketSendError as err:
            logger.warning(
                f"Encountered connection issue. Attempting to reconnect and retry.\n{err}"
            )
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

        returns a Result object with stdout, stderr, and status
        """
        initial_size = int(self.run(f"stat -c %s {filename}").stdout.strip())
        yield (res := helpers.Result())
        # get the contents of the file from the initial size to the end
        result = self.run(f"tail -c +{initial_size} {filename}")
        res.__dict__.update(result.__dict__)

    def sftp_read(self, source, destination=None, return_data=False):
        """Read a remote file into a local destination or return a bytes object if return_data is True."""
        if not return_data:
            if not destination:
                destination = source
            elif destination.endswith("/"):
                destination = destination + Path(source).name
            # create the destination path if it doesn't exist
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
        # initiate the sftp session, read data, write it to a local destination
        sftp = self.session.sftp_init()
        with sftp.open(
            source, ssh2_sftp.LIBSSH2_FXF_READ, ssh2_sftp.LIBSSH2_SFTP_S_IRUSR
        ) as remote:
            captured_data = b""
            for _rc, data in remote:
                captured_data += data
            if return_data:
                return captured_data
            destination.write_bytes(data)

    def sftp_write(self, source, destination=None, ensure_dir=True):
        """Sftp write a local file to a remote destination."""
        if not destination:
            destination = source
        elif destination.endswith("/"):
            destination = destination + Path(source).name
        data = Path(source).read_bytes()
        if ensure_dir:
            self.run(f"mkdir -p {Path(destination).absolute().parent}")
        sftp = self.session.sftp_init()
        with sftp.open(destination, FILE_FLAGS, SFTP_MODE) as remote:
            remote.write(data)

    def remote_copy(self, source, dest_host, dest_path=None, ensure_dir=True):
        """Copy a file from this host to another."""
        dest_path = dest_path or source
        sftp_down = self.session.sftp_init()
        sftp_up = dest_host.session.session.sftp_init()
        if ensure_dir:
            dest_host.session.run(f"mkdir -p {Path(dest_path).absolute().parent}")
        with sftp_down.open(
            source, ssh2_sftp.LIBSSH2_FXF_READ, ssh2_sftp.LIBSSH2_SFTP_S_IRUSR
        ) as download, sftp_up.open(dest_path, FILE_FLAGS, SFTP_MODE) as upload:
            for _size, data in download:
                upload.write(data)

    def scp_write(self, source, destination=None, ensure_dir=True):
        """SCP write a local file to a remote destination."""
        if not destination:
            destination = source
        elif destination.endswith("/"):
            destination = destination + Path(source).name
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

    def __enter__(self):
        """Return the session object."""
        return self

    def __exit__(self, *args):
        """Close the session."""
        self.session.disconnect()


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
        self._chan = channel
        if pty:
            self._chan.pty()
        self._chan.shell()

    def __enter__(self):
        """Return the shell object."""
        return self

    def __exit__(self, *exc_args):
        """Close the channel and read stdout/stderr and status."""
        self._chan.close()
        self.result = Session._read(self._chan)

    def __getattribute__(self, name):
        """Expose non-duplicate attributes from the channel."""
        try:
            return object.__getattribute__(self, name)
        except AttributeError:
            return getattr(self._chan, name)

    def send(self, cmd):
        """Send a command to the channel, ensuring a newline character."""
        if not cmd.endswith("\n"):
            cmd += "\n"
        self._chan.write(cmd)

    def stdout(self):
        """Read the contents of a channel's stdout."""
        if not self._chan.eof():
            _, data = self._chan.read(65535)
            results = data.decode("utf-8")
        else:
            results = None
            size, data = self._chan.read()
            while size > 0:
                results += data.decode("utf-8")
                size, data = self._chan.read()
        return results


class ContainerSession:
    """An approximation of ssh-based functionality from the Session class."""

    def __init__(self, cont_inst):
        self._cont_inst = cont_inst

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
            result = helpers.Result.from_duplexed_exec(result)
        else:
            result = helpers.Result.from_nonduplexed_exec(result)
        return result

    def disconnect(self):
        """Needed for simple compatability with Session."""

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

    def __enter__(self):
        """Return the session object."""
        return self

    def __exit__(self, *args):
        """Do nothing on exit."""
