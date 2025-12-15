"""Unit tests for the broker.scenarios module.

These tests cover the pure utility functions, StepMemory class, and
ScenarioRunner initialization/configuration without requiring actual
providers or external services.
"""

from pathlib import Path

import pytest

from broker import scenarios
from broker.exceptions import ScenarioError
from broker.helpers import MockStub


TEST_SCENARIOS_DIR = Path(__file__).parent / "data" / "scenarios"
VALID_SCENARIO_PATH = TEST_SCENARIOS_DIR / "valid_scenario.yaml"
INVALID_SCHEMA_PATH = TEST_SCENARIOS_DIR / "invalid_schema.yaml"
LOOP_SCENARIO_PATH = TEST_SCENARIOS_DIR / "loop_scenario.yaml"

SAMPLE_CONTEXT = {
    "my_var": "test_value",
    "my_list": ["a", "b", "c"],
    "my_dict": {"key1": "val1", "key2": "val2"},
    "my_int": 42,
    "my_bool": True,
    "nested": {"inner": {"value": "deep"}},
}


def test_get_schema_returns_valid_dict():
    """Schema should be loaded as a dictionary with required 'steps' field."""
    schema = scenarios.get_schema()
    assert schema is not None
    assert isinstance(schema, dict)
    assert "required" in schema
    assert "steps" in schema["required"]


def test_render_template_preserves_python_types():
    """Simple variable references should preserve Python types (int, bool, list, dict)."""
    assert scenarios.render_template("{{ my_int }}", SAMPLE_CONTEXT) == 42
    assert scenarios.render_template("{{ my_bool }}", SAMPLE_CONTEXT) is True
    assert scenarios.render_template("{{ my_list }}", SAMPLE_CONTEXT) == ["a", "b", "c"]
    assert scenarios.render_template("{{ my_dict }}", SAMPLE_CONTEXT) == {"key1": "val1", "key2": "val2"}


def test_render_template_complex_returns_string():
    """Templates with surrounding text should return rendered string."""
    result = scenarios.render_template("Value is: {{ my_var }}", SAMPLE_CONTEXT)
    assert result == "Value is: test_value"
    assert isinstance(result, str)


def test_render_template_passthrough():
    """Non-string input and strings without template syntax should pass through unchanged."""
    assert scenarios.render_template(42, SAMPLE_CONTEXT) == 42
    assert scenarios.render_template(None, SAMPLE_CONTEXT) is None
    assert scenarios.render_template("plain string", SAMPLE_CONTEXT) == "plain string"


def test_render_template_undefined_behavior():
    """Complex templates with undefined vars raise; simple refs return None."""
    # Complex templates (with text around variable) raise ScenarioError
    with pytest.raises(ScenarioError) as exc_info:
        scenarios.render_template("prefix {{ undefined_var }}", SAMPLE_CONTEXT)
    assert "Undefined variable" in str(exc_info.value)

    # Simple variable refs use evaluate_expression path, which returns None
    result = scenarios.render_template("{{ undefined_var }}", SAMPLE_CONTEXT)
    assert result is None


def test_evaluate_expression_returns_python_objects():
    """Expressions should return actual Python objects, not strings."""
    assert scenarios.evaluate_expression("my_int", SAMPLE_CONTEXT) == 42
    assert scenarios.evaluate_expression("my_list", SAMPLE_CONTEXT) == ["a", "b", "c"]
    assert scenarios.evaluate_expression("my_int + 10", SAMPLE_CONTEXT) == 52
    assert list(scenarios.evaluate_expression("my_dict.items()", SAMPLE_CONTEXT)) == [
        ("key1", "val1"),
        ("key2", "val2"),
    ]


def test_evaluate_expression_undefined_returns_none():
    """Undefined variable returns None (jinja2 compile_expression behavior)."""
    result = scenarios.evaluate_expression("undefined", SAMPLE_CONTEXT)
    assert result is None


