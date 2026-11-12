"""Single source of truth for the engine's wire contracts.

Field names are Portuguese on purpose: they are the public API of the engine.
Everything else in the code base is English.
"""

from __future__ import annotations

from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

Id = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:\-]+$")]
ShortText = Annotated[str, StringConstraints(max_length=512)]
Interval = Annotated[int, Field(ge=0, le=3_600_000)]
Fraction = Annotated[float, Field(ge=0.0, le=1.0)]


class Strict(BaseModel):
    """Unknown fields are rejected: malformed input must fail loudly (422)."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Evento(StrEnum):
    cadastro = "cadastro"
    login = "login"
    checkout = "checkout"
    adicionar_carrinho = "adicionar_carrinho"


class Digitacao(StrEnum):
    colada = "colada"
    constante = "constante"
    humana = "humana"
    ausente = "ausente"


class Decisao(StrEnum):
    aprovar = "aprovar"
    desafiar = "desafiar"
    negar = "negar"


class Acao(StrEnum):
    nenhuma = "nenhuma"
    exigir_passkey = "exigir_passkey"
    confirmar_fora_da_sessao = "confirmar_fora_da_sessao"


class TipoAtor(StrEnum):
    humano = "humano"
    agente_declarado = "agente_declarado"
    automacao_nao_declarada = "automacao_nao_declarada"


class Telemetria(Strict):
    """Aggregates computed in the browser. Raw mouse paths never leave the page (LGPD)."""

    coletada: bool = True  # False when the page script never ran (e.g. raw HTTP client)
    intervalos_acao_ms: Annotated[list[Interval], Field(max_length=200)] = []
    trajetoria_cursor_linear: Fraction | None = None
    cliques_no_centro: Fraction | None = None
    # Share of pointer activations made by touch (None: no pointer was used at all).
    # A phone user has no cursor path; without this, that looks exactly like a script.
    toque: Fraction | None = None
    area_toque: Annotated[float, Field(ge=0, le=500)] | None = None  # mean contact size, px
    digitacao: Digitacao = Digitacao.ausente
    webdriver: bool = False
    inconsistencias_fingerprint: Annotated[int, Field(ge=0, le=50)] = 0
    honeypot_preenchido: bool = False
    canario_detectado: bool = False


class Sessao(Strict):
    """Session-shape aggregates kept by the shop backend (layer 3)."""

    paginas_vistas: Annotated[int, Field(ge=0, le=100_000)] = 0
    segundos_desde_inicio: Annotated[float, Field(ge=0, le=31_536_000)] = 0.0
    desvios_do_caminho: Annotated[int, Field(ge=0, le=100_000)] = 0
    voltas: Annotated[int, Field(ge=0, le=100_000)] = 0


class AssinaturaAgente(Strict):
    """Web Bot Auth material as seen by the shop, so the engine can rebuild the signature base."""

    signature: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    signature_input: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    signature_agent: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    metodo: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3,7}$")]
    autoridade: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    caminho: Annotated[str, StringConstraints(min_length=1, max_length=2048)]


class DadosCadastro(Strict):
    nome: ShortText = ""
    endereco: ShortText = ""
    bio: Annotated[str, StringConstraints(max_length=2000)] = ""


class AssessRequest(Strict):
    id_requisicao: Id  # idempotency key chosen by the shop
    evento: Evento
    momento: AwareDatetime  # event time set by the shop backend, never by the end user
    conta_id: Id | None = None
    dispositivo_id: Id
    sessao_id: Id
    ip: IPv4Address | IPv6Address  # as seen by the trusted proxy
    user_agent: Annotated[str, StringConstraints(max_length=1024)] = ""
    ja4: Annotated[str, StringConstraints(max_length=64)] | None = None
    http2_fingerprint: Annotated[str, StringConstraints(max_length=256)] | None = None
    cartao_hash: Id | None = None  # the card number itself never reaches the engine
    valor: Annotated[float, Field(ge=0, le=10_000_000)] | None = None
    telemetria: Telemetria = Telemetria(coletada=False)
    sessao: Sessao = Sessao()
    assinatura_agente: AssinaturaAgente | None = None
    mandato: Annotated[str, StringConstraints(max_length=8192)] | None = None
    dados_cadastro: DadosCadastro | None = None


class Ator(Strict):
    tipo: TipoAtor
    confianca: Fraction


class AssessResponse(Strict):
    avaliacao_id: str
    decisao: Decisao
    acao: Acao
    ator: Ator
    score_automacao: Fraction
    score_fraude: Fraction | None = None  # None until the fraud model exists (phase 3)
    motivos: list[str]
    regras_acionadas: list[str]
    desafio_id: str | None = None
    versao_modelo: str
    versao_features: str
    versao_politica: str
    modo_degradado: bool = False
    latencia_ms: float


class LabelIn(Strict):
    """Ground truth that arrives later, joined to the decision by ``avaliacao_id``."""

    avaliacao_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    eh_automacao: bool
    eh_fraude: bool
    ator_real: TipoAtor
    nivel_atacante: Annotated[int, Field(ge=0, le=5)] = 0  # 0 = human
    perfil: Annotated[str, StringConstraints(max_length=64)] = ""
    fonte: Annotated[str, StringConstraints(max_length=64)] = "simulador"


class LabelBatch(Strict):
    rotulos: Annotated[list[LabelIn], Field(min_length=1, max_length=5000)]


__all__ = [
    "Acao",
    "AssessRequest",
    "AssessResponse",
    "AssinaturaAgente",
    "Ator",
    "DadosCadastro",
    "Decisao",
    "Digitacao",
    "Evento",
    "LabelBatch",
    "LabelIn",
    "Sessao",
    "Telemetria",
    "TipoAtor",
]
