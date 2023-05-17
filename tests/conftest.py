import os
import pytest
from broker.settings import settings_path, inventory_path, load_settings
import shutil


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

@pytest.fixture(scope="module")
def temp_settings_and_inventory():
    """Temporarily move the local inventory and settings files, then move them back when done"""
    inv_backup_path = None
    if inventory_path.exists():
        inv_backup_path = inventory_path.rename(f"{inventory_path.absolute()}.bak")
    settings_backup_path = settings_path.rename(f"{settings_path.absolute()}.bak")
    shutil.copyfile('tests/data/broker_settings.yaml', f'{settings_path.parent}/broker_settings.yaml')
    inventory_path.touch()
    load_settings(settings_path)
    yield
    inventory_path.unlink()
    settings_path.unlink()
    if inv_backup_path:
        inv_backup_path.rename(inventory_path)
    settings_backup_path.rename(settings_path)
    load_settings(settings_path)
