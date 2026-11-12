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

    def automation_flag(self, score: float, t: Thresholds | None) -> Decisao:
        desafiar = t.desafiar if t else self.desafiar_acima_de
        negar = t.negar if t else self.negar_acima_de
        if score >= negar:
            return Decisao.negar
        return Decisao.desafiar if score >= desafiar else Decisao.aprovar

    @staticmethod
    def fraud_flag(score: float | None, t: Thresholds | None) -> Decisao:
        if score is None or t is None:
            return Decisao.aprovar
        if score >= t.negar:
            return Decisao.negar
        return Decisao.desafiar if score >= t.desafiar else Decisao.aprovar

    def decide(
        self,
        score_automacao: float,
        fired: list[Rule],
        t: Thresholds | None = None,
        *,
        score_fraude: float | None = None,
        t_fraude: Thresholds | None = None,
    ) -> tuple[Decisao, Acao]:
        """Two questions, answered separately and then crossed:

        - Is this undeclared automation? (behaviour; thresholds keep false positives on
          people at 1 %)
        - Is this fraud? (what is being done: velocity, value; thresholds minimise the
          expected cost of mistakes)

        The stricter answer wins. No single signal decides, except the hard rules
        written for the obvious cases.
        """
        auto = self.automation_flag(score_automacao, t)
        fraud = self.fraud_flag(score_fraude, t_fraude)
        if any(r.decisao is Decisao.negar for r in fired) or Decisao.negar in (auto, fraud):
            return Decisao.negar, Acao.nenhuma
        if fired or auto is Decisao.desafiar:
            # Passkey, not captcha: multimodal models solve captchas, but the agent
            # does not hold the account owner's authenticator.
            return Decisao.desafiar, Acao.exigir_passkey
        if fraud is Decisao.desafiar:
            # A person who looks human but is doing something risky: confirm with the
            # account or card owner outside this session.
            return Decisao.desafiar, Acao.confirmar_fora_da_sessao
        return Decisao.aprovar, Acao.nenhuma
