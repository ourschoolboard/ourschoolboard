import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline"
SPEC = importlib.util.spec_from_file_location("pipeline_sandbox_runtime", PIPELINE / "sandbox.py")
SANDBOX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SANDBOX)


class RootRuntimeTests(unittest.TestCase):
    def test_root_run_copies_trusted_runner_and_dependency_to_run_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with unittest.mock.patch.object(SANDBOX.os, "geteuid", return_value=0):
                runner = SANDBOX._child_runner(root)

            self.assertEqual(root / "runtime" / "runner.py", runner)
            self.assertEqual((PIPELINE / "runner.py").read_bytes(), runner.read_bytes())
            self.assertEqual(
                (PIPELINE / "safe_http.py").read_bytes(),
                (root / "runtime" / "safe_http.py").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
