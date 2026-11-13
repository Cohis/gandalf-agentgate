"""Daily job: build the engine's local IP tables (phase 6).

    uv run python -m jobs.ip_reputation            # use the files already in data/ip/
    uv run python -m jobs.ip_reputation --baixar   # download them first

Sources, all free and without an account:
    ip2asn-v4.tsv.gz     iptoasn.com: IP range -> ASN, registry country, AS name
    drop_v4.json         Spamhaus DROP: hijacked netblocks used by criminals
    firehol_level1.netset FireHOL level 1: high-confidence attack sources
    tor_exits.txt        Tor Project bulk exit list

Output: data/ip/rede.npz (ranges, ASN, network type, country) and
data/ip/reputacao_interna.json (the shop's own history, per /24, with exponential
decay). The engine loads both into memory and never looks anything up per request.

The network type comes from the AS name. It is a heuristic: cloud and hosting
providers are easy to name, mobile operators share ASNs with fixed broadband in
Brazil, so "movel" is only claimed for a short list of mobile-only ASNs.
"""

from __future__ import annotations

import argparse
import gzip
import ipaddress
import json
import math
import os
import time
from pathlib import Path

import numpy as np

IP_DIR = Path(os.environ.get("GANDALF_IP_DIR", "data/ip"))
DSN = os.environ.get("GANDALF_DATABASE_URL", "postgresql://gandalf:gandalf@localhost:5432/gandalf")
HALF_LIFE_S = 7 * 86_400

SOURCES = {
    "ip2asn-v4.tsv.gz": "https://iptoasn.com/data/ip2asn-v4.tsv.gz",
    "drop_v4.json": "https://www.spamhaus.org/drop/drop_v4.json",
    "firehol_level1.netset": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level1.netset",
    "tor_exits.txt": "https://check.torproject.org/torbulkexitlist",
}
TYPES = ("desconhecida", "residencial", "movel", "datacenter")  # tor is a separate set
DATACENTER = (
    "AMAZON", "AWS", "GOOGLE", "MICROSOFT", "AZURE", "DIGITALOCEAN", "OVH", "HETZNER",
    "LINODE", "AKAMAI", "ORACLE", "ALIBABA", "TENCENT", "CLOUDFLARE", "VULTR", "CHOOPA",
    "CONTABO", "LEASEWEB", "M247", "SCALEWAY", "IONOS", "HOSTINGER", "LOCAWEB", "KINGHOST",
    "HOSTGATOR", "GODADDY", "RACKSPACE", "FASTLY", "HOSTING", "DATACENTER", "DATA CENTER",
    "CLOUD", "SERVER", "COLOCATION", "VPS",
)  # fmt: skip
MOBILE_ASNS = {26615}  # TIM S/A: mobile-only in Brazil. Others mix fixed and mobile.


def download() -> None:
    import httpx

    IP_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=180, follow_redirects=True) as c:
        for name, url in SOURCES.items():
            r = c.get(url)
            r.raise_for_status()
            (IP_DIR / name).write_bytes(r.content)
            print(f"baixado {name} ({len(r.content) // 1024} KiB)")


def _usable(net: ipaddress.IPv4Network) -> bool:
    """Lists like FireHOL include private and reserved space ('bogons'). Flagging
    127.0.0.1 or a Docker address as hostile would block the lab itself."""
    return not (net.is_private or net.is_reserved or net.is_loopback or net.is_multicast
                or net.is_link_local or net.is_unspecified)  # fmt: skip


def network_type(asn: int, name: str) -> int:
    if asn == 0:
        return TYPES.index("desconhecida")
    if asn in MOBILE_ASNS:
        return TYPES.index("movel")
    upper = name.upper()
    if any(word in upper for word in DATACENTER):
        return TYPES.index("datacenter")
    return TYPES.index("residencial")


