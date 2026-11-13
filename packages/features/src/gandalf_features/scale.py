"""Scale layer: does this sign-up look like many others that arrived recently?

An account farm can make every single session look human. What it cannot easily
hide is that dozens of identities were produced the same way: an LLM asked for
"50 Brazilian profiles" writes bios and addresses in one recognisable style.

The text of a sign-up (bio and address only; the name is left out) is turned into a
vector by hashing word and character n-grams. No model is downloaded and the same
code runs online and offline. The engine never stores the text, only the vector.

FAISS was in the plan. At this lab's scale (a few thousand sign-ups in a 6-hour
window) exact search with numpy takes about a millisecond and keeps a single
implementation for serving and replay; IndexFlatIP would be the drop-in at volume.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

import numpy as np

DIM = 1024
WINDOW_S = 6 * 3600
SIMILAR = 0.3  # cosine above which two bios count as "written the same way" (measured; see tests)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _bucket(token: str) -> tuple[int, float]:
    h = hashlib.blake2b(token.encode(), digest_size=8).digest()
    n = int.from_bytes(h, "little")
    return n % DIM, (1.0 if (n >> 63) & 1 else -1.0)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z9]+", re.sub(r"\d+", "9", _fold(text)))


def _bio_tokens(bio: str) -> list[str]:
    words = _words(bio)
    toks = [f"w:{w}" for w in words]
    toks += [f"b:{a}_{b}" for a, b in zip(words, words[1:], strict=False)]
    padded = f" {' '.join(words)} "
    toks += [f"c:{padded[i : i + 4]}" for i in range(len(padded) - 3)]
    return toks


def address_shape(endereco: str) -> str:
    """'Rua das Palmeiras, 245, Centro' -> 'rua a, 9, a': how it was written, not where.
    Many honest people share a street; a batch of generated profiles shares a format."""
    text = _fold(endereco).strip()
    if not text:
        return ""
    first, *rest = re.split(r"\s+", text, maxsplit=1)
    tail = re.sub(r"\d+", "9", rest[0] if rest else "")
    tail = re.sub(r"[a-z]+(?:\s+[a-z]+)*", "a", tail)
    return f"{first} {tail}".strip()


def text_of(bio: str, endereco: str) -> str:
    """The comparable text of a sign-up: the bio as written, the address by its format."""
    return f"{bio.strip()}||{address_shape(endereco)}"


def embed(text: str) -> np.ndarray | None:
    """Unit vector for a sign-up's free text, or None when there is none."""
    bio, _, shape = text.partition("||")
    toks = _bio_tokens(bio)
    if not toks:
        return None  # an address alone says where, not how someone writes: no comparison
    if shape.strip():
        toks += [f"e:{shape.strip()}"] * 2  # the address format reinforces, never decides
    v = np.zeros(DIM, dtype=np.float32)
    for tok in toks:
        i, sign = _bucket(tok)
        v[i] += sign
    norm = float(np.linalg.norm(v))
    return v / norm if norm else None


def email_shape(email: str) -> str:
    """'ana.souza1987@gmail.com' -> 'a.a9@gmail.com': how the address was built."""
    if "@" not in email:
        return ""
    local, domain = email.lower().rsplit("@", 1)
    shape = re.sub(r"[a-z]+", "a", re.sub(r"\d+", "9", _fold(local)))
    return f"{shape}@{domain}"


@dataclass(frozen=True, slots=True)
class ScaleInfo:
    cadastros_6h: int = 0  # all sign-ups in the window, whatever they look like
    semelhantes_6h: int = 0  # of those, how many have text similar to this one
    sim_max_6h: float | None = None  # closest previous sign-up (None: no text)
    mesmo_formato_email_6h: int = 0
    mesmo_formato_endereco_6h: int = 0


class ScaleWindow:
    """Sign-ups of the last 6 hours, by event time. Read before write, like the
    velocity windows, so a sign-up is never compared with itself."""

    def __init__(self) -> None:
        self._t: list[float] = []
        self._v: list[np.ndarray | None] = []
        self._shape: list[tuple[str, str]] = []

    def _alive(self, now: float) -> np.ndarray:
        t = np.asarray(self._t, dtype=np.float64)
        return (t > now - WINDOW_S) & (t <= now)

    def query(self, vec: np.ndarray | None, shape: tuple[str, str], now: float) -> ScaleInfo:
        """``shape``: (e-mail format, address format)."""
        if not self._t:
            return ScaleInfo(sim_max_6h=0.0 if vec is not None else None)
        alive = self._alive(now)
        idx = np.flatnonzero(alive)
        same_email = sum(1 for i in idx if shape[0] and self._shape[i][0] == shape[0])
        same_addr = sum(1 for i in idx if shape[1] and self._shape[i][1] == shape[1])
        with_text = [i for i in idx if self._v[i] is not None]
        if vec is None or not with_text:
            sim = None if vec is None else 0.0
            return ScaleInfo(int(alive.sum()), 0, sim, same_email, same_addr)
        sims = np.stack([self._v[i] for i in with_text]) @ vec
        return ScaleInfo(
            int(alive.sum()),
            int((sims >= SIMILAR).sum()),
            round(float(sims.max()), 6),
            same_email,
            same_addr,
        )

    def add(self, vec: np.ndarray | None, shape: tuple[str, str], now: float) -> None:
        self._t.append(now)
        self._v.append(vec)
        self._shape.append(shape)
        if len(self._t) > 4096:  # bounded memory; drop what is long out of every window
            newest = max(self._t)
            keep = [i for i, t in enumerate(self._t) if t > newest - 2 * WINDOW_S]
            self._t = [self._t[i] for i in keep]
            self._v = [self._v[i] for i in keep]
            self._shape = [self._shape[i] for i in keep]
