"""
Website fetcher (live mode) with an SSRF guard.

SSRF = Server-Side Request Forgery: a malicious page links to an internal
address (e.g. the cloud metadata service at 169.254.169.254) hoping our server
will fetch it and leak credentials. `is_safe_url()` blocks every private,
loopback, link-local, multicast and reserved address BEFORE any request is
made, including addresses reached through DNS.

Other guards: http/https only, 2 MB response cap, 10 s timeout, no redirects
to unsafe hosts (each redirect hop is re-checked), polite User-Agent.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx

MAX_BYTES = 2_000_000
TIMEOUT_S = 10.0
USER_AGENT = "SalesNavigatorBot/0.1 (+business-research; respects robots.txt)"

_TAG = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.S | re.I)
_SPACE = re.compile(r"\s+")


def _ip_is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        or addr.is_reserved or addr.is_unspecified
    )


def is_safe_url(url: str, *, resolve: bool = True) -> bool:
    """
    True only for http(s) URLs whose host resolves exclusively to public IPs.

    `resolve=False` checks literal IPs and obvious internal names only
    (used in unit tests, where DNS must not be touched).
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in ("localhost", "metadata.google.internal") or host.endswith((".local", ".internal")):
        return False
    try:
        return _ip_is_public(host)          # host is a literal IP
    except ValueError:
        pass                                 # host is a name
    if not resolve:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    return all(_ip_is_public(info[4][0]) for info in infos)


def html_to_text(html: str) -> str:
    return _SPACE.sub(" ", _TAG.sub(" ", html)).strip()


class WebsiteFetcher:
    """Fetches public pages for the crawler. Never follows a redirect to an unsafe host."""

    def fetch(self, url: str) -> str | None:
        current = url
        with httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT},
                          follow_redirects=False) as client:
            for _hop in range(4):
                if not is_safe_url(current):
                    return None
                resp = client.get(current)
                if resp.is_redirect and "location" in resp.headers:
                    current = urljoin(current, resp.headers["location"])
                    continue
                if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
                    return None
                return html_to_text(resp.content[:MAX_BYTES].decode(resp.encoding or "utf-8", "ignore"))
        return None
