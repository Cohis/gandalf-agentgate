from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from gandalf_contracts import Acao, Ator, Decisao, TipoAtor

from .rules import Rule
from .scoring import Thresholds


@dataclass(frozen=True, slots=True)
class Policy:
    versao: str
    limiar_automacao: float
    desafiar_acima_de: float
    negar_acima_de: float

    @classmethod
    def load(cls, path: Path) -> Policy:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        p = cls(
            versao=str(raw["versao"]),
            limiar_automacao=float(raw["ator"]["limiar_automacao"]),
            desafiar_acima_de=float(raw["decisao"]["desafiar_acima_de"]),
            negar_acima_de=float(raw["decisao"]["negar_acima_de"]),
        )
        if not 0 < p.desafiar_acima_de <= p.negar_acima_de <= 1:
            raise ValueError("policy thresholds must satisfy 0 < desafiar <= negar <= 1")
        return p

    def actor(self, score_automacao: float, t: Thresholds | None = None) -> Ator:
        # Declared agents (verified signature + mandate) arrive in phase 5.
        limiar = t.desafiar if t else self.limiar_automacao
        if score_automacao >= limiar:
            return Ator(tipo=TipoAtor.automacao_nao_declarada, confianca=score_automacao)
        return Ator(tipo=TipoAtor.humano, confianca=1.0 - score_automacao)

    def decide(
        self, score_automacao: float, fired: list[Rule], t: Thresholds | None = None
    ) -> tuple[Decisao, Acao]:
        """No single signal decides, except the hard rules written for the obvious cases.
        A trained model brings its own operating points; otherwise the file's apply."""
        desafiar = t.desafiar if t else self.desafiar_acima_de
        negar = t.negar if t else self.negar_acima_de
        if any(r.decisao is Decisao.negar for r in fired) or score_automacao >= negar:
            return Decisao.negar, Acao.nenhuma
        if fired or score_automacao >= desafiar:
            # Passkey, not captcha: multimodal models solve captchas, but the agent
            # does not hold the account owner's authenticator.
            return Decisao.desafiar, Acao.exigir_passkey
        return Decisao.aprovar, Acao.nenhuma
