import importlib.util
import json
import sys
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline"
TOOLS = ROOT / "scripts" / "tools"
sys.path[:0] = [str(PIPELINE), str(TOOLS)]
SPEC = importlib.util.spec_from_file_location("pipeline_execute", PIPELINE / "execute.py")
EXECUTE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXECUTE)


class DocumentLimitTest(unittest.TestCase):
    def test_granicus_clip_uses_caption_materializer(self):
        url = "https://ousd.granicus.com/player/clip/2908"
        with TemporaryDirectory() as tmp, patch.object(
            EXECUTE, "fetch_granicus_captions", return_value="text/plain"
        ) as materialize:
            destination = Path(tmp) / "transcript"
            result = EXECUTE.download(
                url, destination, granicus_captions=True, max_bytes=4096
            )
        self.assertEqual("text/plain", result)
        materialize.assert_called_once_with(url, destination, max_bytes=4096)

    def test_granicus_clip_is_not_routed_to_youtube_materializer(self):
        url = "https://ousd.granicus.com/player/clip/2908"
        with TemporaryDirectory() as tmp, patch.object(
            EXECUTE, "fetch_granicus_captions", return_value="text/plain"
        ) as materialize, patch.object(
            EXECUTE, "fetch_youtube_transcript"
        ) as youtube:
            destination = Path(tmp) / "transcript"
            result = EXECUTE.download(
                url,
                destination,
                youtube_transcript_languages=["en"],
                granicus_captions=True,
                max_bytes=4096,
            )
        self.assertEqual("text/plain", result)
        materialize.assert_called_once_with(url, destination, max_bytes=4096)
        youtube.assert_not_called()

    def test_granicus_clip_url_is_strict(self):
        self.assertTrue(EXECUTE.is_granicus_clip_url(
            "https://ousd.granicus.com/player/clip/2908"
        ))
        self.assertFalse(EXECUTE.is_granicus_clip_url(
            "https://example.com/player/clip/2908"
        ))

    def test_download_uses_canonical_drive_media_resolver(self):
        canonical = "https://drive.google.com/file/d/Abc_123-X/view"
        with TemporaryDirectory() as tmp, patch.object(
            EXECUTE, "resolve_google_drive_video", return_value="application/octet-stream"
        ) as resolve:
            destination = Path(tmp) / "document"
            content_type = EXECUTE.download(
                canonical, destination, google_drive_view_media=True
            )
        self.assertEqual(content_type, "application/octet-stream")
        resolve.assert_called_once_with(
            canonical, destination, max_bytes=EXECUTE.MAX_DOCUMENT_BYTES
        )

    def test_drive_media_resolver_rejects_noncanonical_url(self):
        with TemporaryDirectory() as tmp, self.assertRaises(
            EXECUTE.DriveViewResolverError
        ):
            EXECUTE.download(
                "https://drive.usercontent.google.com/download?id=Abc_123-X",
                Path(tmp) / "document",
                google_drive_view_media=True,
            )

    def test_default_limit_is_preserved(self):
        self.assertEqual(EXECUTE.document_limit({"config": {}}), EXECUTE.MAX_DOCUMENT_BYTES)

    def test_per_scraper_limit_can_increase_within_cap(self):
        wanted = 200 * 1024 * 1024
        self.assertEqual(
            EXECUTE.document_limit({"config": {"document_max_bytes": wanted}}),
            wanted,
        )

    def test_limit_above_hard_cap_is_rejected(self):
        with self.assertRaises(ValueError):
            EXECUTE.document_limit({
                "config": {
                    "document_max_bytes": EXECUTE.MAX_CONFIGURED_DOCUMENT_BYTES + 1
                }
            })

    def test_boolean_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            EXECUTE.document_limit({"config": {"document_max_bytes": True}})

    def test_scraper_timeout_can_be_increased_within_cap(self):
        self.assertEqual(
            EXECUTE.scraper_timeout({"config": {"timeout_seconds": 900}}), 900
        )

    def test_scraper_timeout_above_cap_is_rejected(self):
        with self.assertRaises(ValueError):
            EXECUTE.scraper_timeout({
                "config": {
                    "timeout_seconds": EXECUTE.MAX_CONFIGURED_SCRAPER_TIMEOUT + 1
                }
            })

    def test_stale_window_is_at_least_twice_discovery_timeout(self):
        self.assertEqual(
            EXECUTE.stale_run_seconds({
                "config": {"timeout_seconds": 900, "stale_run_seconds": 900}
            }),
            1800,
        )

    def test_video_transcriptions_are_limited_to_twelve_new_items(self):
        target = {
            "config": {"video_transcripts": {"enabled": True}},
            "document_identities": {},
        }
        candidates = [
            {"kind": "video", "document_url": f"https://official.example/{i}.mp4"}
            for i in range(15)
        ]
        accepted, skipped = EXECUTE.limit_video_transcriptions(target, candidates)
        self.assertEqual(len(accepted), 12)
        self.assertEqual(skipped, 3)

    def test_existing_and_youtube_videos_do_not_consume_paid_limit(self):
        existing = "https://official.example/existing.mp4"
        target = {
            "config": {"video_transcripts": {"enabled": True}},
            "document_identities": {f"url:{existing}": 1},
        }
        candidates = [
            {"kind": "video", "document_url": existing},
            {"kind": "video", "document_url": "https://youtube.com/watch?v=Abc_123-Xyz"},
            *[
                {"kind": "video", "document_url": f"https://official.example/{i}.webm"}
                for i in range(13)
            ],
        ]
        accepted, skipped = EXECUTE.limit_video_transcriptions(target, candidates)
        self.assertEqual(len(accepted), 14)
        self.assertEqual(skipped, 1)

    def test_mislabeled_existing_transcripts_are_refreshable_and_counted(self):
        url = "https://official.example/existing.mp4"
        target = {
            "config": {"video_transcripts": {"enabled": True}},
            "document_identities": {f"url:{url}": 1},
            "refreshable_video_source_urls": {url},
        }
        candidates = [
            {"kind": "video", "document_url": url},
            *[
                {"kind": "video", "document_url": f"https://official.example/{i}.mp4"}
                for i in range(12)
            ],
        ]
        accepted, skipped = EXECUTE.limit_video_transcriptions(target, candidates)
        self.assertEqual(len(accepted), 12)
        self.assertEqual(skipped, 1)
        self.assertTrue(EXECUTE.needs_video_transcript_refresh(target, candidates[0]))