def build_asn_table() -> dict[str, np.ndarray]:
    starts, ends, asns, kinds, countries = [], [], [], [], []
    with gzip.open(IP_DIR / "ip2asn-v4.tsv.gz", "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            a, b, asn, cc, name = parts[:5]
            starts.append(int(ipaddress.IPv4Address(a)))
            ends.append(int(ipaddress.IPv4Address(b)))
            asns.append(int(asn))
            kinds.append(network_type(int(asn), name))
            countries.append(cc if len(cc) == 2 else "")
    order = np.argsort(np.asarray(starts, dtype=np.uint32))
    return {
        "asn_start": np.asarray(starts, dtype=np.uint32)[order],
        "asn_end": np.asarray(ends, dtype=np.uint32)[order],
        "asn": np.asarray(asns, dtype=np.uint32)[order],
        "tipo": np.asarray(kinds, dtype=np.uint8)[order],
        "pais": np.asarray(countries, dtype="<U2")[order],
    }


def build_blocklist() -> dict[str, np.ndarray]:
    nets: list[ipaddress.IPv4Network] = []
    for line in (IP_DIR / "drop_v4.json").read_text().splitlines():
        try:
            nets.append(ipaddress.IPv4Network(json.loads(line)["cidr"]))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue  # the file ends with a metadata line
    for line in (IP_DIR / "firehol_level1.netset").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            try:
                nets.append(ipaddress.IPv4Network(line, strict=False))
            except ValueError:
                continue
    nets = sorted(n for n in set(nets) if _usable(n))
    merged: list[list[int]] = []
    for n in nets:  # collapse overlaps so a binary search finds the one range
        a, b = int(n.network_address), int(n.broadcast_address)
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    arr = np.asarray(merged, dtype=np.uint32).reshape(-1, 2)
    return {"lista_start": arr[:, 0], "lista_end": arr[:, 1]}


def build_tor() -> dict[str, np.ndarray]:
    ips = []
    for line in (IP_DIR / "tor_exits.txt").read_text().splitlines():
        try:
            ips.append(int(ipaddress.IPv4Address(line.strip())))
        except ValueError:
            continue
    return {"tor": np.unique(np.asarray(ips, dtype=np.uint32))}


def internal_reputation() -> dict[str, list[float]]:
    """The shop's own history per /24: decayed share of confirmed fraud. Labels come
    late (chargebacks, reviews); this job is where they become a signal."""
    import psycopg

    sql = """
        SELECT e.ip_prefixo, extract(epoch FROM e.momento), l.eh_fraude
        FROM events e JOIN labels l USING (avaliacao_id)
        WHERE e.ip_prefixo LIKE '%%/24'
    """
    with psycopg.connect(DSN) as conn:
        rows = conn.execute(sql).fetchall()
    if not rows:
        return {}
    newest = max(float(t) for _, t, _ in rows)
    acc: dict[str, list[float]] = {}
    for prefix, t, fraud in rows:
        w = math.exp(-math.log(2) * (newest - float(t)) / HALF_LIFE_S)
        f, h = acc.setdefault(prefix, [0.0, 0.0])
        acc[prefix] = [f + w * fraud, h + w * (not fraud)]
    # Laplace-smoothed share of fraud; a prefix seen once says little.
    return {p: [round(f / (f + h + 2.0), 4), newest] for p, (f, h) in acc.items() if f > 0}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--baixar", action="store_true", help="download the sources first")
    ap.add_argument("--sem-banco", action="store_true", help="skip the internal reputation")
    a = ap.parse_args()
    if a.baixar:
        download()
    t0 = time.monotonic()
    tables = build_asn_table() | build_blocklist() | build_tor()
    tables["tipos"] = np.asarray(TYPES)
    np.savez_compressed(IP_DIR / "rede.npz", **tables)
    rep = {} if a.sem_banco else internal_reputation()
    (IP_DIR / "reputacao_interna.json").write_text(json.dumps(rep))
    kinds = np.bincount(tables["tipo"], minlength=len(TYPES))
    print(
        f"{len(tables['asn'])} faixas de ASN ({dict(zip(TYPES, kinds.tolist(), strict=True))}), "
        f"{len(tables['lista_start'])} faixas em listas, {len(tables['tor'])} saídas Tor, "
        f"{len(rep)} prefixos com reputação interna, em {time.monotonic() - t0:.0f} s"
    )


if __name__ == "__main__":
    main()
