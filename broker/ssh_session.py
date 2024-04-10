"""Module providing base SSH methods and classes."""
import socket

from logzero import logger

from broker import exceptions


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
                # FIXME this socket was created for AF_INET6. We shouldn't reuse it with ipv6=False.
                return _create_connect_socket(host, port, timeout, ipv6=False, sock=sock)
            else:
                raise exceptions.ConnectionError(
                    f"Unable to establish IPv6 connection to {host}."
                ) from err
    else:
        sock.connect((host, port))
    return sock, ipv6
