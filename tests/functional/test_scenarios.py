from pathlib import Path
from broker.scenarios import ScenarioRunner

TEST_SCENARIO_DIR = Path(__file__).parent / "scenarios"

def test_comprehensive_container_scenario():
    scenario = ScenarioRunner(scenario_path=TEST_SCENARIO_DIR / "comprehensive_container_test.yaml")

    assert scenario is not None
    assert scenario.scenario_name == "comprehensive_container_test"
    assert scenario.config.get("inventory_path") == "~/.broker/comprehensive_test_inventory.yaml"
    assert scenario.config.get("log_path") == "comprehensive_container_test.log"
    scenario.run()

def test_deploy_checkin_containers_scenario():
    scenario = ScenarioRunner(scenario_path=TEST_SCENARIO_DIR / "deploy_checkin_containers.yaml")

    assert scenario is not None
    assert scenario.scenario_name == "deploy_checkin_containers"
    scenario.run()

def test_deploy_details_scenario():
    scenario = ScenarioRunner(scenario_path=TEST_SCENARIO_DIR / "deploy_details.yaml")

    assert scenario is not None
    assert scenario.scenario_name == "deploy_details"
    scenario.run()
    assert "workflow_list" in scenario.variables
    assert "workflow_details" in scenario.variables
    assert scenario.steps_memory["List workflows"].status == "completed"
