import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline"
TOOLS = ROOT / "scripts" / "tools"
sys.path[:0] = [str(PIPELINE), str(TOOLS)]


def load(name):
    spec = importlib.util.spec_from_file_location(name, PIPELINE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScraperUserAgentTest(unittest.TestCase):
    def test_default_is_conventional_browser_identity(self):
        agents = [
            load("sandbox").DEFAULT_USER_AGENT,
            load("runner").USER_AGENT,
            load("execute").USER_AGENT,
        ]
        for agent in agents:
            self.assertTrue(agent.startswith("Mozilla/5.0"))
            self.assertIn("AppleWebKit/537.36", agent)
            self.assertIn("Chrome/", agent)
            self.assertNotIn("ourschoolboard", agent.lower())


if __name__ == "__main__":
    unittest.main()
