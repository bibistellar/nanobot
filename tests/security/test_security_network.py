"""Tests for nanobot.security.network — SSRF protection and internal URL detection."""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from nanobot.security.network import (
    configure_ssrf_whitelist,
    contains_internal_url,
    reset_ssrf_whitelist,
    validate_url_target,
)


@pytest.fixture(autouse=True)
def _isolate_ssrf_whitelist():
    """``configure_ssrf_whitelist`` is union-only and never shrinks the
    module-level allowlist (see issue #3). Tests still need to start
    from a clean slate, so reset before each test and tear down after."""
    reset_ssrf_whitelist()
    yield
    reset_ssrf_whitelist()


def _fake_resolve(host: str, results: list[str]):
    """Return a getaddrinfo mock that maps the given host to fake IP results."""
    def _resolver(hostname, port, family=0, type_=0):
        if hostname == host:
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in results]
        raise socket.gaierror(f"cannot resolve {hostname}")
    return _resolver


# ---------------------------------------------------------------------------
# validate_url_target — scheme / domain basics
# ---------------------------------------------------------------------------

def test_rejects_non_http_scheme():
    ok, err = validate_url_target("ftp://example.com/file")
    assert not ok
    assert "http" in err.lower()


def test_rejects_missing_domain():
    ok, err = validate_url_target("http://")
    assert not ok


# ---------------------------------------------------------------------------
# validate_url_target — blocked private/internal IPs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ip,label", [
    ("127.0.0.1", "loopback"),
    ("127.0.0.2", "loopback_alt"),
    ("10.0.0.1", "rfc1918_10"),
    ("172.16.5.1", "rfc1918_172"),
    ("192.168.1.1", "rfc1918_192"),
    ("169.254.169.254", "metadata"),
    ("0.0.0.0", "zero"),
])
def test_blocks_private_ipv4(ip: str, label: str):
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("evil.com", [ip])):
        ok, err = validate_url_target("http://evil.com/path")
        assert not ok, f"Should block {label} ({ip})"
        assert "private" in err.lower() or "blocked" in err.lower()


def test_blocks_ipv6_loopback():
    def _resolver(hostname, port, family=0, type_=0):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0, 0, 0))]
    with patch("nanobot.security.network.socket.getaddrinfo", _resolver):
        ok, err = validate_url_target("http://evil.com/")
        assert not ok


# ---------------------------------------------------------------------------
# validate_url_target — IPv6-mapped IPv4 bypass prevention
# ---------------------------------------------------------------------------

def _fake_resolve_v6(host: str, results: list[str]):
    """Like _fake_resolve but returns AF_INET6 tuples for IPv6 addresses."""
    def _resolver(hostname, port, family=0, type_=0):
        if hostname == host:
            entries = []
            for ip in results:
                if ":" in ip:
                    entries.append((socket.AF_INET6, socket.SOCK_STREAM, 0, "", (ip, 0, 0, 0)))
                else:
                    entries.append((socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)))
            return entries
        raise socket.gaierror(f"cannot resolve {hostname}")
    return _resolver


def test_blocks_ipv6_mapped_loopback():
    """::ffff:127.0.0.1 must be blocked just like 127.0.0.1."""
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_v6("evil.com", ["::ffff:127.0.0.1"])):
        ok, err = validate_url_target("http://evil.com/")
        assert not ok
        assert "blocked" in err.lower()


def test_blocks_ipv6_mapped_metadata():
    """::ffff:169.254.169.254 must be blocked just like 169.254.169.254."""
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_v6("evil.com", ["::ffff:169.254.169.254"])):
        ok, err = validate_url_target("http://evil.com/")
        assert not ok


def test_blocks_ipv6_mapped_rfc1918():
    """::ffff:10.0.0.1 must be blocked just like 10.0.0.1."""
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_v6("evil.com", ["::ffff:10.0.0.1"])):
        ok, err = validate_url_target("http://evil.com/")
        assert not ok


def test_allows_public_ipv6():
    """Public IPv6 addresses must still be allowed."""
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_v6("example.com", ["2606:4700::6810:84e5"])):
        ok, err = validate_url_target("http://example.com/")
        assert ok, f"Should allow public IPv6, got: {err}"


# ---------------------------------------------------------------------------
# validate_url_target — allows public IPs
# ---------------------------------------------------------------------------

def test_allows_public_ip():
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("example.com", ["93.184.216.34"])):
        ok, err = validate_url_target("http://example.com/page")
        assert ok, f"Should allow public IP, got: {err}"


def test_allows_normal_https():
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("github.com", ["140.82.121.3"])):
        ok, err = validate_url_target("https://github.com/HKUDS/nanobot")
        assert ok


# ---------------------------------------------------------------------------
# contains_internal_url — shell command scanning
# ---------------------------------------------------------------------------

def test_detects_curl_metadata():
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("169.254.169.254", ["169.254.169.254"])):
        assert contains_internal_url('curl -s http://169.254.169.254/computeMetadata/v1/')


def test_detects_wget_localhost():
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("localhost", ["127.0.0.1"])):
        assert contains_internal_url("wget http://localhost:8080/secret")


