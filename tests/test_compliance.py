import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline"
sys.path.insert(0, str(PIPELINE))
SPEC = importlib.util.spec_from_file_location("pipeline_compliance", PIPELINE / "compliance.py")
COMPLIANCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPLIANCE)


class HostScopedTermsTest(unittest.TestCase):
    def test_simbli_host_is_prohibited_without_live_fetch(self):
        with patch.object(COMPLIANCE, "find_terms_url") as discovery:
            result = COMPLIANCE.check_terms(
                "https://simbli.eboardsolutions.com/SB_Meetings/"
            )
        discovery.assert_not_called()
        self.assertEqual(result["verdict"], "prohibited")
        self.assertEqual(result["raw"]["platform"], "simbli")

    def test_boarddocs_subdomain_uses_its_own_live_terms_discovery(self):
        with patch.object(COMPLIANCE, "find_terms_url", return_value=None) as discovery:
            result = COMPLIANCE.check_terms("https://go.boarddocs.com/ca/example/Board.nsf/Public")
        discovery.assert_called_once()
        self.assertEqual(result["verdict"], "unavailable")

    def test_custom_district_host_does_not_inherit_platform_terms(self):
        with patch.object(COMPLIANCE, "find_terms_url", return_value=None) as discovery:
            result = COMPLIANCE.check_terms("https://meetings.example.edu/archive")
        discovery.assert_called_once()
        self.assertEqual(result["verdict"], "unavailable")

    def test_simbli_finding_records_the_reviewed_terms_url(self):
        result = COMPLIANCE.known_host_terms(
            "https://simbli.eboardsolutions.com/SB_Meetings/"
        )
        self.assertEqual(
            result["url"], "https://simbli.eboardsolutions.com/TERMSOFSERVICE.PDF"
        )

    def test_unreviewed_host_still_uses_live_discovery(self):
        with patch.object(COMPLIANCE, "find_terms_url", return_value=None) as discovery:
            result = COMPLIANCE.check_terms("https://district.example.edu/")
        discovery.assert_called_once()
        self.assertEqual(result["verdict"], "unavailable")


if __name__ == "__main__":
    unittest.main()
