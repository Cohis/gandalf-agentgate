"""Turn the top positive contributions into sentences an analyst can read."""

from __future__ import annotations

from collections.abc import Callable

from gandalf_features import FeatureVector

from .scoring import Contributions


def _n(f: FeatureVector, k: str) -> float:
    return f.get(k) or 0.0


_PHRASES: dict[str, Callable[[FeatureVector], str]] = {
    "tel_coletada": lambda f: (
        "o script da página nunca rodou: requisição feita fora de um navegador"
    ),
    "webdriver": lambda f: "navegador se declara controlado por automação",
    "ua_automacao": lambda f: "user-agent de biblioteca de automação ou navegador headless",
    "inconsistencias_fp": lambda f: (
        f"{_n(f, 'inconsistencias_fp'):.0f} inconsistências no fingerprint do navegador"
    ),
    "rede_datacenter": lambda f: "IP de datacenter, incomum para um cliente final",
    "rede_tor": lambda f: "IP de saída da rede Tor",
    "ritmo_cv": lambda f: (
        f"ritmo de ações regular demais (variação de {_n(f, 'ritmo_cv'):.0%} "
        f"em torno de {_n(f, 'ritmo_media_ms') / 1000:.1f} s)"
    ),
    "cursor_linear": lambda f: (
        f"trajetória do cursor quase reta ({_n(f, 'cursor_linear'):.0%} de linearidade)"
    ),
    "cliques_centro": lambda f: (
        f"{_n(f, 'cliques_centro'):.0%} dos cliques no centro exato dos elementos"
    ),
    "digitacao_constante": lambda f: "digitação em cadência constante, sem variação humana",
    "honeypot": lambda f: "campo invisível do formulário foi preenchido",
    "canario": lambda f: "instrução escondida na página foi seguida",
    "contas_por_disp_24h": lambda f: (
        f"{_n(f, 'contas_por_disp_24h'):.0f} contas distintas neste dispositivo em 24 h"
    ),
    "ev_ip_10m": lambda f: f"{_n(f, 'ev_ip_10m'):.0f} eventos deste IP em 10 min",
}


def reasons(f: FeatureVector, contributions: Contributions, top: int = 3) -> list[str]:
    ranked = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)
    out: list[str] = []
    for name, weight in ranked:
        if weight <= 0 or len(out) >= top:
            break
        phrase = _PHRASES.get(name)
        out.append(phrase(f) if phrase else f"sinal '{name}' acima do esperado")
    return out
