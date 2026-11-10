from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from gandalf_contracts import Acao, Ator, Decisao, TipoAtor

from .rules import Rule


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

    def actor(self, score_automacao: float) -> Ator:
        # Declared agents (verified signature + mandate) arrive in phase 5.
        if score_automacao >= self.limiar_automacao:
            return Ator(tipo=TipoAtor.automacao_nao_declarada, confianca=score_automacao)
        return Ator(tipo=TipoAtor.humano, confianca=1.0 - score_automacao)

    def decide(self, score_automacao: float, fired: list[Rule]) -> tuple[Decisao, Acao]:
        """No single signal decides, except the hard rules written for the obvious cases."""
        if any(r.decisao is Decisao.negar for r in fired) or score_automacao >= self.negar_acima_de:
            return Decisao.negar, Acao.nenhuma
        if fired or score_automacao >= self.desafiar_acima_de:
            # Passkey, not captcha: multimodal models solve captchas, but the agent
            # does not hold the account owner's authenticator.
            return Decisao.desafiar, Acao.exigir_passkey
        return Decisao.aprovar, Acao.nenhuma
