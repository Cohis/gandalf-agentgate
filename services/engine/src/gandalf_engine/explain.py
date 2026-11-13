"""Turn the top positive contributions into sentences an analyst can read.

A contribution says *that* a feature pushed the score up; the sentence states the
value that did it. Each phrase therefore describes what was observed, and returns
None when it has nothing true to say for that value (the generic fallback is used).
"""

from __future__ import annotations

from collections.abc import Callable

from gandalf_features import FeatureVector

from .scoring import Contributions

Phrase = Callable[[FeatureVector], str | None]


def _n(f: FeatureVector, k: str) -> float:
    return f.get(k) or 0.0


def _flag(name: str, when_set: str, when_unset: str | None = None) -> Phrase:
    return lambda f: when_set if _n(f, name) else when_unset


def _rhythm(f: FeatureVector) -> str | None:
    cv = f.get("ritmo_cv")
    if cv is None:
        return "poucas ações medidas na sessão para avaliar o ritmo"
    kind = "regular demais" if cv < 0.5 else "irregular"
    return (
        f"ritmo de ações {kind} (variação de {cv:.0%} em torno de "
        f"{_n(f, 'ritmo_media_ms') / 1000:.1f} s)"
    )


def _cursor(f: FeatureVector) -> str | None:
    lin = f.get("cursor_linear")
    if lin is None:
        return "nenhum movimento de mouse entre os cliques"
    return f"trajetória do cursor com {lin:.0%} de linearidade"


def _center(f: FeatureVector) -> str | None:
    c = f.get("cliques_centro")
    if c is None:
        return "nenhum clique de mouse medido"
    return f"{c:.0%} dos cliques no centro exato dos elementos"


def _detours(f: FeatureVector) -> str | None:
    n = _n(f, "sessao_desvios")
    if n == 0:
        return "sessão foi direto ao objetivo, sem visitar nenhuma outra página"
    return f"{n:.0f} páginas visitadas fora do caminho até o objetivo"


def _count(name: str, text: str) -> Phrase:
    return lambda f: text.format(n=_n(f, name))


_PHRASES: dict[str, Phrase] = {
    "tel_coletada": lambda f: (
        None
        if _n(f, "tel_coletada")
        else "o script da página nunca rodou: requisição feita fora de um navegador"
    ),
    "webdriver": _flag("webdriver", "navegador se declara controlado por automação"),
    "ua_automacao": _flag(
        "ua_automacao", "user-agent de biblioteca de automação ou navegador headless"
    ),
    "ua_vazio": _flag("ua_vazio", "requisição sem user-agent"),
    "ja4_ausente": _flag("ja4_ausente", "impressão digital TLS (JA4) ausente"),
    "inconsistencias_fp": _count(
        "inconsistencias_fp", "{n:.0f} inconsistências no fingerprint do navegador"
    ),
    "rede_datacenter": _flag("rede_datacenter", "IP de datacenter, incomum para um cliente final"),
    "rede_tor": _flag("rede_tor", "IP de saída da rede Tor"),
    "ip_em_lista": _flag("ip_em_lista", "IP presente em lista pública de bloqueio"),
    "ritmo_cv": _rhythm,
    "ritmo_p90_p50": _rhythm,
    "ritmo_media_ms": _rhythm,
    "ritmo_min_ms": _rhythm,
    "ritmo_frac_rapidos": _count(
        "ritmo_frac_rapidos", "{n:.0%} das ações com menos de 150 ms entre uma e outra"
    ),
    "ritmo_n": _count("ritmo_n", "{n:.0f} ações medidas na sessão"),
    "cursor_linear": _cursor,
    "cliques_centro": _center,
    "digitacao_constante": _flag(
        "digitacao_constante", "digitação em cadência constante, sem variação humana"
    ),
    "digitacao_colada": _flag("digitacao_colada", "campos preenchidos sem nenhuma tecla digitada"),
    "digitacao_humana": _flag(
        "digitacao_humana", "digitação com cadência humana", "sem cadência humana de digitação"
    ),
    "digitacao_ausente": _flag("digitacao_ausente", "nenhuma digitação nesta página"),
    "honeypot": _flag("honeypot", "campo invisível do formulário foi preenchido"),
    "canario": _flag("canario", "instrução escondida na página foi seguida"),
    "sessao_desvios": _detours,
    "sessao_paginas": _count("sessao_paginas", "{n:.0f} páginas vistas na sessão"),
    "sessao_voltas": _count("sessao_voltas", "{n:.0f} retornos a páginas já vistas"),
    "sessao_segundos": _count("sessao_segundos", "ação feita {n:.0f} s após o início da sessão"),
    "sessao_seg_por_pagina": _count("sessao_seg_por_pagina", "{n:.1f} s por página, em média"),
    "contas_por_disp_24h": _count(
        "contas_por_disp_24h", "{n:.0f} contas distintas neste dispositivo em 24 h"
    ),
    "contas_por_ip_24h": _count("contas_por_ip_24h", "{n:.0f} contas distintas neste IP em 24 h"),
    "cartoes_por_disp_24h": _count(
        "cartoes_por_disp_24h", "{n:.0f} cartões distintos neste dispositivo em 24 h"
    ),
    "cadastros_ip_24h": _count("cadastros_ip_24h", "{n:.0f} cadastros deste IP em 24 h"),
    "ev_ip_10m": _count("ev_ip_10m", "{n:.0f} eventos deste IP em 10 min"),
    "ev_disp_10m": _count("ev_disp_10m", "{n:.0f} eventos deste dispositivo em 10 min"),
    "ev_conta_10m": _count("ev_conta_10m", "{n:.0f} eventos desta conta em 10 min"),
    "ev_cartao_24h": _count("ev_cartao_24h", "{n:.0f} eventos com este cartão em 24 h"),
    "valor": _count("valor", "valor da operação: R$ {n:.2f}"),
}


