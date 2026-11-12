"""Feature computation shared by online serving and offline training.

There is exactly one implementation of every feature. The engine and the offline
replay both call the same three pure functions:

    state_queries(event)            -> which sliding-window counts are needed
    compute(event, counts, network) -> the feature vector
    state_writes(event, event_id)   -> what to record once the decision is made

Only the *store* differs (Redis online, memory offline), and both stores implement
the same sorted-set semantics. Windows are always read BEFORE the current event is
written, so a feature never sees its own event.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import IPv6Address, ip_network

from gandalf_contracts import AssessRequest, Digitacao, Evento

FEATURE_VERSION = "f2"  # f2 = f1 + toque, toque_area

WINDOWS: dict[str, int] = {"10m": 600, "1h": 3600, "24h": 86400}
MAX_WINDOW_S = max(WINDOWS.values())

FeatureVector = dict[str, float | None]


@dataclass(frozen=True, slots=True)
class WindowQuery:
    name: str  # feature name the count feeds
    key: str  # sorted-set key
    window_s: int


@dataclass(frozen=True, slots=True)
class StateWrite:
    key: str
    member: str


@dataclass(frozen=True, slots=True)
class NetworkInfo:
    """Result of local IP enrichment. No external call is ever made per request."""

    tipo_rede: str = "desconhecida"  # residencial | movel | datacenter | tor | desconhecida
    pais: str = ""
    asn: int = 0
    em_lista_bloqueio: bool = False
    reputacao: float = 0.0  # internal, time-decayed, 0 (clean) .. 1 (bad)


def ip_key(ip: object) -> str:
    """IPv4 is keyed by address; IPv6 by /64, since a single host owns the whole /64."""
    if isinstance(ip, IPv6Address):
        return str(ip_network(f"{ip}/64", strict=False).network_address) + "/64"
    return str(ip)


def _entities(ev: AssessRequest) -> dict[str, str]:
    ents = {"disp": ev.dispositivo_id, "ip": ip_key(ev.ip)}
    if ev.conta_id:
        ents["conta"] = ev.conta_id
    if ev.cartao_hash:
        ents["cartao"] = ev.cartao_hash
    return ents


# (feature prefix, "by" entity, counted entity): distinct <counted> seen per <by>
_DISTINCT = (
    ("contas_por_disp", "disp", "conta"),
    ("contas_por_ip", "ip", "conta"),
    ("disps_por_conta", "conta", "disp"),
    ("cartoes_por_disp", "disp", "cartao"),
    ("cartoes_por_ip", "ip", "cartao"),
)
_DISTINCT_WINDOWS = ("1h", "24h")


def state_queries(ev: AssessRequest) -> list[WindowQuery]:
    ents = _entities(ev)
    queries = [
        WindowQuery(f"ev_{dim}_{w}", f"ev:{dim}:{val}", secs)
        for dim, val in ents.items()
        for w, secs in WINDOWS.items()
    ]
    queries += [
        WindowQuery(f"cadastros_ip_{w}", f"su:ip:{ents['ip']}", WINDOWS[w]) for w in ("1h", "24h")
    ]
    for prefix, by, _counted in _DISTINCT:
        if by in ents:
            queries += [
                WindowQuery(f"{prefix}_{w}", f"dx:{prefix}:{ents[by]}", WINDOWS[w])
                for w in _DISTINCT_WINDOWS
            ]
    return queries


def state_writes(ev: AssessRequest, event_id: str) -> list[StateWrite]:
    ents = _entities(ev)
    writes = [StateWrite(f"ev:{dim}:{val}", event_id) for dim, val in ents.items()]
    if ev.evento is Evento.cadastro:
        writes.append(StateWrite(f"su:ip:{ents['ip']}", event_id))
    for prefix, by, counted in _DISTINCT:
        if by in ents and counted in ents:
            # member is the counted entity: re-adding only refreshes its timestamp
            writes.append(StateWrite(f"dx:{prefix}:{ents[by]}", ents[counted]))
    return writes


def all_count_names() -> list[str]:
    names = [f"ev_{d}_{w}" for d in ("disp", "ip", "conta", "cartao") for w in WINDOWS]
    names += [f"cadastros_ip_{w}" for w in ("1h", "24h")]
    names += [f"{p}_{w}" for p, _, _ in _DISTINCT for w in _DISTINCT_WINDOWS]
    return names


def _rhythm(intervals: list[int]) -> FeatureVector:
    """Shape of the gaps between actions.

    Scale-free statistics on purpose: a screenshot-driven agent shows *regular* gaps
    (inference latency), whatever their absolute size is on a given GPU.
    """
    n = len(intervals)
    out: FeatureVector = {
        "ritmo_n": float(n),
        "ritmo_media_ms": None,
        "ritmo_cv": None,
        "ritmo_p90_p50": None,
        "ritmo_min_ms": None,
        "ritmo_frac_rapidos": None,
    }
    if n < 3:
        return out
    mean = statistics.fmean(intervals)
    ordered = sorted(intervals)
    p50 = ordered[n // 2]
    p90 = ordered[min(n - 1, math.ceil(0.9 * n) - 1)]
    out["ritmo_media_ms"] = mean
    out["ritmo_cv"] = statistics.pstdev(intervals) / mean if mean > 0 else 0.0
    out["ritmo_p90_p50"] = p90 / p50 if p50 > 0 else None
    out["ritmo_min_ms"] = float(ordered[0])
    out["ritmo_frac_rapidos"] = sum(1 for i in intervals if i < 150) / n
    return out


_NET_TYPES = ("residencial", "movel", "datacenter", "tor")
_MOBILE_UA = ("Android", "iPhone", "Mobile")
_AUTOMATION_UA = ("python", "curl", "httpx", "aiohttp", "go-http", "Headless", "node-fetch")


def compute(ev: AssessRequest, counts: Mapping[str, int], net: NetworkInfo) -> FeatureVector:
    """Pure function: same inputs, same vector, online or offline."""
    t = ev.telemetria
    ua = ev.user_agent
    f: FeatureVector = {}

    # layer 1: environment
    f["tel_coletada"] = float(t.coletada)
    f["webdriver"] = float(t.webdriver)
    f["inconsistencias_fp"] = float(t.inconsistencias_fingerprint)
    f["ua_vazio"] = float(not ua)
    f["ua_movel"] = float(any(m in ua for m in _MOBILE_UA))
    f["ua_automacao"] = float(any(m.lower() in ua.lower() for m in _AUTOMATION_UA))
    f["ja4_ausente"] = float(not ev.ja4)
    for nt in _NET_TYPES:
        f[f"rede_{nt}"] = float(net.tipo_rede == nt)
    f["ip_em_lista"] = float(net.em_lista_bloqueio)
    f["ip_reputacao"] = net.reputacao

    # layer 2: rhythm
    f.update(_rhythm(list(t.intervalos_acao_ms)))
    f["cursor_linear"] = t.trajetoria_cursor_linear
    f["cliques_centro"] = t.cliques_no_centro
    f["toque"] = t.toque
    f["toque_area"] = t.area_toque
    for d in Digitacao:
        f[f"digitacao_{d.value}"] = float(t.digitacao is d)

    # layer 3: session shape
    s = ev.sessao
    f["sessao_paginas"] = float(s.paginas_vistas)
    f["sessao_segundos"] = s.segundos_desde_inicio
    f["sessao_desvios"] = float(s.desvios_do_caminho)
    f["sessao_voltas"] = float(s.voltas)
    f["sessao_seg_por_pagina"] = (
        s.segundos_desde_inicio / s.paginas_vistas if s.paginas_vistas else None
    )

    # layer 5: traps. Signals only, never a hard rule (screen readers read hidden text too).
    f["honeypot"] = float(t.honeypot_preenchido)
    f["canario"] = float(t.canario_detectado)

    # event context
    for e in Evento:
        f[f"evento_{e.value}"] = float(ev.evento is e)
    f["valor"] = ev.valor
    f["agente_declarado"] = float(ev.assinatura_agente is not None)

    # velocity (layer 4, first half): sliding windows read before this event is written
    for name in all_count_names():
        f[name] = float(counts.get(name, 0))

    return f


FEATURE_NAMES: tuple[str, ...] = tuple(
    compute(
        AssessRequest.model_validate(
            {
                "id_requisicao": "x",
                "evento": "login",
                "momento": "2026-01-01T00:00:00Z",
                "dispositivo_id": "d",
                "sessao_id": "s",
                "ip": "192.0.2.1",
            }
        ),
        {},
        NetworkInfo(),
    )
)


def upgrade_f1(features: Mapping[str, float | None]) -> FeatureVector:
    """Bring a vector logged under feature set f1 up to f2.

    f1 predates touch telemetry, so ``toque`` is inferred, by rule, from what f1 did
    record: if clicks were measured, the pointer was a finger when the user agent is
    a phone's and a mouse otherwise. ``toque_area`` cannot be inferred and stays
    missing. This is the one place where a logged vector is amended after the fact.
    """
    out: FeatureVector = dict(features)
    clicked = out.get("cliques_centro") is not None
    out["toque"] = (1.0 if out.get("ua_movel") else 0.0) if clicked else None
    out["toque_area"] = None
    return {name: out.get(name) for name in FEATURE_NAMES}


class MemoryStateStore:
    """Offline twin of the Redis store: same sorted-set semantics, plain dicts.

    A count for window W at time T is the number of members whose score lies in
    (T - W, T]. Scores are event-time seconds.
    """

    def __init__(self) -> None:
        self._sets: dict[str, dict[str, float]] = {}

    def read(self, queries: list[WindowQuery], now: float) -> dict[str, int]:
        out: dict[str, int] = {}
        for q in queries:
            members = self._sets.get(q.key)
            lo = now - q.window_s
            out[q.name] = sum(1 for s in members.values() if lo < s <= now) if members else 0
        return out

    def write(self, writes: list[StateWrite], now: float) -> None:
        horizon = now - MAX_WINDOW_S
        for w in writes:
            members = self._sets.setdefault(w.key, {})
            members[w.member] = now
            for stale in [m for m, s in members.items() if s <= horizon]:
                del members[stale]


__all__ = [
    "FEATURE_NAMES",
    "FEATURE_VERSION",
    "MAX_WINDOW_S",
    "WINDOWS",
    "FeatureVector",
    "MemoryStateStore",
    "NetworkInfo",
    "StateWrite",
    "WindowQuery",
    "all_count_names",
    "compute",
    "ip_key",
    "state_queries",
    "state_writes",
    "upgrade_f1",
]
