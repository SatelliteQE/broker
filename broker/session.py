import os
import socket
import tempfile
from pathlib import Path
from logzero import logger
from ssh2.session import Session as ssh2_Session
from ssh2 import sftp as ssh2_sftp
from broker import helpers

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
        helpers.simple_retry(sock.connect, [(host, port)])
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
        return helpers.Result.from_ssh(
            stdout=results,
            channel=channel,
        )

    def run(self, command, timeout=0):
        """run a command on the host and return the results"""
        self.session.set_timeout(helpers.translate_timeout(timeout))
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
        elif destination.endswith("/"):
            destination = destination + Path(source).name
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

    def sftp_write(self, source, destination=None, ensure_dir=True):
        """sftp write a local file to a remote destination"""
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

    def remote_copy(self, source, dest_host, ensure_dir=True):
        """Copy a file from this host to another"""
        sftp_down = self.session.sftp_init()
        sftp_up = dest_host.session.session.sftp_init()
        if ensure_dir:
            dest_host.run(f"mkdir -p {Path(source).absolute().parent}")
        with sftp_down.open(
            source, ssh2_sftp.LIBSSH2_FXF_READ, ssh2_sftp.LIBSSH2_SFTP_S_IRUSR
        ) as download:
            with sftp_up.open(source, FILE_FLAGS, SFTP_MODE) as upload:
                for size, data in download:
                    upload.write(data)

    def scp_write(self, source, destination=None, ensure_dir=True):
        """scp write a local file to a remote destination"""
        if not destination:
            destination = source
        elif destination.endswith("/"):
            destination = destination + Path(source).name
        fileinfo = os.stat(source)
        chan = self.session.scp_send64(
            destination,
            fileinfo.st_mode & 0o777,
            fileinfo.st_size,
            fileinfo.st_mtime,
            fileinfo.st_atime,
        )
        if ensure_dir:
            self.run(f"mkdir -p {Path(destination).absolute().parent}")
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


class ContainerSession:
    """An approximation of ssh-based functionality from the Session class"""

    def __init__(self, cont_inst):
        self._cont_inst = cont_inst

    def run(self, command, demux=True, **kwargs):
        """This is the container approximation of Session.run"""
        kwargs.pop("timeout", None)  # Timeouts are set at the client level
        kwargs["demux"] = demux
        if "'" in command:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sh") as tmp:
                tmp.write(command)
                tmp.seek(0)
                command = f"/bin/bash {tmp.name}"
                self.sftp_write(tmp.name)
        if any([s in command for s in "|&><"]):
            # Containers don't handle pipes, redirects, etc well in a bare exec_run
            command = f"/bin/bash -c '{command}'"
        result = self._cont_inst._cont_inst.exec_run(command, **kwargs)
        if demux:
            result = helpers.Result.from_duplexed_exec(result)
        else:
            result = helpers.Result.from_nonduplexed_exec(result)
        return result

    def disconnect(self):
        """Needed for simple compatability with Session"""
        pass

    def sftp_write(self, source, destination=None, ensure_dir=True):
        """Add one of more files to the container"""
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
            logger.debug(
                f"{self._cont_inst.hostname} adding file(s) {source} to {destination}"
            )
            if ensure_dir:
                if destination.endswith("/"):
                    self.run(f"mkdir -m 666 -p {destination}")
                else:
                    self.run(f"mkdir -m 666 -p {Path(destination).parent}")
            self._cont_inst._cont_inst.put_archive(str(destination), tar.read_bytes())

    def sftp_read(self, source, destination=None):
        """Get a file or directory from the container"""
        destination = Path(destination or source)
        logger.debug(
            f"{self._cont_inst.hostname} getting file {source} from {destination}"
        )
        data, status = self._cont_inst._cont_inst.get_archive(source)
        logger.debug(f"{self._cont_inst.hostname}: {status}")
        all_data = b"".join(d for d in data)
        if destination.name == "_raw":
            return all_data
        with helpers.data_to_tempfile(all_data, as_tar=True) as tar:
            logger.debug(f"Extracting {source} to {destination}")
            tar.extractall(destination.parent if destination.is_file() else destination)

    def shell(self, pty=False):
        """Create and return an interactive shell instance"""
        raise NotImplementedError("ContainerSession.shell has not been implemented")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass
