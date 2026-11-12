from __future__ import annotations

from ipaddress import ip_address

import pytest
from gandalf_contracts import AssessRequest
from gandalf_features import (
    FEATURE_NAMES,
    MemoryStateStore,
    NetworkInfo,
    compute,
    ip_key,
    state_queries,
    state_writes,
)

from tests.conftest import make_event


def _req(**over: object) -> AssessRequest:
    return AssessRequest.model_validate(make_event(**over))


def test_vector_has_a_fixed_schema() -> None:
    human = compute(_req(), {}, NetworkInfo())
    bare = compute(_req(telemetria={"coletada": False}, conta_id=None), {}, NetworkInfo())
    assert tuple(human) == tuple(bare) == FEATURE_NAMES


def test_rhythm_is_scale_free() -> None:
    """A slow local LLM and a fast hosted one must look alike: only the shape counts."""
    fast = [2000, 2100, 1950, 2050, 2000, 1980]
    slow = [i * 5 for i in fast]
    tel = make_event()["telemetria"]
    a = compute(_req(telemetria={**tel, "intervalos_acao_ms": fast}), {}, NetworkInfo())
    b = compute(_req(telemetria={**tel, "intervalos_acao_ms": slow}), {}, NetworkInfo())
    assert a["ritmo_cv"] == pytest.approx(b["ritmo_cv"])
    assert a["ritmo_p90_p50"] == pytest.approx(b["ritmo_p90_p50"])
    assert a["ritmo_cv"] < 0.05


def test_too_few_actions_yield_missing_not_zero() -> None:
    tel = {**make_event()["telemetria"], "intervalos_acao_ms": [500]}
    f = compute(_req(telemetria=tel), {}, NetworkInfo())
    assert f["ritmo_cv"] is None and f["ritmo_n"] == 1.0


def test_ipv6_is_keyed_by_slash_64() -> None:
    a = ip_key(ip_address("2001:db8:1:2:aaaa::1"))
    b = ip_key(ip_address("2001:db8:1:2:bbbb::9"))
    assert a == b == "2001:db8:1:2::/64"
    assert ip_key(ip_address("198.51.100.4")) == "198.51.100.4"


def test_windows_are_read_before_write_and_expire() -> None:
    store = MemoryStateStore()
    req = _req()
    q = state_queries(req)
    assert store.read(q, 1000.0)["ev_disp_10m"] == 0  # never sees its own event
    store.write(state_writes(req, "av_1"), 1000.0)
    assert store.read(q, 1001.0)["ev_disp_10m"] == 1
    assert store.read(q, 1000.0 + 600)["ev_disp_10m"] == 0  # window is (now - W, now]
    assert store.read(q, 1000.0 + 600)["ev_disp_1h"] == 1


def test_distinct_counts_do_not_double_count() -> None:
    store = MemoryStateStore()
    for i, conta in enumerate(["c_a", "c_b", "c_a", "c_c"]):
        store.write(state_writes(_req(conta_id=conta), f"av_{i}"), 1000.0 + i)
    counts = store.read(state_queries(_req()), 1010.0)
    assert counts["contas_por_disp_24h"] == 3
    assert counts["ev_disp_10m"] == 4


def test_card_features_only_exist_with_a_card() -> None:
    names = {q.name for q in state_queries(_req())}
    assert "ev_cartao_10m" not in names
    names = {q.name for q in state_queries(_req(cartao_hash="k_1", evento="checkout"))}
    assert {"ev_cartao_10m", "cartoes_por_disp_24h", "cartoes_por_ip_1h"} <= names


def test_touch_is_a_feature_and_f1_vectors_are_upgraded_by_rule() -> None:
    from gandalf_features import upgrade_f1

    tel = {**make_event()["telemetria"], "toque": 1.0, "area_toque": 24.5}
    f = compute(_req(telemetria={**tel, "trajetoria_cursor_linear": None}), {}, NetworkInfo())
    assert f["toque"] == 1.0 and f["toque_area"] == 24.5 and f["cursor_linear"] is None

    old = {k: v for k, v in f.items() if k not in {"toque", "toque_area"}}
    phone = upgrade_f1({**old, "ua_movel": 1.0, "cliques_centro": 0.0})
    desk = upgrade_f1({**old, "ua_movel": 0.0, "cliques_centro": 0.1})
    raw_http = upgrade_f1({**old, "ua_movel": 0.0, "cliques_centro": None})
    assert (phone["toque"], desk["toque"], raw_http["toque"]) == (1.0, 0.0, None)
    assert tuple(phone) == FEATURE_NAMES and phone["toque_area"] is None
