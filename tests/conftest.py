import os
import pytest


def pytest_sessionstart(session):
    """Put Broker into test mode."""
    os.environ["BROKER_TEST_MODE"] = "True"


@pytest.fixture
def set_envars(request):
    """Set and unset one or more environment variables"""
    if isinstance(request.param, list):
        for pair in request.param:
            os.environ[pair[0]] = pair[1]
        yield
        for pair in request.param:
            del os.environ[pair[0]]
    else:
        os.environ[request.param[0]] = request.param[1]
        yield
        del os.environ[request.param[0]]