class DriveViewResolverTest(unittest.TestCase):
    class Response:
        def __init__(self, url, content, content_type="text/html"):
            self.url = url
            self.content = content
            self.headers = {"content-type": content_type}

    def test_resolves_exact_pdf_bytes_under_canonical_input(self):
        canonical = "https://drive.google.com/file/d/Abc_123-X/view"
        manifest_url = "https://drive.google.com/viewer/upload?ds=opaque&ck=drive"
        pdf_url = (
            "https://doc-00-a0-apps-viewer.googleusercontent.com/"
            "viewer/secure/pdf/session/file/123/drive/*/opaque"
        )
        html = (
            b'<script>"https://drive.google.com/viewer/upload?'
            b'ds\\u003dopaque\\u0026ck\\u003ddrive"</script>'
        )
        manifest = b")]}'\n" + json.dumps({
            "status": "status?id=opaque", "pdf": pdf_url,
        }).encode()
        pdf = b"%PDF-1.7\nbyte-exact official content"
        responses = [
            self.Response(canonical, html),
            self.Response(manifest_url, manifest, "application/json"),
            self.Response(pdf_url, pdf, "application/pdf; charset=binary"),
        ]
        with TemporaryDirectory() as tmp, patch.object(
            EXECUTE, "_resolver_get", side_effect=responses
        ) as get:
            destination = Path(tmp) / "document"
            content_type = EXECUTE.resolve_google_drive_view(
                canonical, destination, max_bytes=1024
            )
            self.assertEqual(destination.read_bytes(), pdf)
        self.assertEqual(content_type, "application/pdf")
        self.assertEqual([call.args[0] for call in get.call_args_list], [
            canonical, manifest_url, pdf_url,
        ])

    def test_rejects_unallowlisted_view_and_pdf_urls(self):
        self.assertFalse(EXECUTE.is_google_drive_view_url(
            "https://evil.example/file/d/Abc/view"
        ))
        with self.assertRaises(EXECUTE.DriveViewResolverError):
            EXECUTE._drive_pdf_url(b")]}'\n" + json.dumps({
                "status": "ok", "pdf": "https://evil.example/document.pdf",
            }).encode())
        with TemporaryDirectory() as tmp, self.assertRaises(
            EXECUTE.DriveViewResolverError
        ):
            EXECUTE.download(
                "https://drive.google.com/uc?id=Abc", Path(tmp) / "document",
                google_drive_view_pdf=True,
            )

    def test_accepts_lowercase_base36_drive_pdf_host_label(self):
        url = (
            "https://doc-0k-a0-apps-viewer.googleusercontent.com/"
            "viewer/secure/pdf/a/b/c/drive/*/d"
        )
        self.assertEqual(EXECUTE._drive_pdf_url(
            b")]}'\n" + json.dumps({"status": "ok", "pdf": url}).encode()
        ), url)

    def test_rejects_non_base36_drive_pdf_host_labels(self):
        for label in ("0_k", "0-k", "0.k", ""):
            url = (
                f"https://doc-{label}-a0-apps-viewer.googleusercontent.com/"
                "viewer/secure/pdf/a/b/c/drive/*/d"
            )
            with self.subTest(label=label), self.assertRaises(
                EXECUTE.DriveViewResolverError
            ):
                EXECUTE._drive_pdf_url(
                    b")]}'\n" + json.dumps({"status": "ok", "pdf": url}).encode()
                )

    def test_rejects_final_non_pdf_response(self):
        canonical = "https://drive.google.com/file/d/Abc/view"
        manifest_url = "https://drive.google.com/viewer/upload?ds=opaque"
        pdf_url = (
            "https://doc-01-a0-apps-viewer.googleusercontent.com/"
            "viewer/secure/pdf/a/b/c/drive/*/d"
        )
        html = b'"https://drive.google.com/viewer/upload?ds\\u003dopaque"'
        manifest = b")]}'\n" + json.dumps({"status": "ok", "pdf": pdf_url}).encode()
        responses = [
            self.Response(canonical, html),
            self.Response(manifest_url, manifest, "application/json"),
            self.Response(pdf_url, b"not a pdf", "text/html"),
        ]
        with TemporaryDirectory() as tmp, patch.object(
            EXECUTE, "_resolver_get", side_effect=responses
        ), patch.object(
            EXECUTE, "_drive_download_response",
            side_effect=EXECUTE.DriveViewResolverError("fallback unavailable"),
        ), self.assertRaises(EXECUTE.DriveViewResolverError):
            EXECUTE.resolve_google_drive_view(
                canonical, Path(tmp) / "document", max_bytes=1024
            )

    def test_pdf_resolver_falls_back_to_confirmed_large_file_download(self):
        canonical = "https://drive.google.com/file/d/Abc_123-X/view"
        confirmation_html = b'''<form id="download-form"
          action="https://drive.usercontent.google.com/download" method="get">
          <input type="hidden" name="id" value="Abc_123-X">
          <input type="hidden" name="export" value="download">
          <input type="hidden" name="confirm" value="t">
          <input type="hidden" name="uuid" value="safe-token">
        </form>'''

        def response(url, content, content_type):
            item = unittest.mock.MagicMock()
            item.url = url
            item.status_code = 200
            item.headers = {"content-type": content_type}
            item.iter_content.return_value = [content]
            item.raise_for_status.return_value = None
            return item

        interstitial = response(
            "https://drive.usercontent.google.com/download?id=Abc_123-X",
            confirmation_html, "text/html",
        )
        pdf = response(
            "https://drive.usercontent.google.com/download?id=Abc_123-X",
            b"%PDF-1.7\nlarge official file", "application/pdf",
        )
        with TemporaryDirectory() as tmp, patch.object(
            EXECUTE, "_resolver_get",
            side_effect=EXECUTE.DriveViewResolverError("viewer export timed out"),
        ), patch.object(
            EXECUTE, "_drive_download_response", side_effect=[interstitial, pdf]
        ) as get:
            destination = Path(tmp) / "document"
            self.assertEqual(
                "application/pdf", EXECUTE.resolve_google_drive_view(
                    canonical, destination, max_bytes=1024,
                ),
            )
            self.assertEqual(b"%PDF-1.7\nlarge official file", destination.read_bytes())
        self.assertIn("confirm=t", get.call_args_list[1].args[0])

    def test_pdf_resolver_falls_back_when_viewer_upload_fetch_fails(self):
        canonical = "https://drive.google.com/file/d/Abc_123-X/view"
        manifest_url = "https://drive.google.com/viewer/upload?ds=opaque"
        viewer = self.Response(
            canonical,
            b'"https://drive.google.com/viewer/upload?ds\\u003dopaque"',
        )

        def response(url, content, content_type):
            item = unittest.mock.MagicMock()
            item.url = url
            item.status_code = 200
            item.headers = {"content-type": content_type}
            item.iter_content.return_value = [content]
            item.raise_for_status.return_value = None
            return item

        pdf = response(
            "https://drive.usercontent.google.com/download?id=Abc_123-X",
            b"%PDF-1.7\nDrive generic MIME type", "application/octet-stream",
        )
        with TemporaryDirectory() as tmp, patch.object(
            EXECUTE, "_resolver_get", side_effect=[
                viewer,
                EXECUTE.DriveViewResolverError(
                    f"Drive resolver fetch failed: {manifest_url}"
                ),
            ],
        ), patch.object(
            EXECUTE, "_drive_download_response", return_value=pdf
        ) as get:
            destination = Path(tmp) / "document"
            self.assertEqual(
                "application/pdf", EXECUTE.resolve_google_drive_view(
                    canonical, destination, max_bytes=1024,
                ),
            )
        self.assertEqual(
            "https://drive.usercontent.google.com/download?"
            "id=Abc_123-X&export=download&confirm=t",
            get.call_args.args[0],
        )

    def test_resolver_get_checks_robots_before_fetching(self):
        url = "https://drive.google.com/file/d/Abc/view"
        parser = type("Parser", (), {"can_fetch": lambda self, ua, value: True})()
        response = type("Response", (), {
            "url": url,
            "status_code": 200,
            "content": b"ok",
            "raise_for_status": lambda self: None,
            "iter_content": lambda self, size: iter([b"ok"]),
            "close": lambda self: None,
        })()
        with patch.object(EXECUTE, "_robots_policy", return_value=(parser, 0)), \
                patch("safe_http.socket.getaddrinfo", return_value=[(
                    2, 1, 6, "", ("142.250.72.206", 443)
                )]), \
                patch.object(EXECUTE.HTTP, "get", return_value=response) as get:
            EXECUTE._resolver_get(url, max_bytes=10)
        get.assert_called_once()

    def test_robots_cache_key_normalizes_the_drive_pdf_export_pattern(self):
        first = (
            "https://doc-00-a0-apps-viewer.googleusercontent.com/"
            "viewer/secure/pdf/session/file/1/drive/*/a"
        )
        second = (
            "https://doc-ff-a0-apps-viewer.googleusercontent.com/"
            "viewer/secure/pdf/session/file/2/drive/*/b"
        )
        self.assertEqual(
            EXECUTE._robots_cache_key(first), EXECUTE._robots_cache_key(second)
        )
        self.assertEqual(
            EXECUTE._robots_cache_key(first), "pattern:drive-pdf-export-host"
        )

    def test_robots_cache_key_leaves_ordinary_hosts_untouched(self):
        self.assertEqual(
            EXECUTE._robots_cache_key("https://drive.google.com/viewer/upload?ds=x"),
            "https://drive.google.com",
        )
        self.assertNotEqual(
            EXECUTE._robots_cache_key("https://drive.google.com/x"),
            EXECUTE._robots_cache_key("https://example.org/x"),
        )

    def test_robots_policy_is_fetched_once_across_different_pdf_host_subdomains(self):
        # Regression test: a real signed Drive PDF export URL was expiring
        # before the pipeline's final fetch, because every document landed
        # on a different random subdomain and paid a fresh robots.txt round
        # trip mid-sequence. Confirms that cost is now paid at most once.
        first_url = (
            "https://doc-00-a0-apps-viewer.googleusercontent.com/"
            "viewer/secure/pdf/session/file/1/drive/*/a"
        )
        second_url = (
            "https://doc-ff-a0-apps-viewer.googleusercontent.com/"
            "viewer/secure/pdf/session/file/2/drive/*/b"
        )
        robots_response = self.Response(
            "https://doc-00-a0-apps-viewer.googleusercontent.com/robots.txt",
            b"", "text/plain",
        )
        robots_response.status_code = 404
        robots_response.raise_for_status = lambda: None
        robots_response.iter_content = lambda size: iter([])
        robots_response.close = lambda: None

        cache_backup = dict(EXECUTE._ROBOTS_CACHE)
        last_request_backup = dict(EXECUTE._ORIGIN_LAST_REQUEST)
        EXECUTE._ROBOTS_CACHE.clear()
        EXECUTE._ORIGIN_LAST_REQUEST.clear()
        try:
            with patch.object(
                EXECUTE, "safe_get", return_value=robots_response
            ) as fetch:
                EXECUTE._robots_policy(first_url)
                EXECUTE._robots_policy(second_url)
            fetch.assert_called_once()
        finally:
            EXECUTE._ROBOTS_CACHE.clear()
            EXECUTE._ROBOTS_CACHE.update(cache_backup)
            EXECUTE._ORIGIN_LAST_REQUEST.clear()
            EXECUTE._ORIGIN_LAST_REQUEST.update(last_request_backup)

    def test_generic_document_download_rejects_loopback(self):
        with TemporaryDirectory() as tmp, self.assertRaises(
            EXECUTE.UnsafeUrlError
        ):
            EXECUTE.download(
                "http://127.0.0.1:3100/api/admin/scrapes/status",
                Path(tmp) / "document",
            )

    def test_drive_media_url_must_match_canonical_file_id(self):
        canonical = "https://drive.google.com/file/d/Abc_123-X/view"
        html = (
            b'"https://drive.usercontent.google.com/uc?'
            b'id\\u003dAbc_123-X\\u0026export\\u003ddownload"'
        )
        self.assertEqual(
            "https://drive.usercontent.google.com/uc?id=Abc_123-X&export=download",
            EXECUTE._drive_media_url(canonical, html),
        )
        bad = html.replace(b"Abc_123-X", b"Other_456")
        with self.assertRaises(EXECUTE.DriveViewResolverError):
            EXECUTE._drive_media_url(canonical, bad)

    def test_drive_video_handles_large_file_confirmation(self):
        canonical = "https://drive.google.com/file/d/Abc_123-X/view"
        viewer = self.Response(canonical, (
            b'"https://drive.usercontent.google.com/uc?'
            b'id\\u003dAbc_123-X\\u0026export\\u003ddownload"'
        ))
        confirmation_html = b'''<form id="download-form"
          action="https://drive.usercontent.google.com/download" method="get">
          <input type="hidden" name="id" value="Abc_123-X">
          <input type="hidden" name="export" value="download">
          <input type="hidden" name="confirm" value="t">
          <input type="hidden" name="uuid" value="safe-token">
        </form>'''

        def response(url, content, content_type):
            item = unittest.mock.MagicMock()
            item.url = url
            item.status_code = 200
            item.headers = {"content-type": content_type}
            item.iter_content.return_value = [content]
            item.raise_for_status.return_value = None
            return item

        confirm = response(
            "https://drive.usercontent.google.com/download?id=Abc_123-X",
            confirmation_html, "text/html",
        )
        media = response(
            "https://drive.usercontent.google.com/download?id=Abc_123-X",
            b"video bytes", "video/mp4",
        )
        with TemporaryDirectory() as temporary, patch.object(
            EXECUTE, "_resolver_get", return_value=viewer
        ), patch.object(
            EXECUTE, "_drive_download_response", side_effect=[confirm, media]
        ) as get:
            destination = Path(temporary) / "recording"
            content_type = EXECUTE.resolve_google_drive_video(
                canonical, destination, max_bytes=1024
            )
            self.assertEqual(b"video bytes", destination.read_bytes())
        self.assertEqual("video/mp4", content_type)
        self.assertIn("confirm=t", get.call_args_list[1].args[0])

    def test_video_input_uses_trusted_media_transcriber(self):
        url = "https://official.example/meeting.webm"
        with TemporaryDirectory() as temporary, patch.object(
            EXECUTE, "safe_get"
        ) as get, patch.object(
            EXECUTE, "fetch_video_transcript", return_value="text/plain"
        ) as transcribe:
            response = unittest.mock.MagicMock()
            response.__enter__.return_value = response
            response.iter_content.return_value = [b"video"]
            response.headers = {"content-type": "video/webm"}
            response.raise_for_status.return_value = None
            get.return_value = response
            destination = Path(temporary) / "transcript"
            result = EXECUTE.download(
                url, destination, max_bytes=1024,
                video_transcript_config={"enabled": True}, video_max_bytes=4096,
            )
        self.assertEqual("text/plain", result)
        self.assertEqual(url, transcribe.call_args.args[0])

    def test_youtube_video_uses_transcript_materializer(self):
        url = "https://www.youtube.com/watch?v=Abc_123-Xyz"
        with TemporaryDirectory() as tmp, patch.object(
            EXECUTE, "fetch_youtube_transcript", return_value="text/plain"
        ) as fetch:
            destination = Path(tmp) / "document"
            content_type = EXECUTE.download(
                url, destination, max_bytes=1234,
                youtube_transcript_languages=["en", "es"],
            )
        self.assertEqual(content_type, "text/plain")
        fetch.assert_called_once_with(
            url, destination, ["en", "es"], max_bytes=1234
        )


if __name__ == "__main__":
    unittest.main()
