import importlib.util
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import requests


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline"
sys.path.insert(0, str(PIPELINE))
SPEC = importlib.util.spec_from_file_location("pipeline_safe_http", PIPELINE / "safe_http.py")
SAFE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SAFE
SPEC.loader.exec_module(SAFE)


def answer(ip, port=443):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)


class Response:
    def __init__(self, status=200, location=None):
        self.status_code = status
        self.headers = {} if location is None else {"location": location}
        self.history = []
        self.closed = False

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.proxies = {}
        self.trust_env = True

    def get(self, url, **kwargs):
        # A second resolution here simulates requests/urllib3 connecting after
        # validation. It must receive the pinned public answer, not a rebind.
        resolved = socket.getaddrinfo("public.example", 443, type=socket.SOCK_STREAM)
        self.calls.append((url, kwargs, resolved))
        return next(self.responses)

    def post(self, url, **kwargs):
        resolved = socket.getaddrinfo("public.example", 443, type=socket.SOCK_STREAM)
        self.calls.append((url, kwargs, resolved))
        return next(self.responses)


class SafeHttpTest(unittest.TestCase):
    def test_rejects_local_private_metadata_and_non_http_targets(self):
        blocked = (
            "http://localhost/admin",
            "http://localhost.localdomain/",
            "http://service.internal/",
            "http://127.0.0.1/",
            "http://2130706433/",
            "http://10.0.0.2/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://[::ffff:127.0.0.1]/",
            "file:///etc/passwd",
            "https://user:secret@public.example/",
        )
        real = socket.getaddrinfo

        def resolve(host, port, **kwargs):
            if host in {"localhost", "localhost.localdomain", "service.internal"}:
                return [answer("127.0.0.1", port)]
            return real(host, port, **kwargs)

        with patch.object(SAFE.socket, "getaddrinfo", side_effect=resolve):
            for url in blocked:
                with self.subTest(url=url), self.assertRaises(SAFE.UnsafeUrlError):
                    SAFE.resolve_public_url(url)

    def test_rejects_hostname_with_any_non_public_dns_answer(self):
        answers = [answer("93.184.216.34"), answer("127.0.0.1")]
        with patch.object(SAFE.socket, "getaddrinfo", return_value=answers):
            with self.assertRaises(SAFE.UnsafeUrlError):
                SAFE.resolve_public_url("https://public.example/document.pdf")

    def test_connection_is_pinned_to_validated_dns_answer(self):
        public = [answer("93.184.216.34")]
        private = [answer("127.0.0.1")]
        with patch.object(SAFE.socket, "getaddrinfo", side_effect=[public, private]):
            session = Session([Response()])
            response = SAFE.safe_get(session, "https://public.example/document.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.calls[0][2][0][4][0], "93.184.216.34")

    def test_redirect_to_loopback_is_rejected_before_second_request(self):
        public = [answer("93.184.216.34")]
        session = Session([Response(302, "http://127.0.0.1/private")])
        with patch.object(SAFE.socket, "getaddrinfo", return_value=public):
            with self.assertRaises(SAFE.UnsafeUrlError):
                SAFE.safe_get(session, "https://public.example/start")
        self.assertEqual(len(session.calls), 1)

    def test_each_public_redirect_is_revalidated(self):
        public = [answer("93.184.216.34")]
        first = Response(302, "/next")
        session = Session([first, Response(200)])
        with patch.object(SAFE.socket, "getaddrinfo", return_value=public):
            response = SAFE.safe_get(session, "https://public.example/start")
        self.assertEqual([item[0] for item in session.calls], [
            "https://public.example/start", "https://public.example/next",
        ])
        self.assertEqual(response.history, [first])
        self.assertTrue(first.closed)

    def test_proxy_and_host_header_bypasses_are_rejected(self):
        session = requests.Session()
        with self.assertRaises(SAFE.UnsafeUrlError):
            SAFE.safe_get(session, "https://public.example", proxies={})
        with self.assertRaises(SAFE.UnsafeUrlError):
            SAFE.safe_get(session, "https://public.example", headers={"Host": "localhost"})

    def test_preconfigured_session_proxy_is_removed(self):
        public = [answer("93.184.216.34")]
        session = Session([Response()])
        session.proxies = {"https": "http://127.0.0.1:8888"}
        session.trust_env = True
        with patch.object(SAFE.socket, "getaddrinfo", return_value=public):
            SAFE.safe_get(session, "https://public.example/document.pdf")
        self.assertEqual(session.proxies, {})
        self.assertFalse(session.trust_env)

    def test_post_is_dns_pinned_and_redirects_fail_closed(self):
        public = [answer("93.184.216.34")]
        redirect = Response(307, "http://127.0.0.1/private")
        session = Session([redirect])
        with patch.object(SAFE.socket, "getaddrinfo", return_value=public):
            with self.assertRaises(SAFE.UnsafeUrlError):
                SAFE.safe_post(session, "https://public.example/youtubei/v1/player")
        self.assertEqual(session.calls[0][2][0][4][0], "93.184.216.34")
        self.assertTrue(redirect.closed)


if __name__ == "__main__":
    unittest.main()
