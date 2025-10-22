"""Module providing classes to establish ssh or ssh-like connections to hosts.

Classes:
    Session - Wrapper around Paramiko's auth/connection system.

Note: You typically want to use a Host object instance to create sessions,
      not these classes directly.
"""

from contextlib import contextmanager
from pathlib import Path
import shlex
import socket

import paramiko

from broker import exceptions, helpers


class Session:
    """Wrapper around Paramiko's auth/connection system."""

    def _load_private_key(self, key_filename, password):
        """Attempt to load a private key file, trying various types."""
        key_path_str = str(Path(key_filename).expanduser().resolve())
        key_types = [
            paramiko.RSAKey,
            paramiko.ECDSAKey,
            paramiko.Ed25519Key,
        ]
        last_exception = None

        for key_type in key_types:
            try:
                return key_type.from_private_key_file(key_path_str, password=password)
            except paramiko.PasswordRequiredException:  # noqa: PERF203
                # If password is required but not provided (or incorrect), raise immediately
                raise exceptions.AuthenticationError(
                    f"Password required or incorrect for key file: {key_path_str}"
                )
            except paramiko.SSHException as e:
                # Store the exception and try the next key type
                last_exception = e
                continue  # Try next key type

        # If all key types failed
        raise exceptions.AuthenticationError(
            f"Unsupported or invalid key file: {key_path_str} - {last_exception}"
        ) from last_exception

    def _authenticate_with_agent(self):
        """Attempt authentication using the SSH agent."""
        try:
            agent = paramiko.Agent()
            agent_keys = agent.get_keys()
            if not agent_keys:
                raise exceptions.AuthenticationError("Agent available but has no keys.")
            for key in agent_keys:
                try:
                    self.transport.auth_publickey(self.username, key)
                    if self.transport.is_authenticated():
                        return  # Success
                except paramiko.AuthenticationException:  # noqa: PERF203
                    continue  # Try next agent key
            # If loop finishes without authenticating
            raise exceptions.AuthenticationError("Agent authentication failed (tried all keys).")
        except Exception as e:  # Catch agent connection errors, etc.
            raise exceptions.AuthenticationError(f"Agent authentication failed: {e}") from e

    def __init__(self, **kwargs):  # noqa: PLR0912, PLR0915 - TODO: refactor?
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

        try:
            # Establish socket connection
            sock = socket.create_connection((self.hostname, self.port), self.timeout)
        except OSError as e:
            raise exceptions.ParamikoBindError(f"Failed to connect socket: {e}") from e

        self.transport = paramiko.Transport(sock)
        try:
            # Start SSH transport, potentially verifying host key
            self.transport.start_client(timeout=self.timeout)
            self.transport.set_keepalive(30)  # Set keepalive interval

        except paramiko.SSHException as e:
            self.transport.close()
            raise exceptions.ParamikoBindError(f"Failed to start SSH transport: {e}") from e

        # Load host keys and implement AutoAddPolicy behavior
        try:
            host_keys_path = Path("~/.ssh/known_hosts").expanduser().resolve()
            host_keys = paramiko.util.load_host_keys(str(host_keys_path))
            server_key = self.transport.get_remote_server_key()
            key_entry = host_keys.lookup(self.hostname)

            if not key_entry or key_entry.get(server_key.get_name()) != server_key:
                # Auto-accept unknown host keys (like AutoAddPolicy)
                # This is appropriate for test environments and development
                host_keys.add(self.hostname, server_key.get_name(), server_key)
                try:
                    # Try to save the host key to known_hosts
                    host_keys_path.parent.mkdir(parents=True, exist_ok=True)
                    host_keys.save(str(host_keys_path))
                except (OSError, PermissionError):
                    # If we can't save, that's okay - proceed anyway
                    pass

        except FileNotFoundError:
            # known_hosts file doesn't exist, which is fine for first connection
            # Auto-accept the host key
            try:
                host_keys_path.parent.mkdir(parents=True, exist_ok=True)
                server_key = self.transport.get_remote_server_key()
                host_keys = paramiko.HostKeys()
                host_keys.add(self.hostname, server_key.get_name(), server_key)
                host_keys.save(str(host_keys_path))
            except (OSError, PermissionError):
                # If we can't save, that's okay - proceed anyway
                pass
        except (OSError, PermissionError, paramiko.SSHException):
            # For any other host key related errors, just proceed
            # This ensures compatibility with test environments
            pass

        # Attempt authentication
        auth_method = "none"
        try:
            if self.key_filename:
                auth_method = "key"
                key = self._load_private_key(self.key_filename, self.password)
                self.transport.auth_publickey(self.username, key)

            elif self.password:
                auth_method = "password"
                self.transport.auth_password(self.username, self.password)
            else:
                # Try agent authentication if no key/password provided
                auth_method = "agent"
                self._authenticate_with_agent()

            if not self.transport.is_authenticated():
                # This path should ideally not be reached if helpers raise correctly.
                raise exceptions.AuthenticationError(
                    f"{auth_method.capitalize()} authentication failed."
                )

            # Create an SSHClient interface attached to the authenticated transport
            self.client = paramiko.SSHClient()
            self.client._transport = self.transport  # Attach transport

        except (paramiko.AuthenticationException, exceptions.AuthenticationError) as err:
            self.transport.close()
            # Re-raise specific AuthenticationError or a general one if needed
            if isinstance(err, exceptions.AuthenticationError):
                raise  # Propagate our specific error
            else:
                raise exceptions.AuthenticationError(
                    f"{auth_method.capitalize()} authentication failed: {err}"
                ) from err
        except (
            FileNotFoundError
        ) as err:  # Should be caught by _load_private_key now, but keep for safety
            self.transport.close()
            raise exceptions.AuthenticationError(
                f"Key file not found: {self.key_filename}"
            ) from err
        except Exception as err:  # Catch-all for unexpected auth errors
            self.transport.close()
            raise exceptions.ParamikoBindError(
                f"Unexpected error during SSH {auth_method} auth: {err}"
            ) from err

    def disconnect(self):
        """Disconnect session."""
        if (
            hasattr(self, "transport") and self.transport and self.transport.is_active()
        ):  # Use transport directly
            self.transport.close()
        if hasattr(self, "client"):  # Ensure client is cleaned up
            self.client = None  # Or del self.client?

    def run(self, command, timeout=0):
        """Run a command on the host and return the results."""
        timeout = None if timeout == 0 else timeout
        _, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        stdout.channel.recv_exit_status()  # Wait for command termination
        return helpers.Result(
            status=stdout.channel.exit_status,
            stdout=stdout.read().decode("utf-8"),
            stderr=stderr.read().decode("utf-8"),
        )

    def scp_read(self, source, destination=None, return_data=False):
        """Read a remote file into a local destination or return a string if return_data is True."""
        if not return_data:  # Ensure local destination path exists if writing to file
            destination = destination or source
            Path(destination).parent.mkdir(parents=True, exist_ok=True)

        with self.client.open_sftp() as sftp:
            if return_data:
                with sftp.open(source, "rb") as remote_file:
                    return remote_file.read().decode("utf-8")
            else:
                sftp.get(source, destination)

    def scp_write(self, source, destination=None, ensure_dir=True):
        """Write a local file to a remote destination using SCP (via SFTP)."""
        destination = destination or source
        if ensure_dir:
            self.run(
                f"mkdir -p {shlex.quote(str(Path(destination).parent))}"
            )  # Use shlex.quote for safety
        with self.client.open_sftp() as sftp:
            sftp.put(source, destination)

    def sftp_read(self, source, destination=None, return_data=False):
        """Read a remote file into a local destination or return a bytes object if return_data is True."""
        if not return_data:  # Ensure local destination path exists if writing to file
            destination = destination or source
            Path(destination).parent.mkdir(parents=True, exist_ok=True)

        with self.client.open_sftp() as sftp:
            if return_data:
                with sftp.open(source, "rb") as remote_file:
                    return remote_file.read()
            else:
                sftp.get(source, destination)

    def sftp_write(self, source, destination=None, ensure_dir=True):
        """Write a local file to a remote destination using SFTP."""
        destination = destination or source
        if ensure_dir:
            self.run(
                f"mkdir -p {shlex.quote(str(Path(destination).parent))}"
            )  # Use shlex.quote for safety
        with self.client.open_sftp() as sftp:
            sftp.put(source, destination)

    def remote_copy(self, source, dest_host, dest_path=None, ensure_dir=True):
        """Copy a file from this host to another using SFTP."""
        dest_path = dest_path or source
        if ensure_dir:  # Ensure the destination directory exists on the dest_host
            dest_host.session.run(
                f"mkdir -p {shlex.quote(str(Path(dest_path).parent))}"
            )  # Use shlex.quote for safety

        try:
            # Use file-like objects with consolidated 'with' statement
            with (
                self.client.open_sftp() as sftp_source,
                dest_host.session.client.open_sftp() as sftp_dest,
                sftp_source.open(source, "rb") as source_file,
                sftp_dest.open(dest_path, "wb") as dest_file,
            ):
                # Note: Paramiko SFTP lacks direct copy_file_to_fileobj; manually set permissions if needed.
                dest_file.set_pipelined(True)  # May improve speed
                # Read and write in chunks
                while True:
                    chunk = source_file.read(32768)  # 32KB chunks
                    if not chunk:
                        break
                    dest_file.write(chunk)
                # Attempt to copy permissions
                try:
                    stat_info = sftp_source.stat(source)
                    sftp_dest.chmod(dest_path, stat_info.st_mode)
                except Exception as e:
                    # Raise exception instead of printing
                    raise exceptions.ParamikoBindError(
                        f"Could not set permissions on remote copy {dest_path}: {e}"
                    ) from e

        except Exception as e:
            # Catch potential SFTP errors or other issues during copy
            if isinstance(e, exceptions.ParamikoBindError):
                raise  # Propagate the specific permission error
            else:
                raise exceptions.ParamikoBindError(f"Remote copy failed: {e}") from e

    def shell(self, pty=False):
        """Create and return an interactive shell instance."""
        channel = self.transport.open_session()  # Use transport directly
        if pty:
            channel.get_pty()  # Request PTY
        channel.invoke_shell()
        return InteractiveShell(channel)

    @contextmanager
    def tail_file(self, filename):
        """Simulate tailing a file on the remote host using SFTP stat and exec_command."""
        initial_size = -1
        try:
            with self.client.open_sftp() as sftp:
                initial_size = sftp.stat(filename).st_size
        except Exception as e:  # Handle SFTP errors (e.g., file not found)
            raise exceptions.ParamikoBindError(f"Could not get initial size for tail: {e}") from e

        tailer = FileTailer(initial_size=initial_size)  # Helper object to store results
        yield tailer

        # After the 'with' block, get the newly added content
        if initial_size != -1:
            # Use tail command to get content added after initial_size
            # Add 1 because tail -c +N starts from Nth byte (1-based)
            command = (
                f"tail -c +{initial_size + 1} {shlex.quote(filename)}"  # Use shlex.quote for safety
            )
            result = self.run(command)
            if result.status == 0:
                tailer.contents = result.stdout
            else:
                tailer.contents = ""  # Return empty on failure (alternative: None or raise)
        else:  # If initial stat failed
            tailer.contents = ""


