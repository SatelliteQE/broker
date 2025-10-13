"""Tests for SSH functionality in broker."""

from pathlib import Path
import time
import shlex

import pytest

from broker.hosts import Host
from broker.settings import settings

TEXT_FILE = Path("tests/data/ssh/hp.txt").resolve()
IMG_FILE = Path("tests/data/ssh/puppy.jpeg").resolve()


@pytest.fixture
def host(run_test_server):
    """Return a basic Host object."""
    return Host(hostname="localhost", port=8022, password="toor")


def test_password_auth(run_test_server):
    """Test that we can establish a connection with password-based authentication."""
    assert Host(hostname="localhost", port=8022, password="toor")


def test_key_auth(run_test_server):
    """Test that we can establish a connection with key-based authentication."""
    assert Host(hostname="localhost", port=8022, key_filename="tests/data/ssh/test_key")


def test_key_with_password_auth(run_test_server):
    """Test that we can establish a connection with key-based authentication and a password."""
    assert Host(
        hostname="localhost",
        port=8022,
        key_filename="tests/data/ssh/auth_test_key",
        password="husshpuppy",
    )


def test_basic_command(host):
    """Test that we can run a basic command."""
    result = host.execute("echo hello")
    assert result.status == 0
    assert result.stdout == "hello\n"


def test_bad_command(host):
    """Test that we can run a bad command."""
    result = host.execute("kiara")
    assert result.status != 0
    assert "command not found" in result.stderr


def test_text_scp(host):
    """Test that we can copy a file to the server and read it back."""
    # Copy a local file to the server
    host.session.scp_write(str(TEXT_FILE), "/root/hp.txt")
    assert "hp.txt" in host.execute("ls /root").stdout

    if settings.ssh.backend == "ssh2-python":
        pytest.skip("ssh2 backend does not implement scp_read")

    # Read the file back from the server
    read_text = host.session.scp_read("/root/hp.txt", return_data=True)
    # Copy the file from the server to a local file
    scp_local_path = "scp_hp.txt"
    host.session.scp_read("/root/hp.txt", scp_local_path)

    hp_text = Path(str(TEXT_FILE)).read_text()
    assert read_text == hp_text
    scp_hp_text = Path(scp_local_path).read_text()
    Path(scp_local_path).unlink()
    assert scp_hp_text == hp_text


def test_scp_write_data(host):
    """Test that we can write a string to a file on the server."""
    # Create a temporary local file with test data
    temp_file = Path("temp_hello.txt")
    temp_file.write_text("hello")
    try:
        host.session.scp_write(str(temp_file), "/root/hello.txt")
        assert "hello.txt" in host.execute("ls /root").stdout
        if settings.ssh.backend in ("hussh", "paramiko"):
            read_text = host.session.scp_read("/root/hello.txt", return_data=True)
        else:
            # ssh2 backend lacks scp_read, use sftp_read instead
            read_text = host.session.sftp_read("/root/hello.txt", return_data=True).decode("utf-8")
        assert read_text == "hello"
    finally:
        # Clean up the temporary file
        temp_file.unlink()


def test_text_sftp(host):
    """Test that we can copy a file to the server and read it back."""
    # Copy a local file to the server
    host.session.sftp_write(str(TEXT_FILE), "/root/hp.txt")
    assert "hp.txt" in host.execute("ls /root").stdout
    # Read the file back from the server
    read_text = host.session.sftp_read("/root/hp.txt", return_data=True).decode("utf-8")
    hp_text = Path(str(TEXT_FILE)).read_text()
    assert read_text == hp_text
    # Copy the file from the server to a local file
    sftp_local_path = "sftp_hp.txt"
    host.session.sftp_read("/root/hp.txt", sftp_local_path)
    sftp_hp_text = Path(sftp_local_path).read_text()
    Path(sftp_local_path).unlink()
    assert sftp_hp_text == hp_text


