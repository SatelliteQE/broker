import os
import socket
from ssh2.session import Session as ssh2_Session
from ssh2 import sftp as ssh2_sftp

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


class Result:
    """Dummy result class for presenting results in dot access"""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __repr__(self):
        return getattr(self, "stdout")


class Session:
    def __init__(self, **kwargs):
        """Wrapper around ssh2-python's auth/connection system"""
        host = kwargs.get("hostname", "localhost")
        user = kwargs.get("username", "root")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, kwargs.get("port", 22)))
        self.session = ssh2_Session()
        self.session.handshake(sock)
        if kwargs.get("password"):
            self.session.userauth_password(user, kwargs["password"])
        elif kwargs.get("key_filename"):
            self.session.userauth_publickey_fromfile(user, kwargs["key_filename"])
        else:
            raise AuthException("No password or key file provided.")

    def _read(self, channel):
        """read the contents of a channel"""
        size, data = channel.read()
        results = ""
        while size > 0:
            results += data.decode("utf-8")
            size, data = channel.read()
        return Result(
            stdout=results,
            status=channel.get_exit_status(),
            stderr=channel.read_stderr(),
        )

    def run(self, command):
        """run a command on the host and return the results"""
        channel = self.session.open_session()
        channel.execute(command)
        results = self._read(channel)
        channel.close()
        return results

    def sftp_read(self, source, destination=None):
        """read a remote file into a local destination"""
        if not destination:
            destination = source
        sftp = self.session.sftp_init()
        with sftp.open(
            source, ssh2_sftp.LIBSSH2_FXF_READ, ssh2_sftp.LIBSSH2_SFTP_S_IRUSR
        ) as remote:
            with open(destination, "wb") as local:
                for size, data in remote:
                    local.write(data)

    def sftp_write(self, source, destination=None):
        """sftp write a local file to a remote destination"""
        if not destination:
            destination = source
        sftp = self.session.sftp_init()
        with open(source, "rb") as local:
            with sftp.open(destination, FILE_FLAGS, SFTP_MODE) as remote:
                for data in local:
                    remote.write(data)

    def scp_write(self, source, destination=None):
        """scp write a local file to a remote destination"""
        if not destination:
            destination = source
        fileinfo = os.stat(args.source)
        chan = s.scp_send64(
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