class FileTailer:
    """Helper class to store results for simulated tail."""

    def __init__(self, initial_size):
        self.initial_size = initial_size
        self.contents = ""  # Content captured after context exit


class InteractiveShell:
    """A helper class that provides an interactive shell interface."""

    def __init__(self, channel):
        self._channel = channel
        self.stdout_data = ""  # Captured stdout
        self.stderr_data = ""  # Captured stderr (often merged in PTY)
        self.status = -1  # Status not reliably available via invoke_shell

    def __enter__(self):
        """Return the shell object."""
        return self

    def __exit__(self, *exc_args):
        """Close the channel and capture output."""
        # Try to capture remaining output non-blockingly
        while self._channel.recv_ready():
            self.stdout_data += self._channel.recv(1024).decode("utf-8", errors="ignore")
        while self._channel.recv_stderr_ready():
            self.stderr_data += self._channel.recv_stderr(1024).decode("utf-8", errors="ignore")

        # Exit status isn't reliably available after invoke_shell without sending 'exit'
        # and potentially blocking. Leaving status as -1.
        # self.status = self._channel.recv_exit_status()

        self._channel.close()
        # Store captured data in a Result-like object
        self.result = helpers.Result(
            status=self.status,  # Likely -1
            stdout=self.stdout_data,
            stderr=self.stderr_data,
        )

    def send(self, cmd):
        """Send a command to the channel, ensuring a newline character."""
        if not cmd.endswith("\n"):
            cmd += "\n"
        self._channel.send(cmd)
        # Avoid reading immediately after send to prevent blocking; collect output in __exit__.

    # Output is collected on exit, no separate stdout/stderr methods needed.
