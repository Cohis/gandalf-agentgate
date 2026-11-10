"""Local IP enrichment. Nothing here ever calls out to the network per request.

Phase 1 ships the interface and the trivial classifier; phase 6 fills the tables
(DB-IP mmdb, iptoasn, Spamhaus DROP, FireHOL, Tor exits, internal reputation).
"""

from __future__ import annotations

import hashlib
import hmac
from ipaddress import IPv4Address, IPv6Address, ip_network

from gandalf_features import NetworkInfo

IP = IPv4Address | IPv6Address


class Enricher:
    def lookup(self, ip: IP) -> NetworkInfo:
        return NetworkInfo()


def ip_hmac(ip: IP, key: str) -> str:
    """Keyed hash stored instead of the raw address (LGPD: minimisation)."""
    return hmac.new(key.encode(), str(ip).encode(), hashlib.sha256).hexdigest()[:32]


def ip_prefix(ip: IP) -> str:
    bits = 24 if isinstance(ip, IPv4Address) else 48
    return str(ip_network(f"{ip}/{bits}", strict=False))


def ua_family(ua: str) -> str:
    low = ua.lower()
    for needle, family in (
        ("python", "python"),
        ("curl", "curl"),
        ("headless", "headless"),
        ("edg/", "edge"),
        ("firefox", "firefox"),
        ("chrome", "chrome"),
        ("safari", "safari"),
    ):
        if needle in low:
            return family
    return "outro" if ua else "vazio"
