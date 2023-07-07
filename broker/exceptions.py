"""A collection of Broker-specific exceptions."""
import logging

from logzero import logger


class BrokerError(Exception):
    """Base class for Broker exceptions."""

    error_code = 1

    def __init__(self, message="An unhandled exception occured!"):
        # Log the exception if the logger is set to DEBUG
        if logger.level == logging.DEBUG and isinstance(message, Exception):
            logger.exception(message)
        self.message = message
        logger.error(f"{self.__class__.__name__}: {self.message}")


class AuthenticationError(BrokerError):
    """Raised when authentication with a provider or Host fails."""

    error_code = 5


class PermissionError(BrokerError):
    """Raised when the user does not have permission to perform an action."""

    error_code = 6


class ProviderError(BrokerError):
    """Raised when a provider-specific error occurs."""

    error_code = 7

    def __init__(self, provider=None, message="Unspecified exception"):
        self.message = f"{provider} encountered the following error: {message}"
        super().__init__(message=self.message)


class ConfigurationError(BrokerError):
    """Raised when a Broker configuration error occurs."""

    error_code = 8


class NotImplementedError(BrokerError):
    """Raised when a method or function has not been implemented."""

    error_code = 9


class HostError(BrokerError):
    """Raised when a Host-specific error occurs."""

    error_code = 10

    def __init__(self, host=None, message="Unspecified exception"):
        if host:
            self.message = f"{host.hostname or host.name}: {message}"
        super().__init__(message=self.message)


class ContainerBindError(BrokerError):
    """Raised when a problem occurs at the container's bind level."""

    error_code = 11


class BeakerBindError(BrokerError):
    """Raised when a problem occurs at the Beaker bind level."""

    error_code = 12