def test_evaluate_condition_boolean_results():
    """Conditions should evaluate to proper boolean values."""
    assert scenarios.evaluate_condition("", SAMPLE_CONTEXT) is True
    assert scenarios.evaluate_condition(None, SAMPLE_CONTEXT) is True
    assert scenarios.evaluate_condition("my_bool == true", SAMPLE_CONTEXT) is True
    assert scenarios.evaluate_condition("my_int > 40", SAMPLE_CONTEXT) is True
    assert scenarios.evaluate_condition("my_int < 40", SAMPLE_CONTEXT) is False


def test_evaluate_condition_string_coercion():
    """String values like 'true', 'false', 'yes', 'no' should coerce to booleans."""
    assert scenarios.evaluate_condition("result", {"result": "true"}) is True
    assert scenarios.evaluate_condition("result", {"result": "yes"}) is True
    assert scenarios.evaluate_condition("result", {"result": "false"}) is False
    assert scenarios.evaluate_condition("result", {"result": "none"}) is False


def test_evaluate_condition_is_defined():
    """'is defined' checks should work for variable existence."""
    assert scenarios.evaluate_condition("my_var is defined", SAMPLE_CONTEXT) is True
    assert scenarios.evaluate_condition("undefined_var is defined", SAMPLE_CONTEXT) is False


def test_recursive_render_nested_structures():
    """Should render templates throughout nested dicts and lists."""
    data = {
        "outer": {
            "inner": "{{ my_var }}",
            "list": ["{{ my_int }}", {"deep": "{{ my_bool }}"}],
        },
        "static": "no change",
    }
    result = scenarios.recursive_render(data, SAMPLE_CONTEXT)

    assert result["outer"]["inner"] == "test_value"
    assert result["outer"]["list"][0] == 42
    assert result["outer"]["list"][1]["deep"] is True
    assert result["static"] == "no change"


def test_find_scenario_by_direct_path():
    """Should find scenario by direct file path."""
    result = scenarios.find_scenario(str(VALID_SCENARIO_PATH))
    assert result == VALID_SCENARIO_PATH


def test_find_scenario_in_scenarios_dir(tmp_path, monkeypatch):
    """Should find scenarios by name in SCENARIOS_DIR with .yaml or .yml extension."""
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (scenarios_dir / "test_scenario.yaml").write_text("steps: []")
    (scenarios_dir / "another.yml").write_text("steps: []")
    monkeypatch.setattr(scenarios, "SCENARIOS_DIR", scenarios_dir)

    assert scenarios.find_scenario("test_scenario") == scenarios_dir / "test_scenario.yaml"
    assert scenarios.find_scenario("another") == scenarios_dir / "another.yml"


def test_find_scenario_nonexistent_raises():
    """Should raise ScenarioError for nonexistent scenario."""
    with pytest.raises(ScenarioError) as exc_info:
        scenarios.find_scenario("does_not_exist_anywhere")
    assert "Scenario not found" in str(exc_info.value)


def test_list_scenarios_returns_sorted_names(tmp_path, monkeypatch):
    """Should return sorted list of scenario names without extensions."""
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (scenarios_dir / "zebra.yaml").write_text("steps: []")
    (scenarios_dir / "alpha.yml").write_text("steps: []")
    monkeypatch.setattr(scenarios, "SCENARIOS_DIR", scenarios_dir)

    result = scenarios.list_scenarios()
    assert result == ["alpha", "zebra"]


def test_validate_scenario_valid():
    """Valid scenario should pass validation."""
    is_valid, error = scenarios.validate_scenario(VALID_SCENARIO_PATH)
    assert is_valid is True
    assert error is None


def test_validate_scenario_invalid_and_missing():
    """Invalid schema and nonexistent files should fail validation."""
    is_valid, error = scenarios.validate_scenario(INVALID_SCHEMA_PATH)
    assert is_valid is False
    assert "Validation error" in error

    is_valid, error = scenarios.validate_scenario("/nonexistent/path.yaml")
    assert is_valid is False
    assert "not found" in error


