import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "vote_store", ROOT / "scripts" / "tools" / "vote_store.py"
)
STORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STORE)


def complete_vote():
    return {
        "voteKey": "2026-11-03-school-bond",
        "kind": "bond",
        "venue": "ballot",
        "title": "School construction bond",
        "summary": "Voters will decide whether to authorize the bond.",
        "amount": 25_000_000,
        "jurisdiction": "Example City",
        "voteDate": "2026-11-03",
        "status": "scheduled",
        "detail": {"projects": ["Example Middle School"]},
        "sourceUrl": "https://clerk.example.gov/elections/school-bond",
        "lastVerified": "2026-09-24",
    }


class VoteStoreTests(unittest.TestCase):
    def test_normalizes_supported_vote(self):
        vote = STORE.normalize_vote(complete_vote())
        self.assertEqual(vote["vote_key"], "2026-11-03-school-bond")
        self.assertEqual(vote["amount"], 25_000_000)

    def test_outcome_requires_official_source(self):
        vote = complete_vote()
        vote["status"] = "passed"
        vote["sourceUrl"] = None
        with self.assertRaisesRegex(ValueError, "requires a sourceUrl"):
            STORE.normalize_vote(vote)

    def test_scheduled_vote_requires_date(self):
        vote = complete_vote()
        vote["voteDate"] = None
        with self.assertRaisesRegex(ValueError, "requires voteDate"):
            STORE.normalize_vote(vote)

    def test_rejects_non_https_source(self):
        vote = complete_vote()
        vote["sourceUrl"] = "http://clerk.example.gov/elections"
        with self.assertRaisesRegex(ValueError, "https URL"):
            STORE.normalize_vote(vote)

    def test_writes_require_staging(self):
        with patch.object(STORE, "load_env"):
            with patch.dict(os.environ, {"APP_ENV": "production"}, clear=True):
                with self.assertRaisesRegex(ValueError, "refusing to write outside staging"):
                    STORE.require_staging_env()
            with patch.dict(os.environ, {"APP_ENV": "staging"}, clear=True):
                STORE.require_staging_env()


if __name__ == "__main__":
    unittest.main()
