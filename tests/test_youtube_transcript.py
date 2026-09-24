import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline"
sys.path.insert(0, str(PIPELINE))
SPEC = importlib.util.spec_from_file_location(
    "pipeline_youtube_transcript", PIPELINE / "youtube_transcript.py"
)
YOUTUBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(YOUTUBE)


class FakeTranscript:
    video_id = "Abc_123-Xyz"
    language = "English"
    language_code = "en"
    is_generated = True

    def __iter__(self):
        return iter([
            SimpleNamespace(start=1.9, text="Welcome &amp; introductions"),
            SimpleNamespace(start=3661.2, text="  Budget\n motion approved  "),
        ])


class YouTubeTranscriptTest(unittest.TestCase):
    def test_parses_supported_individual_video_urls(self):
        expected = "Abc_123-Xyz"
        for url in (
            f"https://www.youtube.com/watch?v={expected}",
            f"https://youtube.com/watch?feature=share&v={expected}",
            f"https://youtu.be/{expected}?si=tracking",
            f"https://www.youtube.com/live/{expected}",
            f"https://m.youtube.com/shorts/{expected}",
        ):
            with self.subTest(url=url):
                self.assertEqual(YOUTUBE.video_id_from_url(url), expected)

    def test_rejects_playlists_credentials_http_and_lookalike_hosts(self):
        for url in (
            "https://www.youtube.com/playlist?list=abc",
            "http://www.youtube.com/watch?v=Abc_123-Xyz",
            "https://user@www.youtube.com/watch?v=Abc_123-Xyz",
            "https://youtube.example/watch?v=Abc_123-Xyz",
            "https://www.youtube.com/watch?v=too-short",
        ):
            with self.subTest(url=url):
                self.assertIsNone(YOUTUBE.video_id_from_url(url))

    def test_renders_deterministic_timestamped_plain_text(self):
        url = "https://www.youtube.com/watch?v=Abc_123-Xyz"
        text = YOUTUBE.render_transcript(url, FakeTranscript())
        self.assertIn("Transcript type: YouTube auto-generated captions", text)
        self.assertIn("[00:00:01] Welcome & introductions", text)
        self.assertIn("[01:01:01] Budget motion approved", text)
        self.assertEqual(text, YOUTUBE.render_transcript(url, FakeTranscript()))

    def test_network_client_rejects_non_allowlisted_library_url(self):
        client = YOUTUBE.RestrictedYouTubeClient()
        with self.assertRaises(YOUTUBE.YouTubeTranscriptError):
            client.get("https://evil.example/api/timedtext")
        with self.assertRaises(YOUTUBE.YouTubeTranscriptError):
            client.post("https://www.youtube.com/account")

    def test_proxy_credentials_are_encoded_and_never_in_target_url(self):
        with patch.dict(YOUTUBE.os.environ, {
            "BRIGHTDATA_PROXY": "proxy.example:22225",
            "BRIGHTDATA_PROXY_USER": "user:name",
            "BRIGHTDATA_PROXY_PASS": "p@ss/word",
        }, clear=False):
            proxy = YOUTUBE._brightdata_proxy_url()
        self.assertEqual(
            proxy, "http://user%3Aname:p%40ss%2Fword@proxy.example:22225"
        )
        client = YOUTUBE.RestrictedYouTubeClient(proxy)
        response = SimpleNamespace(
            is_redirect=False, is_permanent_redirect=False
        )
        with patch.object(client._session, "get", return_value=response) as get:
            self.assertIs(client.get(
                "https://www.youtube.com/watch?v=Abc_123-Xyz"
            ), response)
        self.assertNotIn("user", get.call_args.args[0])
        self.assertFalse(get.call_args.kwargs["allow_redirects"])

    def test_fetch_writes_stable_text_and_enforces_size_limit(self):
        api = SimpleNamespace(fetch=lambda video_id, languages: FakeTranscript())
        module = SimpleNamespace(YouTubeTranscriptApi=lambda http_client: api)
        url = "https://www.youtube.com/watch?v=Abc_123-Xyz"
        with TemporaryDirectory() as tmp, patch.dict(
            sys.modules, {"youtube_transcript_api": module}
        ):
            output = Path(tmp) / "transcript.txt"
            self.assertEqual(
                YOUTUBE.fetch_to_file(url, output, ["en"], max_bytes=10_000),
                "text/plain",
            )
            self.assertIn("Budget motion approved", output.read_text())
            with self.assertRaises(YOUTUBE.YouTubeTranscriptError):
                YOUTUBE.fetch_to_file(
                    url, Path(tmp) / "small.txt", ["en"], max_bytes=10
                )

    def test_dataset_uses_direct_video_check_when_snapshot_omits_transcript(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        session = SimpleNamespace(
            trust_env=True,
            headers={},
            post=lambda url, **kwargs: Response(
                [{"transcript": "Budget motion approved", "url": kwargs["json"][0]["url"]}]
            ) if url.endswith("/scrape") else Response({"snapshot_id": "snap-1"}),
            get=lambda url, **kwargs: Response({"status": "ready"})
            if "/progress/" in url else Response([{"warning": "no transcript field"}]),
        )
        url = "https://www.youtube.com/watch?v=Abc_123-Xyz"
        with patch.object(YOUTUBE.requests, "Session", return_value=session):
            rendered = YOUTUBE._fetch_via_dataset(url, "Abc_123-Xyz", "secret")
        self.assertIn("Budget motion approved", rendered)
        self.assertFalse(session.trust_env)
        self.assertEqual(session.headers["Authorization"], "Bearer secret")


if __name__ == "__main__":
    unittest.main()