def test_step_memory_initialization_and_access():
    """StepMemory should initialize with defaults and support dict-style access."""
    mem = scenarios.StepMemory("test_step")

    # Check defaults
    assert mem.name == "test_step"
    assert mem.output is None
    assert mem.status == "pending"

    # Update and check to_dict
    mem.output = "some_output"
    mem.status = "completed"
    assert mem.to_dict() == {"name": "test_step", "output": "some_output", "status": "completed"}

    # Dict-style access
    assert mem["name"] == "test_step"
    assert mem["nonexistent"] is None
    assert mem.get("nonexistent", "default") == "default"


def test_scenario_runner_init_valid():
    """Should initialize with valid scenario file and load config/variables."""
    runner = scenarios.ScenarioRunner(VALID_SCENARIO_PATH)

    assert runner.scenario_name == "valid_scenario"
    assert runner.variables["my_var"] == "test_value"
    assert runner.variables["my_count"] == 2
    assert runner.config.get("inventory_path") == "/tmp/test_inventory.yaml"


def test_scenario_runner_cli_vars_override():
    """CLI variables should override scenario variables."""
    runner = scenarios.ScenarioRunner(
        VALID_SCENARIO_PATH,
        cli_vars={"my_var": "overridden", "new_var": "new_value"},
    )

    assert runner.variables["my_var"] == "overridden"
    assert runner.variables["new_var"] == "new_value"
    assert runner.variables["my_count"] == 2  # Unchanged


def test_scenario_runner_cli_config_overrides():
    """CLI config should create/update nested config paths."""
    runner = scenarios.ScenarioRunner(
        VALID_SCENARIO_PATH,
        cli_config={"settings.Container.runtime": "docker", "settings.NewProvider.option": "value"},
    )

    assert runner.config["settings"]["Container"]["runtime"] == "docker"
    assert runner.config["settings"]["NewProvider"]["option"] == "value"


def test_scenario_runner_init_errors():
    """Should raise ScenarioError for nonexistent file or invalid schema."""
    with pytest.raises(ScenarioError) as exc_info:
        scenarios.ScenarioRunner(Path("/nonexistent/scenario.yaml"))
    assert "Scenario file not found" in str(exc_info.value)

    with pytest.raises(ScenarioError) as exc_info:
        scenarios.ScenarioRunner(INVALID_SCHEMA_PATH)
    assert "validation failed" in str(exc_info.value)


def test_scenario_runner_map_argument_names():
    """'count' should be mapped to '_count', other args unchanged."""
    runner = scenarios.ScenarioRunner(VALID_SCENARIO_PATH)

    result = runner._map_argument_names({"count": 5, "nick": "test_nick"})

    assert result == {"_count": 5, "nick": "test_nick"}
    assert "count" not in result


def test_scenario_runner_get_inventory_path(tmp_path):
    """Should use config inventory_path or generate default."""
    runner = scenarios.ScenarioRunner(VALID_SCENARIO_PATH)
    assert runner.inventory_path == Path("/tmp/test_inventory.yaml")

    # Scenario without inventory_path config
    scenario = tmp_path / "no_inv.yaml"
    scenario.write_text("steps:\n  - name: test\n    action: output\n    arguments:\n      content: test\n      destination: stdout")
    runner = scenarios.ScenarioRunner(scenario)
    assert "scenario_no_inv_inventory.yaml" in str(runner.inventory_path)


def test_scenario_runner_build_context():
    """Context should include variables, steps memory, and scenario_inventory."""
    runner = scenarios.ScenarioRunner(VALID_SCENARIO_PATH)
    runner.steps_memory["step1"] = scenarios.StepMemory("step1")
    runner.steps_memory["step1"].output = "step1_output"

    context = runner._build_context("step2", runner.steps_memory["step1"])

    assert context["my_var"] == "test_value"
    assert context["steps"]["step1"].output == "step1_output"
    assert context["previous_step"].name == "step1"
    assert context["scenario_inventory"] == []


