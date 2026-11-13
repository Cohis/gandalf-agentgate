"""Phase 6: local IP enrichment from the daily job's tables."""

from __future__ import annotations

import json
from ipaddress import ip_address
from pathlib import Path

import numpy as np
from gandalf_engine.enrich import Enricher


def ip(s: str) -> int:
    return int(ip_address(s))


def write_tables(d: Path) -> None:
    np.savez_compressed(
        d / "rede.npz",
        asn_start=np.array([ip("8.8.8.0"), ip("177.0.0.0")], dtype=np.uint32),
        asn_end=np.array([ip("8.8.8.255"), ip("177.255.255.255")], dtype=np.uint32),
        asn=np.array([15169, 18881], dtype=np.uint32),
        tipo=np.array([3, 1], dtype=np.uint8),
        pais=np.array(["US", "BR"]),
        tipos=np.array(["desconhecida", "residencial", "movel", "datacenter"]),
        lista_start=np.array([ip("1.10.16.0")], dtype=np.uint32),
        lista_end=np.array([ip("1.10.31.255")], dtype=np.uint32),
        tor=np.array([ip("171.25.193.25")], dtype=np.uint32),
    )
    (d / "reputacao_interna.json").write_text(json.dumps({"177.10.20.0/24": [0.8, 0]}))


def test_lookups(tmp_path: Path) -> None:
    write_tables(tmp_path)
    e = Enricher(tmp_path)
    dc = e.lookup(ip_address("8.8.8.8"))
    assert (dc.tipo_rede, dc.asn, dc.pais) == ("datacenter", 15169, "US")
    home = e.lookup(ip_address("177.10.20.30"))
    assert home.tipo_rede == "residencial" and home.reputacao == 0.8
    assert e.lookup(ip_address("171.25.193.25")).tipo_rede == "tor"
    assert e.lookup(ip_address("1.10.20.1")).em_lista_bloqueio
    unknown = e.lookup(ip_address("203.0.113.9"))
    assert unknown.tipo_rede == "desconhecida" and not unknown.em_lista_bloqueio
    assert e.lookup(ip_address("2001:db8::1")).tipo_rede == "desconhecida"  # no IPv6 tables


def test_without_tables_and_reload(tmp_path: Path) -> None:
    assert Enricher(None).lookup(ip_address("8.8.8.8")).tipo_rede == "desconhecida"
    e = Enricher(tmp_path)  # directory exists, tables not yet
    assert e.lookup(ip_address("8.8.8.8")).tipo_rede == "desconhecida"
    write_tables(tmp_path)
    assert e.reload() is True and e.reload() is False  # only when the job rewrote them
    assert e.lookup(ip_address("8.8.8.8")).tipo_rede == "datacenter"
