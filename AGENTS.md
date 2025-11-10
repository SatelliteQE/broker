# Project Overview

This project, named "Broker", is a command-line tool written in Python. It acts as an infrastructure middleman, providing a common interface to provision and manage virtual machines and containers across various services like Ansible Tower, Beaker, Docker, Podman, and OpenStack. The tool is built using the `click` library for its command-line interface and `dynaconf` for configuration management.

The core logic resides in the `Broker` class (`broker/broker.py`), which manages the lifecycle of hosts (VMs or containers). It supports checking out, checking in, extending leases, and executing arbitrary actions on these hosts. The application is designed to be extensible, with a provider-based architecture that allows for adding new services easily.

# Building and Running

## Dependencies

The project uses `uv` for dependency management. The core dependencies are listed in `pyproject.toml` and include `click`, `dynaconf`, `logzero`, `requests`, `rich`, `rich_click`, and `ruamel.yaml`. Optional dependencies for specific providers are also defined.

## Installation

To install the project and its development dependencies, run the following command:

```bash
uv pip install "broker[dev] @ ."
```

## Running the application

The main entry point for the CLI is `broker.commands:cli`. After installation, the tool can be run using the `broker` command:

```bash
broker --help
```

## Code quality checks

This project has strict code quality and formatting standards. Each change you make should ensure conformity by running the pre-commit checks.

```bash
# ensure you're in the virtual environment
source .venv/bin/activate

# run pre-commit
pre-commit run --all-files
```

## Testing

The project uses `pytest` for testing. The tests are located in the `tests/` directory. To run the unit tests, use the following command:

```bash
pytest -v tests/ --ignore tests/functional --ignore tests/test_ssh.py
```

SSH tests should only be executed when a change is made that could impact the behavior of all or a specific ssh backend (underlying ssh library; hussh, ssh2-python, paramiko). Before running the ssh-specific tests, make sure the correct dependency is installed.

```bash
uv pip install "broker[hussh] @ ."
```

Then use the following command to run the tests, targetting the specific ssh backend.

```bash
BROKER_SSH__BACKEND=hussh pytest -v tests/test_ssh.py
```

Tox is likely the best way to run the tests for this project, since it handles everything for you.

```bash
# General unit tests accross all supported python versions
tox

# Quick tests that also do linting
tox -e quick

# Functional tests for satlab (AnsibleTower)
tox -e func-satlab

# Tests for a specific ssh backend
tox -e ssh-hussh

# Run all ssh backend tests sequentially
tox -m ssh
```

# Development Conventions

*   **Linting:** The project uses `ruff` for linting. The configuration is in `pyproject.toml`.
*   **Pre-commit Hooks:** The project uses `pre-commit` to run checks before committing. The configuration is in `.pre-commit-config.yaml`.
*   **Configuration:** The application uses a `broker_settings.yaml` file for configuration. An example file is provided as `broker_settings.yaml.example`.
*   **Providers:** The provider-specific logic is located in the `broker/providers/` directory. Each provider is a class that inherits from a base provider class and implements methods for actions like `checkout`, `checkin`, etc.
*   **CLI:** The command-line interface is defined in `broker/commands.py` using `rich_click`. Commands are organized into groups for better usability.
