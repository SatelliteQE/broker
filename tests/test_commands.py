"""Test file for broker.commands module helpers."""

import pytest
from broker.commands import _handle_ambiguous_names


@pytest.mark.parametrize("selection", [["--all"], ["0", "1"], ["good.example.com", "bad.example.com"]])
@pytest.mark.parametrize("named", [True, False])
def test_checkin_partial_failure(broker_settings, monkeypatch, tmp_path, selection, named):
    """CLI checkin reports failure after saving successful releases to inventory."""
    from click.testing import CliRunner
    from broker import Broker, helpers, settings
    from broker.commands import cli
    from broker.providers.test_provider import TestProvider

    monkeypatch.setattr(settings, "inventory_path", tmp_path / "inventory.yaml")
    monkeypatch.setattr("broker.broker.clone_global_settings", lambda: broker_settings)
    inventory = [
        {"name": name, "hostname": f"{name}.example.com", "_broker_provider": "TestProvider"}
        for name in ("good", "bad")
    ]
    if not named:
        for host in inventory:
            host.pop("name")
    helpers.update_inventory(add=inventory)

    def release(provider, host):
        if host.hostname == "bad.example.com":
            raise Broker.ProviderError("TestProvider", "release failed")

    monkeypatch.setattr(TestProvider, "release", release)
    result = CliRunner().invoke(cli, ["checkin", *selection])

    assert result.exit_code == Broker.BrokerError.error_code
    assert helpers.load_inventory() == [inventory[1]]
    assert "BrokerError" in result.output


def test_handle_ambiguous_names_with_interrupt_resume(monkeypatch, capsys):
    """Test that _handle_ambiguous_names retries prompt when InterruptResumeError is raised."""
    from broker import exceptions
    from broker.config_manager import ConfigManager

    # Setup test data
    filtered_scenarios = [
        {"path": "scenario1.yaml"},
        {"path": "scenario2.yaml"},
    ]
    name = "test"
    category = None
    import_all = False

    # Enable interactive mode for this test
    original_interactive = ConfigManager.interactive_mode
    ConfigManager.interactive_mode = True

    try:
        # Track how many times the prompt is called
        call_count = 0

        def mock_prompt(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: simulate keyboard interrupt with resume
                raise exceptions.InterruptResumeError()
            else:
                # Second call: user selects first scenario
                return 1

        # Patch both the module's click.prompt and the imported one in commands
        monkeypatch.setattr("broker.commands.click.prompt", mock_prompt)

        # Call the function - should retry after InterruptResumeError
        result_scenarios, result_paths, should_continue = _handle_ambiguous_names(
            filtered_scenarios, name, category, import_all
        )

        # Verify the prompt was called twice (once failed, once succeeded)
        assert call_count == 2
        # Verify the result is correct (first scenario selected)
        assert len(result_scenarios) == 1
        assert result_scenarios[0]["path"] == "scenario1.yaml"
        assert should_continue is True
    finally:
        # Restore original interactive mode
        ConfigManager.interactive_mode = original_interactive
