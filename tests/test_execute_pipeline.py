import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "execute_pipeline", ROOT / "scripts" / "pipeline" / "execute.py"
)
EXECUTE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXECUTE)


class HostDownloadThrottleTests(unittest.TestCase):
    def test_waits_between_requests_to_the_same_host(self):
        now = [100.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        throttle = EXECUTE.HostDownloadThrottle(
            0.75, clock=lambda: now[0], sleep=sleep
        )
        url = "https://records.example/one.pdf"
        throttle.wait(url)
        throttle.finished(url)
        now[0] += 0.25
        throttle.wait("https://records.example/two.pdf")

        self.assertEqual(sleeps, [0.5])

    def test_does_not_delay_a_different_host(self):
        now = [100.0]
        sleeps = []
        throttle = EXECUTE.HostDownloadThrottle(
            0.75, clock=lambda: now[0], sleep=sleeps.append
        )
        throttle.finished("https://one.example/document.pdf")
        throttle.wait("https://two.example/document.pdf")

        self.assertEqual(sleeps, [])

    def test_download_counts_failed_requests_toward_throttling(self):
        throttle = mock.Mock()
        previous = EXECUTE.DOCUMENT_DOWNLOAD_THROTTLE
        EXECUTE.DOCUMENT_DOWNLOAD_THROTTLE = throttle
        try:
            with tempfile.TemporaryDirectory() as directory, mock.patch.object(
                EXECUTE,
                "safe_get",
                side_effect=EXECUTE.requests.ConnectionError("unavailable"),
            ):
                destination = Path(directory) / "document.pdf"
                with self.assertRaises(EXECUTE.requests.ConnectionError):
                    EXECUTE.download("https://records.example/document.pdf", destination)
        finally:
            EXECUTE.DOCUMENT_DOWNLOAD_THROTTLE = previous

        throttle.wait.assert_called_once_with("https://records.example/document.pdf")
        throttle.finished.assert_called_once_with("https://records.example/document.pdf")


if __name__ == "__main__":
    unittest.main()
