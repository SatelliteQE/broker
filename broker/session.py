import os
import socket
from pathlib import Path
from logzero import logger
from ssh2.session import Session as ssh2_Session
from ssh2 import sftp as ssh2_sftp
from broker.helpers import simple_retry, translate_timeout, Result

SESSIONS = {}

SFTP_MODE = (
    ssh2_sftp.LIBSSH2_SFTP_S_IRUSR
    | ssh2_sftp.LIBSSH2_SFTP_S_IWUSR
    | ssh2_sftp.LIBSSH2_SFTP_S_IRGRP
    | ssh2_sftp.LIBSSH2_SFTP_S_IROTH
)
FILE_FLAGS = ssh2_sftp.LIBSSH2_FXF_CREAT | ssh2_sftp.LIBSSH2_FXF_WRITE


class AuthException(Exception):
    pass


class Session:
    def __init__(self, **kwargs):
        """Wrapper around ssh2-python's auth/connection system"""
        host = kwargs.get("hostname", "localhost")
        user = kwargs.get("username", "root")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(kwargs.get("timeout"))
        port = kwargs.get("port", 22)
        key_filename = kwargs.get("key_filename")
        simple_retry(sock.connect, [(host, port)])
        self.session = ssh2_Session()
        self.session.handshake(sock)
        if key_filename:
            if not Path(key_filename).exists():
                raise FileNotFoundError(f"Key not found in '{key_filename}'")
            self.session.userauth_publickey_fromfile(user, key_filename)
        elif kwargs.get("password"):
            self.session.userauth_password(user, kwargs["password"])
        else:
            raise AuthException("No password or key file provided.")

    @staticmethod
    def _read(channel):
        """read the contents of a channel"""
        size, data = channel.read()
        results = ""
        while size > 0:
            try:
                results += data.decode("utf-8")
            except UnicodeDecodeError as err:
                logger.error(f"Skipping data chunk due to {err}\nReceived: {data}")
            size, data = channel.read()
        return Result.from_ssh(
            stdout=results,
            channel=channel,
        )

    def run(self, command, timeout=0):
        """run a command on the host and return the results"""
        self.session.set_timeout(translate_timeout(timeout))
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
        """Create and return an interactive shell instance"""
        channel = self.session.open_session()
        return InteractiveShell(channel, pty)

    def sftp_read(self, source, destination=None):
        """read a remote file into a local destination"""
        if not destination:
            destination = source
        # create the destination path if it doesn't exist
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.touch()
        # initiate the sftp session, read data, write it to a local destination
        sftp = self.session.sftp_init()
        with sftp.open(
            source, ssh2_sftp.LIBSSH2_FXF_READ, ssh2_sftp.LIBSSH2_SFTP_S_IRUSR
        ) as remote:
            with destination.open("wb") as local:
                for size, data in remote:
                    local.write(data)

    def sftp_write(self, source, destination=None):
        """sftp write a local file to a remote destination"""
        if not destination:
            destination = source
        data = Path(source).read_bytes()
        sftp = self.session.sftp_init()
        with sftp.open(destination, FILE_FLAGS, SFTP_MODE) as remote:
            remote.write(data)

    def remote_copy(self, source, dest_host):
        """Copy a file from this host to another"""
        sftp_down = self.session.sftp_init()
        sftp_up = dest_host.session.session.sftp_init()
        with sftp_down.open(
            source, ssh2_sftp.LIBSSH2_FXF_READ, ssh2_sftp.LIBSSH2_SFTP_S_IRUSR
        ) as download:
            with sftp_up.open(source, FILE_FLAGS, SFTP_MODE) as upload:
                for size, data in download:
                    upload.write(data)

    def scp_write(self, source, destination=None):
        """scp write a local file to a remote destination"""
        if not destination:
            destination = source
        fileinfo = os.stat(source)
        chan = self.session.scp_send64(
            destination,
            fileinfo.st_mode & 0o777,
            fileinfo.st_size,
            fileinfo.st_mtime,
            fileinfo.st_atime,
        )
        with open(source, "rb") as local:
            for data in local:
                chan.write(data)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.session.disconnect()


class InteractiveShell:
    """A helper class that provides an interactive shell interface

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
        return self

    def __exit__(self, *exc_args):
        """Close the channel and read stdout/stderr and status"""
        self._chan.close()
        self.result = Session._read(self._chan)

    def __getattribute__(self, name):
        """Expose non-duplicate attributes from the channel"""
        try:
            return object.__getattribute__(self, name)
        except AttributeError:
            return getattr(self._chan, name)

    def send(self, cmd):
        """Send a command to the channel, ensuring a newline character"""
        if not cmd.endswith("\n"):
            cmd += "\n"
        self._chan.write(cmd)

    def stdout(self):
        """read the contents of a channel's stdout"""
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
