import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline"
sys.path.insert(0, str(PIPELINE))
SPEC = importlib.util.spec_from_file_location("pipeline_runner_config", PIPELINE / "runner.py")
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


class RuntimeConfigTests(unittest.TestCase):
    def test_total_document_floors_receive_two_record_tolerance(self):
        config = RUNNER.RuntimeConfig({
            "minimum_documents": 22,
            "minimum_records": 10,
        })
        self.assertEqual(config["minimum_documents"], 20)
        self.assertEqual(config.get("minimum_records"), 8)

    def test_floor_never_drops_below_one(self):
        self.assertEqual(RUNNER.RuntimeConfig({"minimum_documents": 1}).get("minimum_documents"), 1)
        self.assertEqual(RUNNER.RuntimeConfig({"minimum_records": 2})["minimum_records"], 1)

    def test_structural_and_date_guards_are_not_relaxed(self):
        config = RUNNER.RuntimeConfig({
            "minimum_meetings": 14,
            "minimum_agendas": 14,
            "minimum_minutes": 8,
            "expected_oldest_meeting": "2025-09-01",
        })
        self.assertEqual(config.get("minimum_meetings"), 14)
        self.assertEqual(config.get("minimum_agendas"), 14)
        self.assertEqual(config.get("minimum_minutes"), 8)
        self.assertEqual(config.get("expected_oldest_meeting"), "2025-09-01")

    def test_canary_floor_remains_deliberately_impossible(self):
        self.assertEqual(RUNNER.RuntimeConfig({"minimum_documents": 999})["minimum_documents"], 997)

    def test_original_stored_config_is_not_mutated(self):
        stored = {"minimum_documents": 22}
        config = RUNNER.RuntimeConfig(stored)
        self.assertEqual(config.get("minimum_documents"), 20)
        self.assertEqual(stored["minimum_documents"], 22)


if __name__ == "__main__":
    unittest.main()
