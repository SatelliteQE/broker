"""Module providing classes to establish ssh or ssh-like connections to hosts.

Classes:
    Session - Wrapper around Paramiko's auth/connection system.

Note: You typically want to use a Host object instance to create sessions,
      not these classes directly.
"""

from pathlib import Path

import paramiko

from broker import exceptions, helpers


class Session:
    """Wrapper around Paramiko's auth/connection system."""

    def __init__(self, **kwargs):
        """Initialize a Session object.

        kwargs:
            hostname (str): The hostname or IP address of the remote host. Defaults to 'localhost'.
            username (str): The username to authenticate with. Defaults to 'root'.
            timeout (float): The timeout for the connection in seconds. Defaults to 60.
            port (int): The port number to connect to. Defaults to 22.
            key_filename (str): The path to the private key file to use for authentication.
            password (str): The password to use for authentication.
        """
        self.hostname = kwargs.get("hostname", "localhost")
        self.username = kwargs.get("username", "root")
        self.port = kwargs.get("port", 22)
        self.timeout = kwargs.get("timeout", 60)
        self.key_filename = kwargs.get("key_filename")
        self.password = kwargs.get("password")

        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            if self.key_filename:
                self.client.connect(
                    self.hostname,
                    port=self.port,
                    username=self.username,
                    key_filename=self.key_filename,
                    timeout=self.timeout,
                )
            elif self.password:
                self.client.connect(
                    self.hostname,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    timeout=self.timeout,
                )
            else:
                raise exceptions.AuthenticationError("No password or key file provided.")
        except paramiko.AuthenticationException as err:
            raise exceptions.AuthenticationError(f"Connection failed: {err}") from err

    def disconnect(self):
        """Disconnect session."""
        self.client.close()

    def run(self, command, timeout=0):
        """Run a command on the host and return the results."""
        timeout = None if timeout == 0 else timeout
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        stdout.channel.recv_exit_status()  # Wait for command to terminate
        return helpers.Result(
            status=stdout.channel.exit_status,
            stdout=stdout.read().decode("utf-8"),
            stderr=stderr.read().decode("utf-8"),
        )

    def scp_read(self, source, destination=None, return_data=False):
        """Read a remote file into a local destination or return a bytes object if return_data is True."""
        if return_data:
            with self.client.open_sftp() as sftp, sftp.open(source, "rb") as remote_file:
                return remote_file.read()
        else:
            destination = destination or source
            with self.client.open_sftp() as sftp:
                sftp.get(source, destination)

    def scp_write(self, source, destination=None, ensure_dir=True):
        """Write a local file to a remote destination using SCP."""
        destination = destination or source
        if ensure_dir:
            self.run(f"mkdir -p {Path(destination).parent}")
        with self.client.open_sftp() as sftp:
            sftp.put(source, destination)

    def sftp_read(self, source, destination=None, return_data=False):
        """Read a remote file into a local destination or return a bytes object if return_data is True."""
        if return_data:
            with self.client.open_sftp() as sftp, sftp.open(source, "rb") as remote_file:
                return remote_file.read()
            destination = destination or source
            with self.client.open_sftp() as sftp:
                sftp.get(source, destination)

    def sftp_write(self, source, destination=None, ensure_dir=True):
        """Write a local file to a remote destination using SFTP."""
        destination = destination or source
        if ensure_dir:
            self.run(f"mkdir -p {Path(destination).parent}")
        with self.client.open_sftp() as sftp:
            sftp.put(source, destination)

    def shell(self, pty=False):
        """Create and return an interactive shell instance."""
        transport = self.client.get_transport()
        channel = transport.open_session()
        if pty:
            channel.get_pty()
        channel.invoke_shell()
        return InteractiveShell(channel)


class InteractiveShell:
    """A helper class that provides an interactive shell interface."""

    def __init__(self, channel):
        self._channel = channel

    def __enter__(self):
        """Return the shell object."""
        return self

    def __exit__(self, *exc_args):
        """Close the channel."""
        self._channel.close()

    def send(self, cmd):
        """Send a command to the channel, ensuring a newline character."""
        if not cmd.endswith("\n"):
            cmd += "\n"
        self._channel.send(cmd)

    def stdout(self):
        """Read the contents of a channel's stdout."""
        return self._channel.recv(1024).decode("utf-8")  # Adjust buffer size as needed
