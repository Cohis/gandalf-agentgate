"""The trained-model path: loading, feature-version guard, explanations, hot reload."""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient
from gandalf_contracts import AssessRequest
from gandalf_engine.app import create_app
from gandalf_engine.config import Settings
from gandalf_engine.explain import reasons
from gandalf_engine.scoring import HeuristicScorer, LightGBMScorer, ModelError, load_scorer
from gandalf_engine.storage import NullStorage
from gandalf_features import FEATURE_NAMES, FEATURE_VERSION, NetworkInfo, compute

from tests.conftest import KEY, make_event


def write_model(root: Path, versao: str, *, features: str = FEATURE_VERSION) -> Path:
    """A tiny real LightGBM model: 'automation' iff every click hits dead centre."""
    rng = np.random.default_rng(0)
    x = rng.random((400, len(FEATURE_NAMES)))
    centre = FEATURE_NAMES.index("cliques_centro")
    y = (x[:, centre] > 0.8).astype(int)
    booster = lgb.train(
        {"objective": "binary", "verbosity": -1, "num_leaves": 4, "min_data_in_leaf": 5},
        lgb.Dataset(x, y, feature_name=list(FEATURE_NAMES)),
        num_boost_round=20,
    )
    d = root / versao
    d.mkdir(parents=True)
    booster.save_model(d / "model.txt")
    meta = {
        "versao": versao,
        "versao_features": features,
        "features": list(FEATURE_NAMES),
        "calibracao": {"a": 1.0, "b": 0.0},
        "limiares": {"desafiar": 0.5, "negar": 0.999},
    }
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def vector(**telemetry: object) -> dict[str, float | None]:
    tel = {**make_event()["telemetria"], **telemetry}
    return compute(AssessRequest.model_validate(make_event(telemetria=tel)), {}, NetworkInfo())


def test_model_scores_and_explains_with_its_own_contributions(tmp_path: Path) -> None:
    scorer = LightGBMScorer(write_model(tmp_path, "m1"))
    bot, human = vector(cliques_no_centro=1.0), vector(cliques_no_centro=0.1)
    p_bot, contrib = scorer.score(bot)
    p_human, _ = scorer.score(human)
    assert p_bot > 0.8 > 0.2 > p_human
    assert max(contrib, key=contrib.get) == "cliques_centro"  # type: ignore[arg-type]
    assert reasons(bot, contrib)[0] == "100% dos cliques no centro exato dos elementos"


def test_missing_values_are_handled_not_zero_filled(tmp_path: Path) -> None:
    scorer = LightGBMScorer(write_model(tmp_path, "m1"))
    p, _ = scorer.score(vector(cliques_no_centro=None, trajetoria_cursor_linear=None))
    assert 0.0 <= p <= 1.0


def test_model_trained_on_other_features_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ModelError, match="trained on features f0"):
        LightGBMScorer(write_model(tmp_path, "old", features="f0"))


def test_no_active_model_falls_back_to_the_baseline(tmp_path: Path) -> None:
    assert isinstance(load_scorer(tmp_path), HeuristicScorer)
    assert isinstance(load_scorer(None), HeuristicScorer)


def test_hot_reload_swaps_models_without_restart(tmp_path: Path) -> None:
    app = create_app(
        Settings(service_key=KEY, model_dir=tmp_path), redis=FakeAsyncRedis(), storage=NullStorage()
    )
    h = {"X-Gandalf-Key": KEY}
    centred = {**make_event()["telemetria"], "cliques_no_centro": 1.0}
    with TestClient(app) as c:
        first = c.post("/avaliar", json=make_event(telemetria=centred), headers=h).json()
        assert first["versao_modelo"] == "heuristica-0"

        write_model(tmp_path, "m1")
        (tmp_path / "ACTIVE").write_text("m1\n")
        assert c.post("/admin/recarregar-modelo", headers=h).json() == {"versao_modelo": "m1"}
        out = c.post("/avaliar", json=make_event(telemetria=centred), headers=h).json()
        assert out["versao_modelo"] == "m1"
        assert out["decisao"] == "desafiar"  # the model's own threshold (0.5) applies
        assert "centro exato" in out["motivos"][0]

        # a bad model is refused and the good one keeps serving
        write_model(tmp_path, "bad", features="f0")
        (tmp_path / "ACTIVE").write_text("bad\n")
        assert c.post("/admin/recarregar-modelo", headers=h).status_code == 409
        still = c.post("/avaliar", json=make_event(), headers=h).json()
        assert still["versao_modelo"] == "m1"
        assert c.post("/admin/recarregar-modelo").status_code == 401
