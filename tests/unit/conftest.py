import sys
import shutil

def pytest_configure(config):
    from broker.settings import settings_path, inventory_path
    inv_backup_path = None
    if inventory_path.exists():
        inv_backup_path = inventory_path.rename(f"{inventory_path.absolute()}.bak")
    settings_backup_path = settings_path.rename(f"{settings_path.absolute()}.bak")
    shutil.copyfile('tests/data/broker_settings.yaml', f'{settings_path.parent}/broker_settings.yaml')
    inventory_path.touch()
    del sys.modules['broker.settings']
    yield
    inventory_path.unlink()
    settings_path.unlink()
    if inv_backup_path:
        inv_backup_path.rename(inventory_path)
    settings_backup_path.rename(settings_path)
