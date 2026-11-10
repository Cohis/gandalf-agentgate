"""Scorers return a probability plus additive per-feature contributions (log-odds).

Phase 1 ships a transparent hand-written baseline. Phase 2 swaps in LightGBM, whose
``predict(pred_contrib=True)`` yields the same shape (TreeSHAP), so the explanation
path does not change.
"""

from __future__ import annotations

import math
from typing import Protocol

from gandalf_features import FeatureVector

Contributions = dict[str, float]


class Scorer(Protocol):
    version: str

    def score(self, f: FeatureVector) -> tuple[float, Contributions]: ...


def _v(f: FeatureVector, name: str) -> float:
    v = f.get(name)
    return 0.0 if v is None else v


class HeuristicScorer:
    """'Is this automation?' baseline. Also the yardstick the trained model must beat."""

    version = "heuristica-0"
    _BIAS = -3.0

    def score(self, f: FeatureVector) -> tuple[float, Contributions]:
        c: Contributions = {}
        if not _v(f, "tel_coletada"):
            c["tel_coletada"] = 3.0
        if _v(f, "webdriver"):
            c["webdriver"] = 3.5
        if _v(f, "ua_automacao"):
            c["ua_automacao"] = 3.0
        if _v(f, "inconsistencias_fp"):
            c["inconsistencias_fp"] = min(_v(f, "inconsistencias_fp"), 5.0) * 0.6
        if _v(f, "rede_datacenter"):
            c["rede_datacenter"] = 1.2
        if _v(f, "rede_tor"):
            c["rede_tor"] = 1.5
        cv = f.get("ritmo_cv")
        if cv is not None and cv < 0.15:
            c["ritmo_cv"] = 1.5
        lin = f.get("cursor_linear")
        if lin is not None and lin > 0.95:
            c["cursor_linear"] = 1.2
        centro = f.get("cliques_centro")
        if centro is not None and centro > 0.9:
            c["cliques_centro"] = 1.0
        if _v(f, "digitacao_constante"):
            c["digitacao_constante"] = 1.0
        if _v(f, "honeypot"):
            c["honeypot"] = 1.5
        if _v(f, "canario"):
            c["canario"] = 1.5
        contas = _v(f, "contas_por_disp_24h")
        if contas >= 3:
            c["contas_por_disp_24h"] = min(contas * 0.5, 3.0)
        if _v(f, "ev_ip_10m") >= 30:
            c["ev_ip_10m"] = 1.5
        logit = self._BIAS + sum(c.values())
        return 1.0 / (1.0 + math.exp(-logit)), c
