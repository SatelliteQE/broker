"""A collection of Broker-specific exceptions"""
from logzero import logger


class BrokerError(Exception):
    error_code = 1

    def __init__(self, message="An unhandled exception occured!"):
        if logger.level == 10 and isinstance(message, Exception):
            logger.exception(message)
        self.message = message
        logger.error(f"{self.__class__.__name__}: {self.message}")


class AuthenticationError(BrokerError):
    error_code = 5


class PermissionError(BrokerError):
    error_code = 6


class ProviderError(BrokerError):
    error_code = 7

    def __init__(self, provider=None, message="Unspecified exception"):
        self.message = f"{provider} encountered the following error: {message}"
        super().__init__(message=self.message)


class ConfigurationError(BrokerError):
    error_code = 8


class NotImplementedError(BrokerError):
    error_code = 9


class HostError(BrokerError):
    error_code = 10

    def __init__(self, host=None, message="Unspecified exception"):
        if host:
            self.message = f"{host.hostname or host.name}: {message}"
        super().__init__(message=self.message)