_DIM = {
    "disp": "deste dispositivo",
    "ip": "deste IP",
    "conta": "desta conta",
    "cartao": "com este cartão",
}
_WIN = {"10m": "10 min", "1h": "1 h", "24h": "24 h"}
_DISTINCT = {
    "contas_por_disp": "contas distintas neste dispositivo",
    "contas_por_ip": "contas distintas neste IP",
    "disps_por_conta": "dispositivos diferentes nesta conta",
    "cartoes_por_disp": "cartões distintos neste dispositivo",
    "cartoes_por_ip": "cartões distintos neste IP",
}


def _velocity(name: str) -> Phrase | None:
    """Phrases for the windowed counts, built from the feature name."""
    stem, _, win = name.rpartition("_")
    if win not in _WIN:
        return None
    if stem.startswith("ev_") and stem[3:] in _DIM:
        dim = _DIM[stem[3:]]
        return lambda f: f"{_n(f, name):.0f} eventos {dim} em {_WIN[win]}"
    if stem in _DISTINCT:
        text = _DISTINCT[stem]
        return lambda f: f"{_n(f, name):.0f} {text} em {_WIN[win]}"
    if stem == "cadastros_ip":
        return lambda f: f"{_n(f, name):.0f} cadastros deste IP em {_WIN[win]}"
    return None


_SCALE: dict[str, Phrase] = {
    "escala_mesmo_perfil_6h": lambda f: (
        f"{_n(f, 'escala_mesmo_perfil_6h'):.0f} cadastros nas últimas 6 h preenchidos exatamente "
        "no mesmo formato"
    ),
    "escala_mesmo_endereco_6h": lambda f: (
        f"{_n(f, 'escala_mesmo_endereco_6h'):.0f} cadastros nas últimas 6 h com o endereço escrito "
        "no mesmo formato"
    ),
    "escala_mesmo_email_6h": lambda f: (
        f"{_n(f, 'escala_mesmo_email_6h'):.0f} cadastros nas últimas 6 h com e-mail "
        "no mesmo formato"
    ),
    "escala_semelhantes_6h": lambda f: (
        f"{_n(f, 'escala_semelhantes_6h'):.0f} cadastros nas últimas 6 h com bio parecida"
    ),
    "escala_cadastros_6h": lambda f: (
        f"{_n(f, 'escala_cadastros_6h'):.0f} cadastros na loja nas últimas 6 h, acima do normal"
    ),
    "foco_teclado": lambda f: None,  # it protects keyboard users; never a reason against
    "toque": lambda f: None,
    "ua_movel": lambda f: None,
}


def reasons(f: FeatureVector, contributions: Contributions, top: int = 3) -> list[str]:
    ranked = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)
    out: list[str] = []
    for name, weight in ranked:
        if weight <= 0 or len(out) >= top:
            break
        if name.startswith("evento_"):
            continue  # which page it is says nothing about who is acting
        phrase = _PHRASES.get(name) or _SCALE.get(name) or _velocity(name)
        text = phrase(f) if phrase else None
        if text is None:
            continue  # better no reason than an unreadable one
        if text not in out:  # several rhythm features share one sentence
            out.append(text)
    return out or ["combinação de sinais acima do esperado"]