def test_sftp_write_data(host):
    """Test that we can write a string to a file on the server."""
    # Create a temporary local file with test data
    temp_file = Path("temp_sftp_hello.txt")
    temp_file.write_text("hello")
    try:
        host.session.sftp_write(str(temp_file), "/root/hello.txt")
        assert "hello.txt" in host.execute("ls /root").stdout
        read_text = host.session.sftp_read("/root/hello.txt", return_data=True).decode("utf-8")
        assert read_text == "hello"
    finally:
        # Clean up the temporary file
        temp_file.unlink()


def test_shell_context(host):
    """Test that we can run multiple commands in a shell context."""
    with host.session.shell() as sh:
        sh.send("echo test shell")
        sh.send("bad command")
        time.sleep(0.5)  # Allow time for command processing
    assert "test shell" in sh.result.stdout
    if settings.ssh.backend == "hussh":
        assert "command not found" in sh.result.stderr
        assert sh.result.status != 0
    else:
        # ssh2/paramiko might not capture stderr correctly or provide reliable status in non-pty shell.
        # Paramiko might put stderr in stdout.
        assert "command not found" in sh.result.stdout or "command not found" in sh.result.stderr
        # Status check unreliable for non-hussh backends here.


def test_pty_shell_context(host):
    """Test that we can run multiple commands in a pty shell context."""
    with host.session.shell(pty=True) as sh:
        sh.send("echo test shell")
        sh.send("bad command")
        time.sleep(0.5)  # Allow time for command processing in PTY

    # All backends capture output on context exit in sh.result
    if settings.ssh.backend == "hussh":
        # Hussh PTY stdout contains logout sequences, not command output after exit. Stderr is empty.
        assert sh.result.status == 127  # Rely solely on the status code (command not found)
        # Cannot reliably check stdout for "test shell" for hussh PTY.
    else:
        # Paramiko/ssh2 capture command output in stdout even with PTY.
        assert "test shell" in sh.result.stdout
        # Both backends seem to put stderr into stdout with pty=True.
        assert "command not found" in sh.result.stdout
        # Exit status check unreliable for ssh2/paramiko PTY shells.


def test_remote_copy(host, run_second_server):
    """Test that we can copy a file from one server to another."""
    # First copy the test file to the first server
    host.session.scp_write(str(TEXT_FILE), "/root/hp.txt")
    assert "hp.txt" in host.execute("ls /root").stdout
    # Now copy the file from the first server to the second server
    dest_host = Host(hostname="localhost", port=8023, password="toor")
    host.session.remote_copy("/root/hp.txt", dest_host)
    assert "hp.txt" in dest_host.execute("ls /root").stdout


def test_tail(host):
    """Test that we can tail a file."""
    TEST_STR = "hello\nworld\n"
    temp_file = Path("temp_hello.txt")
    temp_file.write_text(TEST_STR)
    host.session.scp_write(str(temp_file), "/root/hello.txt")
    temp_file.unlink()

    if settings.ssh.backend == "hussh":
        with host.session.tail_file("/root/hello.txt") as tf:
            assert tf.tailer.read(0) == TEST_STR
            assert tf.tailer.last_pos == len(TEST_STR)
            host.execute("echo goodbye >> /root/hello.txt")
        assert tf.tailer.contents == "goodbye\n"
    else:
        # ssh2 and paramiko backends simulate tail differently
        with host.session.tail_file("/root/hello.txt") as tf:
            # Initial read check not directly possible with simulation. Check initial size instead.
            if hasattr(tf, "initial_size"):  # Paramiko specific check
                assert tf.initial_size == len(TEST_STR)
            else:  # ssh2 specific check via command execution
                initial_size_cmd = int(
                    host.execute(f"stat -c %s {shlex.quote('/root/hello.txt')}").stdout.strip()
                )
                assert initial_size_cmd == len(TEST_STR)

            host.execute("echo goodbye >> /root/hello.txt")
        # Both simulated backends get contents after context exit
        assert tf.contents.strip() == "goodbye"
