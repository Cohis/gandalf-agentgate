"""Local IP enrichment. Nothing here ever calls out to the network per request.

Phase 1 ships the interface and the trivial classifier; phase 6 fills the tables
(DB-IP mmdb, iptoasn, Spamhaus DROP, FireHOL, Tor exits, internal reputation).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from ipaddress import IPv4Address, IPv6Address, ip_network
from pathlib import Path

import numpy as np
from gandalf_features import NetworkInfo

IP = IPv4Address | IPv6Address


class Enricher:
    """Local IP enrichment from the tables built by jobs/ip_reputation.py: network
    type and country from ip2asn, public blocklists (Spamhaus DROP, FireHOL level 1),
    Tor exits, and the shop's own decayed fraud history per /24.

    Everything is in memory; a lookup is a couple of binary searches. Without the
    tables (unit tests, a fresh clone) every address is 'desconhecida'. IPv6 is not
    covered by these sources yet and is always 'desconhecida'.
    """

    def __init__(self, ip_dir: Path | None = None) -> None:
        self._dir = ip_dir
        self._mtime = 0.0
        self._t: dict[str, np.ndarray] = {}
        self._rep: dict[str, float] = {}
        self.reload()

    def reload(self) -> bool:
        """Re-read the tables if the daily job rewrote them. Returns True if it did."""
        if self._dir is None:
            return False
        npz, rep = self._dir / "rede.npz", self._dir / "reputacao_interna.json"
        try:
            mtime = max(npz.stat().st_mtime, rep.stat().st_mtime if rep.exists() else 0.0)
        except OSError:
            return False
        if mtime <= self._mtime:
            return False
        with np.load(npz) as data:
            self._t = {k: data[k] for k in data.files}
        self._rep = (
            {p: float(v[0]) for p, v in json.loads(rep.read_text()).items()} if rep.exists() else {}
        )
        self._mtime = mtime
        return True

    @staticmethod
    def _in(starts: np.ndarray, ends: np.ndarray, n: int) -> int:
        i = int(np.searchsorted(starts, n, side="right")) - 1
        return i if i >= 0 and n <= int(ends[i]) else -1

    def lookup(self, ip: IP) -> NetworkInfo:
        if not self._t or not isinstance(ip, IPv4Address):
            return NetworkInfo()
        n = int(ip)
        t = self._t
        tor = t["tor"]
        j = int(np.searchsorted(tor, n))
        is_tor = j < len(tor) and int(tor[j]) == n
        listed = self._in(t["lista_start"], t["lista_end"], n) >= 0
        i = self._in(t["asn_start"], t["asn_end"], n)
        asn, kind, country = (0, "desconhecida", "")
        if i >= 0:
            asn = int(t["asn"][i])
            kind = str(t["tipos"][int(t["tipo"][i])])
            country = str(t["pais"][i])
        return NetworkInfo(
            tipo_rede="tor" if is_tor else kind,
            pais=country,
            asn=asn,
            em_lista_bloqueio=listed,
            reputacao=self._rep.get(ip_prefix(ip), 0.0),
        )


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
