"""SSRF-safe outbound HTTP for the scraper pipeline.

The generated scraper and the trusted document downloader both accept URLs
from data we do not trust.  A syntax check is not enough: redirects can jump
to an internal service and a hostname can resolve differently between a
preflight check and the actual connection.  This module therefore:

* accepts only HTTP(S) URLs without embedded credentials;
* rejects loopback, private, link-local, multicast and reserved addresses;
* requires every DNS answer to be public;
* pins the connection to the exact DNS answers that passed validation; and
* follows redirects manually, repeating the full validation for every hop.

The DNS pin is process-wide for the brief duration of ``requests.send``.
Scraper workers are single-threaded, and the lock makes accidental future
threading fail closed rather than interleave two resolutions.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import requests


MAX_URL_LENGTH = 8_192
MAX_REDIRECTS = 5
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
BLOCKED_HOSTS = frozenset({"localhost", "localhost.localdomain"})
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
_DNS_LOCK = threading.RLock()


class UnsafeUrlError(requests.RequestException):
    """Raised before an outbound request can reach a non-public destination."""


@dataclass(frozen=True)
class PublicTarget:
    url: str
    hostname: str
    port: int
    addrinfo: tuple[tuple, ...]


def _hostname(value: str) -> str:
    host = (value or "").rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeUrlError("URL hostname is invalid") from exc
    if not host or host in BLOCKED_HOSTS or host.endswith(BLOCKED_SUFFIXES):
        raise UnsafeUrlError(f"URL hostname is not public: {host or '<empty>'}")
    return host


def _public_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError as exc:
        raise UnsafeUrlError("DNS returned an invalid IP address") from exc
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    # is_global is deliberately stricter than merely "not private": it also
    # excludes loopback, link-local, multicast, unspecified and reserved space.
    if not address.is_global:
        raise UnsafeUrlError(f"URL resolved to a non-public address: {address}")
    return address


def resolve_public_url(url: str) -> PublicTarget:
    value = (url or "").strip()
    if not value or len(value) > MAX_URL_LENGTH:
        raise UnsafeUrlError("URL is empty or too long")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrlError("URL is malformed") from exc
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise UnsafeUrlError("only absolute HTTP(S) URLs are allowed")
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrlError("credentials in URLs are not allowed")
    host = _hostname(parts.hostname)
    port = port or (443 if parts.scheme == "https" else 80)
    # Reject IP literals before DNS. Besides being faster, this prevents a
    # resolver shim from laundering a loopback literal into a public answer.
    try:
        literal = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None:
        _public_ip(str(literal))
    try:
        answers = socket.getaddrinfo(
            host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise UnsafeUrlError(f"could not resolve public hostname: {host}") from exc
    if not answers:
        raise UnsafeUrlError(f"hostname returned no addresses: {host}")
    for answer in answers:
        _public_ip(answer[4][0])
    return PublicTarget(value, host, port, tuple(answers))


def assert_public_url(url: str) -> str:
    """Validate and resolve a URL, returning it unchanged on success."""
    return resolve_public_url(url).url


def _same_host(left: str, right: str) -> bool:
    try:
        return _hostname(str(left)) == _hostname(str(right))
    except UnsafeUrlError:
        return False


@contextmanager
def _pinned_dns(target: PublicTarget):
    original = socket.getaddrinfo

    def pinned(host, port, family=0, type=0, proto=0, flags=0):
        try:
            requested_port = int(port)
        except (TypeError, ValueError):
            requested_port = port
        if _same_host(host, target.hostname) and requested_port == target.port:
            answers = target.addrinfo
            if family not in (0, socket.AF_UNSPEC):
                answers = tuple(item for item in answers if item[0] == family)
            if type:
                answers = tuple(item for item in answers if item[1] == type)
            if proto:
                answers = tuple(item for item in answers if item[2] == proto)
            if not answers:
                raise socket.gaierror("validated DNS answers do not match socket request")
            return list(answers)
        return original(host, port, family, type, proto, flags)

    with _DNS_LOCK:
        socket.getaddrinfo = pinned
        try:
            yield
        finally:
            socket.getaddrinfo = original


def safe_get(session: requests.Session, url: str, *, max_redirects: int = MAX_REDIRECTS,
             **kwargs) -> requests.Response:
    """Perform an SSRF-safe GET and validate every redirect before following."""
    if isinstance(max_redirects, bool) or not isinstance(max_redirects, int) \
            or not 0 <= max_redirects <= MAX_REDIRECTS:
        raise ValueError(f"max_redirects must be between 0 and {MAX_REDIRECTS}")
    if "proxies" in kwargs:
        raise UnsafeUrlError("per-request proxies are not allowed")
    kwargs.pop("allow_redirects", None)
    headers = kwargs.get("headers") or {}
    if any(str(name).lower() == "host" for name in headers):
        raise UnsafeUrlError("overriding the Host header is not allowed")
    # Environment proxies would move DNS and redirect enforcement outside this
    # process. Scraper traffic must always use the validated direct connection.
    session.trust_env = False
    session.proxies.clear()

    current = (url or "").strip()
    history: list[requests.Response] = []
    for hop in range(max_redirects + 1):
        target = resolve_public_url(current)
        with _pinned_dns(target):
            response = session.get(target.url, allow_redirects=False, **kwargs)
        if response.status_code not in REDIRECT_STATUSES:
            response.history = history
            return response
        location = response.headers.get("location")
        if not location:
            response.close()
            raise UnsafeUrlError("redirect response did not include a Location header")
        if hop >= max_redirects:
            response.close()
            raise UnsafeUrlError(f"redirect limit exceeded ({max_redirects})")
        next_url = urljoin(target.url, location)
        # Validate before closing out this iteration so an unsafe redirect is
        # rejected before requests has any opportunity to connect to it.
        resolve_public_url(next_url)
        history.append(response)
        response.close()
        current = next_url
    raise UnsafeUrlError("redirect limit exceeded")


def safe_post(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """Perform one SSRF-safe POST without following redirects."""
    if "proxies" in kwargs:
        raise UnsafeUrlError("per-request proxies are not allowed")
    kwargs.pop("allow_redirects", None)
    headers = kwargs.get("headers") or {}
    if any(str(name).lower() == "host" for name in headers):
        raise UnsafeUrlError("overriding the Host header is not allowed")
    session.trust_env = False
    session.proxies.clear()
    target = resolve_public_url((url or "").strip())
    with _pinned_dns(target):
        response = session.post(target.url, allow_redirects=False, **kwargs)
    if response.status_code in REDIRECT_STATUSES:
        response.close()
        raise UnsafeUrlError("POST redirects are not allowed")
    return response