def test_loopback_exception_allows_literal_localhost_only():
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("localhost", ["127.0.0.1"])):
        assert not contains_internal_url("curl http://localhost:8765/", allow_loopback=True)


def test_loopback_exception_rejects_public_name_resolving_to_loopback():
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("example.com", ["127.0.0.1"])):
        assert contains_internal_url("curl http://example.com:8765/", allow_loopback=True)


def test_loopback_exception_rejects_metadata():
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("169.254.169.254", ["169.254.169.254"])):
        assert contains_internal_url("curl http://169.254.169.254/latest/meta-data/", allow_loopback=True)


def test_detects_ipv6_mapped_loopback():
    """contains_internal_url must catch IPv6-mapped loopback in shell commands."""
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_v6("evil.com", ["::ffff:127.0.0.1"])):
        assert contains_internal_url("curl http://evil.com/secret")


def test_allows_normal_curl():
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("example.com", ["93.184.216.34"])):
        assert not contains_internal_url("curl https://example.com/api/data")


def test_no_urls_returns_false():
    assert not contains_internal_url("echo hello && ls -la")


# ---------------------------------------------------------------------------
# SSRF whitelist — allow specific CIDR ranges (#2669)
# ---------------------------------------------------------------------------

def test_blocks_cgnat_by_default():
    """100.64.0.0/10 (CGNAT / Tailscale) is blocked by default."""
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("ts.local", ["100.100.1.1"])):
        ok, _ = validate_url_target("http://ts.local/api")
        assert not ok


def test_whitelist_allows_cgnat():
    """Whitelisting 100.64.0.0/10 lets Tailscale addresses through."""
    configure_ssrf_whitelist(["100.64.0.0/10"])
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("ts.local", ["100.100.1.1"])):
        ok, err = validate_url_target("http://ts.local/api")
        assert ok, f"Whitelisted CGNAT should be allowed, got: {err}"


def test_whitelist_does_not_affect_other_blocked():
    """Whitelisting CGNAT must not unblock other private ranges."""
    configure_ssrf_whitelist(["100.64.0.0/10"])
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("evil.com", ["10.0.0.1"])):
        ok, _ = validate_url_target("http://evil.com/secret")
        assert not ok


def test_whitelist_invalid_cidr_ignored():
    """Invalid CIDR entries are silently skipped."""
    configure_ssrf_whitelist(["not-a-cidr", "100.64.0.0/10"])
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("ts.local", ["100.100.1.1"])):
        ok, _ = validate_url_target("http://ts.local/api")
        assert ok


def test_whitelist_allows_ipv6_mapped_cgnat():
    """Whitelist must work when DNS returns IPv6-mapped CGNAT address."""
    configure_ssrf_whitelist(["100.64.0.0/10"])
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_v6("ts.local", ["::ffff:100.100.1.1"])):
        ok, err = validate_url_target("http://ts.local/api")
        assert ok, f"Whitelisted IPv6-mapped CGNAT should be allowed, got: {err}"


# ---------------------------------------------------------------------------
# Union / non-shrinking semantics for configure_ssrf_whitelist (issue #3)
# ---------------------------------------------------------------------------

def test_configure_ssrf_whitelist_is_union_not_replace():
    """A later configure_ssrf_whitelist call must NOT replace the existing
    set; it must union in new entries. Multiple load_config() call sites
    (web.py's lazy reload, WebUI MCP routes, the gateway entry) all reach
    here, and the prod gateway hit a state in 2026-06-12 where one of those
    paths clobbered ``10.0.0.0/8`` with an empty list."""
    configure_ssrf_whitelist(["10.0.0.0/8"])
    configure_ssrf_whitelist(["100.64.0.0/10"])
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("k8s.local", ["10.43.0.1"])):
        ok, _ = validate_url_target("http://k8s.local/v1")
        assert ok, "10.0.0.0/8 from the first call must survive the second"
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("ts.local", ["100.100.1.1"])):
        ok, _ = validate_url_target("http://ts.local/api")
        assert ok, "100.64.0.0/10 from the second call must also work"


def test_configure_ssrf_whitelist_empty_list_does_not_clear():
    """Passing an empty list (the failure mode that triggered issue #3 in
    prod — a code path falling back to ``Config()`` defaults) must NOT
    erase the previously-whitelisted networks. Once a CIDR is in, only
    process restart can take it out."""
    configure_ssrf_whitelist(["10.0.0.0/8"])
    configure_ssrf_whitelist([])  # what a defective load_config did in prod
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("k8s.local", ["10.43.0.1"])):
        ok, _ = validate_url_target("http://k8s.local/v1")
        assert ok, "previous CIDR must survive an empty re-configure"


def test_configure_ssrf_whitelist_dedupes_repeat_calls():
    """Calling with the same CIDRs twice must not blow up the internal
    list — duplicates are silently dropped during union."""
    configure_ssrf_whitelist(["10.0.0.0/8", "100.64.0.0/10"])
    configure_ssrf_whitelist(["10.0.0.0/8", "100.64.0.0/10"])
    from nanobot.security.network import _allowed_networks
    assert len(_allowed_networks) == 2
