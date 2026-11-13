"""Ed25519 keys and the mandate token format, shared by the shop (which issues
mandates), the engine (which verifies them) and the lab's legitimate agent."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

MANDATE_TYPE = "mandato+jws"


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_jwk(key: Ed25519PublicKey) -> dict[str, str]:
    raw = key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {"kty": "OKP", "crv": "Ed25519", "x": b64u(raw)}


def thumbprint(jwk: dict[str, str]) -> str:
    """RFC 7638 / RFC 8037 JWK thumbprint (the keyid Web Bot Auth requires)."""
    canonical = json.dumps(
        {"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"]}, separators=(",", ":"), sort_keys=True
    )
    return b64u(hashlib.sha256(canonical.encode()).digest())


def key_from_jwk(jwk: dict[str, str]) -> Ed25519PublicKey:
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise ValueError("only Ed25519 keys are accepted")
    return Ed25519PublicKey.from_public_bytes(unb64u(jwk["x"]))


def private_from_b64u(text: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(unb64u(text))


def issue_mandate(private_key: Ed25519PrivateKey, kid: str, claims: dict[str, Any]) -> str:
    """Compact JWS, EdDSA. Claims: jti, sub (account), agente (agent keyid), escopo,
    limite_valor, limite_acumulado, exp."""
    header = {"alg": "EdDSA", "typ": MANDATE_TYPE, "kid": kid}
    signing_input = (
        b64u(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64u(json.dumps(claims, separators=(",", ":"), ensure_ascii=False).encode())
    )
    return signing_input + "." + b64u(private_key.sign(signing_input.encode()))
