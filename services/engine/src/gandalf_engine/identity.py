"""Who is acting, on whose behalf: declared agents and their mandates (phase 5).

Two independent proofs, both checked here:

1. **Web Bot Auth** (draft-ietf-webbotauth-httpsig-protocol-01, on RFC 9421 HTTP
   Message Signatures). The agent's operator publishes Ed25519 public keys at
   ``/.well-known/http-message-signatures-directory``; the agent signs each request.
   This proves WHICH OPERATOR is acting. It says nothing about authorisation.

2. **Mandate**: a compact JWS (EdDSA) issued by the shop after the account owner
   confirmed it. It names the account, the agent's key, what the agent may do and
   how much it may spend. This proves ON WHOSE BEHALF and WITHIN WHICH LIMITS.

A simplified mandate of our own, inspired by the AP2 intent mandate. It is not AP2.

Deviations from the draft, deliberate and documented:
- Operator directories are fetched only from an allowlist configured in the engine,
  never from whatever URL a request names (that would be a server-side request
  forgery hole), and over plain HTTP inside the lab network.
- Directory responses are not themselves signature-checked.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import http_sf
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from gandalf_contracts.keys import (
    MANDATE_TYPE,
    b64u,
    issue_mandate,
    key_from_jwk,
    public_jwk,
    thumbprint,
    unb64u,
)

log = logging.getLogger("gandalf.identity")

TAG = "web-bot-auth"
MAX_LIFETIME_S = 24 * 3600  # the draft recommends expires no more than 24 h after created
CLOCK_SKEW_S = 60
DIRECTORY_PATH = "/.well-known/http-message-signatures-directory"
DIRECTORY_MEDIA_TYPE = "application/http-message-signatures-directory+json"


# --------------------------------------------------------------------- keys


# ------------------------------------------------------- RFC 9421 signature base


def _sf_string(value: str) -> str:
    return http_sf.ser_item((value, {}))


def signature_base(
    components: list[tuple[str, dict[str, Any]]],
    params: dict[str, Any],
    values: dict[str, str],
) -> tuple[bytes, str]:
    """Builds the RFC 9421 signature base. ``values`` maps a component id, as it
    appears in the base (e.g. '"@authority"'), to its value. Returns the base and
    the serialised signature-params, which must equal what the signer declared."""
    params_value = http_sf.ser_dictionary({"x": (components, params)})[2:]
    lines = []
    for name, cparams in components:
        cid = http_sf.ser_item((name, cparams))
        if cid not in values:
            raise ValueError(f"no value for covered component {cid}")
        lines.append(f"{cid}: {values[cid]}")
    lines.append(f'"@signature-params": {params_value}')
    return "\n".join(lines).encode(), params_value


def sign_request(
    private_key: Ed25519PrivateKey,
    agent_url: str,
    *,
    method: str,
    authority: str,
    path: str,
    label: str = "sig1",
    lifetime_s: int = 300,
    nonce: str | None = None,
    now: float | None = None,
) -> dict[str, str]:
    """Headers an agent adds to a request (used by the lab's legitimate agent)."""
    now = int(now if now is not None else time.time())
    keyid = thumbprint(public_jwk(private_key.public_key()))
    components = [
        ("@method", {}),
        ("@authority", {}),
        ("@path", {}),
        ("signature-agent", {"key": label}),
    ]
    params: dict[str, Any] = {
        "created": now,
        "expires": now + lifetime_s,
        "keyid": keyid,
        "alg": "ed25519",
        "tag": TAG,
    }
    # A fresh nonce per request: two identical requests in the same second must not
    # produce the same signature, or the second would look like a replay.
    params["nonce"] = nonce or b64u(os.urandom(16))
    agent_member = _sf_string(agent_url)
    values = {
        '"@method"': method.upper(),
        '"@authority"': authority.lower(),
        '"@path"': path,
        f'"signature-agent";key="{label}"': agent_member,
    }
    base, params_value = signature_base(components, params, values)
    sig = private_key.sign(base)
    return {
        "Signature-Agent": f"{label}={agent_member}",
        "Signature-Input": f"{label}={params_value}",
        "Signature": f"{label}=:{base64.b64encode(sig).decode()}:",
    }


# ------------------------------------------------------------------ verifying


@dataclass(frozen=True, slots=True)
class VerifiedAgent:
    operator: str  # the directory URL the key was found in
    keyid: str
    expires: int
    signature: str  # used for replay protection


class SignatureRejected(ValueError):
    """The request claims to be a declared agent, but the claim does not hold."""


@dataclass
class OperatorDirectory:
    """Keys of the operators this shop trusts, fetched from an allowlist only."""

    urls: tuple[str, ...]
    refresh_s: float = 600.0
    keys: dict[str, dict[str, Ed25519PublicKey]] = field(
        default_factory=dict
    )  # url -> keyid -> key
    fetched_at: float = 0.0

    async def refresh(self, client: Any) -> None:
        """``client``: an httpx.AsyncClient. Failures keep the previous keys."""
        for url in self.urls:
            try:
                r = await client.get(url.rstrip("/") + DIRECTORY_PATH, follow_redirects=False)
                if r.status_code != 200:  # the draft: anything but 200 is a failure
                    raise ValueError(f"status {r.status_code}")
                found = {}
                for jwk in r.json().get("keys", []):
                    try:
                        found[thumbprint(jwk)] = key_from_jwk(jwk)
                    except (KeyError, ValueError):
                        continue
                self.keys[url] = found
            except Exception as exc:  # network, JSON: a directory that cannot be read
                log.warning("operator directory %s unavailable: %s", url, exc)
        self.fetched_at = time.monotonic()

    def stale(self) -> bool:
        return time.monotonic() - self.fetched_at > self.refresh_s


def verify_signature(
    *,
    signature: str,
    signature_input: str,
    signature_agent: str,
    method: str,
    authority: str,
    path: str,
    directory: OperatorDirectory,
    now: float | None = None,
) -> VerifiedAgent:
    now = now if now is not None else time.time()
    try:
        agents = http_sf.parse(signature_agent.encode(), tltype="dictionary")
        inputs = http_sf.parse(signature_input.encode(), tltype="dictionary")
        sigs = http_sf.parse(signature.encode(), tltype="dictionary")
    except Exception as exc:
        raise SignatureRejected(f"malformed signature headers ({exc})") from exc
    labels = [lb for lb in inputs if lb in sigs and lb in agents]
    if not labels:
        raise SignatureRejected("no signature label shared by the three headers")
    label = labels[0]
    agent_url = agents[label][0]
    if not isinstance(agent_url, str) or agent_url not in directory.keys:
        raise SignatureRejected("operator not in this shop's trusted directory list")
    components, params = inputs[label]
    names = {(n, tuple(sorted(p.items()))) for n, p in components}
    if not ({("@authority", ()), ("@target-uri", ())} & names):
        raise SignatureRejected("signature does not cover @authority or @target-uri")
    if ("signature-agent", (("key", label),)) not in names:
        raise SignatureRejected("signature does not cover its own Signature-Agent")
    if params.get("tag") != TAG:
        raise SignatureRejected("tag is not web-bot-auth")
    created, expires, keyid = params.get("created"), params.get("expires"), params.get("keyid")
    if not (isinstance(created, int) and isinstance(expires, int) and isinstance(keyid, str)):
        raise SignatureRejected("created, expires and keyid are required")
    if created > now + CLOCK_SKEW_S or expires < now - CLOCK_SKEW_S:
        raise SignatureRejected("signature expired or not yet valid")
    if expires - created > MAX_LIFETIME_S:
        raise SignatureRejected("signature lifetime longer than 24 h")
    key = directory.keys[agent_url].get(keyid)
    if key is None:
        raise SignatureRejected("keyid not published by the operator")
    values = {
        '"@method"': method.upper(),
        '"@authority"': authority.lower(),
        '"@path"': path,
        f'"signature-agent";key="{label}"': _sf_string(agent_url),
    }
    try:
        base, _ = signature_base(components, params, values)
    except ValueError as exc:
        raise SignatureRejected(str(exc)) from exc
    raw = sigs[label][0]
    if not isinstance(raw, bytes):
        raise SignatureRejected("signature value is not a byte sequence")
    try:
        key.verify(raw, base)
    except InvalidSignature as exc:
        raise SignatureRejected("signature does not verify") from exc
    return VerifiedAgent(agent_url, keyid, expires, base64.b64encode(raw).decode())


# ------------------------------------------------------------------- mandates


@dataclass(frozen=True, slots=True)
class Mandate:
    jti: str
    conta_id: str
    agente: str  # keyid of the operator key this mandate was granted to
    escopo: frozenset[str]  # events the agent may perform
    limite_valor: float  # per operation
    limite_acumulado: float  # over the mandate's life
    exp: int


class MandateRejected(ValueError):
    pass


def verify_mandate(token: str, issuers: dict[str, Ed25519PublicKey], now: float) -> Mandate:
    try:
        h64, p64, s64 = token.split(".")
        header = json.loads(unb64u(h64))
        claims = json.loads(unb64u(p64))
        sig = unb64u(s64)
    except (ValueError, json.JSONDecodeError) as exc:
        raise MandateRejected("malformed mandate") from exc
    if header.get("alg") != "EdDSA" or header.get("typ") != MANDATE_TYPE:
        raise MandateRejected("unexpected mandate type")
    key = issuers.get(str(header.get("kid")))
    if key is None:
        raise MandateRejected("mandate issued by an unknown key")
    try:
        key.verify(sig, f"{h64}.{p64}".encode())
    except InvalidSignature as exc:
        raise MandateRejected("mandate signature does not verify") from exc
    try:
        m = Mandate(
            jti=str(claims["jti"]),
            conta_id=str(claims["sub"]),
            agente=str(claims["agente"]),
            escopo=frozenset(str(e) for e in claims["escopo"]),
            limite_valor=float(claims["limite_valor"]),
            limite_acumulado=float(claims["limite_acumulado"]),
            exp=int(claims["exp"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MandateRejected("mandate is missing claims") from exc
    if m.exp < now:
        raise MandateRejected("mandate expired")
    return m


# ------------------------------------------------------------- the engine's gate


@dataclass(frozen=True, slots=True)
class Declaration:
    agent: VerifiedAgent | None = None
    rejected: str | None = None  # a signature was presented but did not hold
    mandate: Mandate | None = None
    outside: str | None = None  # why the action falls outside the mandate (None: inside)


@dataclass
class AgentGate:
    """Everything the engine needs to judge a declared agent, kept in memory: no
    network call is ever made while answering a request."""

    directory: OperatorDirectory
    issuer_urls: tuple[str, ...] = ()
    issuers: dict[str, Ed25519PublicKey] = field(default_factory=dict)  # kid -> key
    complete: bool = False  # every directory and issuer answered on the last refresh

    async def refresh(self, client: Any) -> None:
        await self.directory.refresh(client)
        complete = bool(self.directory.keys) or not self.directory.urls
        for url in self.issuer_urls:
            try:
                r = await client.get(
                    url.rstrip("/") + "/.well-known/gandalf-mandato", follow_redirects=False
                )
                r.raise_for_status()
                body = r.json()
                self.issuers[str(body["kid"])] = key_from_jwk(body["jwk"])
            except Exception as exc:
                complete = False
                log.warning("mandate issuer %s unavailable: %s", url, exc)
        self.complete = complete

    async def check(self, req: Any, redis: Any, now: float | None = None) -> Declaration:
        """``req``: the AssessRequest. Signature freshness uses the wall clock (the
        request is happening now), never the lab's simulated event time."""
        a = req.assinatura_agente
        if a is None:
            return Declaration()
        now = now if now is not None else time.time()
        try:
            agent = verify_signature(
                signature=a.signature,
                signature_input=a.signature_input,
                signature_agent=a.signature_agent,
                method=a.metodo,
                authority=a.autoridade,
                path=a.caminho,
                directory=self.directory,
                now=now,
            )
        except SignatureRejected as exc:
            return Declaration(rejected=str(exc))
        # One signature, one request: a captured signature replayed is not the agent.
        ttl = max(int(agent.expires - now), 1) + CLOCK_SKEW_S
        if not await redis.set(f"wba:{agent.keyid}:{agent.signature}", 1, nx=True, ex=ttl):
            return Declaration(rejected="signature already used (replay)")
        if not req.mandato:
            return Declaration(agent=agent, outside="no mandate from the account owner")
        try:
            m = verify_mandate(req.mandato, self.issuers, now)
        except MandateRejected as exc:
            return Declaration(agent=agent, outside=f"mandate rejected: {exc}")
        if await redis.sismember("mandatos:revogados", m.jti):
            return Declaration(agent=agent, mandate=m, outside="mandate revoked by the owner")
        if m.agente != agent.keyid:
            return Declaration(agent=agent, mandate=m, outside="mandate granted to another agent")
        if req.conta_id != m.conta_id:
            return Declaration(agent=agent, mandate=m, outside="mandate is for another account")
        if req.evento.value not in m.escopo:
            return Declaration(
                agent=agent, mandate=m, outside=f"'{req.evento.value}' not in the mandate"
            )
        valor = float(req.valor or 0.0)
        if valor > m.limite_valor:
            return Declaration(agent=agent, mandate=m, outside="value above the per-purchase limit")
        spent = float(await redis.get(f"mandato:gasto:{m.jti}") or 0.0)
        if req.evento.value == "checkout" and spent + valor > m.limite_acumulado:
            return Declaration(
                agent=agent, mandate=m, outside="the mandate's total budget is used up"
            )
        return Declaration(agent=agent, mandate=m)

    @staticmethod
    async def record_spend(redis: Any, m: Mandate, valor: float) -> None:
        key = f"mandato:gasto:{m.jti}"
        await redis.incrbyfloat(key, valor)
        await redis.expireat(key, m.exp + 86_400)


__all__ = [
    "AgentGate",
    "Declaration",
    "Mandate",
    "MandateRejected",
    "OperatorDirectory",
    "SignatureRejected",
    "VerifiedAgent",
    "issue_mandate",
    "public_jwk",
    "sign_request",
    "thumbprint",
    "verify_mandate",
    "verify_signature",
]