def test_scenario_runner_capture_output():
    """Capture should store results in variables, with optional transform."""
    runner = scenarios.ScenarioRunner(VALID_SCENARIO_PATH)
    runner.steps_memory["test"] = scenarios.StepMemory("test")
    context = runner._build_context("test", None)

    # Simple capture
    runner._capture_output({"as": "my_result"}, "captured_value", context)
    assert runner.variables["my_result"] == "captured_value"

    # Capture with transform
    result = {"data": [1, 2, 3]}
    runner._capture_output(
        {"as": "data_length", "transform": "{{ step.output.data | length }}"},
        result,
        context,
    )
    assert runner.variables["data_length"] == 3


def test_scenario_runner_get_info():
    """get_info should return scenario metadata summary."""
    runner = scenarios.ScenarioRunner(VALID_SCENARIO_PATH)
    info = runner.get_info()

    assert info["name"] == "valid_scenario"
    assert "config" in info
    assert "variables" in info
    assert info["steps"][0] == {"name": "test_step", "action": "checkout"}


def test_resolve_hosts_reference():
    """Should resolve various host reference formats."""
    scenario_inv = [MockStub({"hostname": "host1"}), MockStub({"hostname": "host2"})]

    # 'scenario_inventory' returns a copy
    result = scenarios.resolve_hosts_reference("scenario_inventory", scenario_inv, {})
    assert len(result) == 2
    assert result is not scenario_inv

    # Variable that is a list
    host_list = [{"hostname": "h1"}, {"hostname": "h2"}]
    result = scenarios.resolve_hosts_reference("{{ my_hosts }}", [], {"my_hosts": host_list})
    assert result == host_list

    # Unknown reference returns empty list
    result = scenarios.resolve_hosts_reference("unknown_ref", [], {})
    assert result == []


def test_action_output(capsys, tmp_path):
    """Output action should write to stdout, stderr, or files."""
    runner = scenarios.ScenarioRunner(VALID_SCENARIO_PATH)

    # stdout
    runner._action_output({"content": "Hello!", "destination": "stdout"})
    assert "Hello!" in capsys.readouterr().out

    # stderr
    runner._action_output({"content": "Error!", "destination": "stderr"})
    assert "Error!" in capsys.readouterr().err

    # File with dict content (JSON)
    output_file = tmp_path / "output.json"
    runner._action_output({"content": {"key": "value"}, "destination": str(output_file)})
    import json
    assert json.loads(output_file.read_text()) == {"key": "value"}

    # Missing content raises
    with pytest.raises(ScenarioError) as exc_info:
        runner._action_output({"destination": "stdout"})
    assert "requires 'content'" in str(exc_info.value)


def test_action_exit():
    """Exit action should raise SystemExit(0) or ScenarioError for non-zero."""
    runner = scenarios.ScenarioRunner(VALID_SCENARIO_PATH)

    # Test successful exit with return code 0
    with pytest.raises(SystemExit) as exc_info:
        runner._action_exit({"return_code": 0, "message": "Success"})
    assert exc_info.value.code == 0

    # Test failure with return code 1
    with pytest.raises(ScenarioError) as exc_info:
        runner._action_exit({"return_code": 1, "message": "Failure"})
    assert "Failure" in str(exc_info.value)

    # Test other non-zero return code
    with pytest.raises(ScenarioError) as exc_info:
        runner._action_exit({"return_code": 42, "message": "Custom error"})
    assert "Custom error" in str(exc_info.value)

    # Test default return code (should default to 0)
    with pytest.raises(SystemExit) as exc_info:
        runner._action_exit({})
    assert exc_info.value.code == 0

    # Test without message
    with pytest.raises(SystemExit) as exc_info:
        runner._action_exit({"return_code": 0})
    assert exc_info.value.code == 0


def test_dispatch_action_unknown():
    """Unknown action should raise ScenarioError."""
    runner = scenarios.ScenarioRunner(VALID_SCENARIO_PATH)

    with pytest.raises(ScenarioError) as exc_info:
        runner._dispatch_action({"name": "test", "action": "nonexistent"}, {}, None)
    assert "Unknown action" in str(exc_info.value)
