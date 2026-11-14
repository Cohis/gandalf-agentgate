"""Data retention and the right to deletion (docs/lgpd.md).

    uv run python -m jobs.retention                  # daily: drop what is past retention
    uv run python -m jobs.retention --dry-run        # only count
    uv run python -m jobs.retention --participante SIGMA   # a volunteer asked to be erased

What is kept and why:

- Unlabelled events (real traffic, the personal data the engine sees) are deleted after
  ``--dias`` days (30 by default), together with their decision, feature vector and
  sign-up vector (ON DELETE CASCADE). Challenges older than that go too.
- Labelled simulator traffic is not personal data (the lab generated it, IPs are
  fictitious) and is the training set, so it stays.
- Recordings of volunteers stay until the end of the project, as the consent page says,
  unless the person asks: ``--participante`` deletes every event recorded under their
  code, and the labels.
"""

from __future__ import annotations

import argparse

import psycopg

from jobs.train import DSN

EXPIRED = """
    FROM events e
    WHERE e.recebido_em < now() - make_interval(days => %(dias)s)
      AND NOT EXISTS (SELECT 1 FROM labels l WHERE l.avaliacao_id = e.avaliacao_id)
"""
OLD_CHALLENGES = "FROM challenges WHERE criado_em < now() - make_interval(days => %(dias)s)"
PARTICIPANT = """
    FROM labels l
    WHERE l.fonte LIKE 'gravacao%%' AND l.perfil = %(perfil)s
"""


def expire(conn: psycopg.Connection, dias: int, dry_run: bool = False) -> dict[str, int]:
    p = {"dias": dias}
    if dry_run:
        return {
            "eventos": conn.execute(f"SELECT count(*) {EXPIRED}", p).fetchone()[0],
            "desafios": conn.execute(f"SELECT count(*) {OLD_CHALLENGES}", p).fetchone()[0],
        }
    ev = conn.execute(
        f"DELETE FROM events WHERE avaliacao_id IN (SELECT e.avaliacao_id {EXPIRED})", p
    ).rowcount
    ch = conn.execute(f"DELETE {OLD_CHALLENGES}", p).rowcount
    return {"eventos": ev, "desafios": ch}


def erase_participant(conn: psycopg.Connection, code: str, dry_run: bool = False) -> dict[str, int]:
    p = {"perfil": "real_" + code.strip().upper()}
    if dry_run:
        return {"eventos": conn.execute(f"SELECT count(*) {PARTICIPANT}", p).fetchone()[0]}
    ids = [r[0] for r in conn.execute(f"SELECT l.avaliacao_id {PARTICIPANT}", p).fetchall()]
    ev = conn.execute("DELETE FROM events WHERE avaliacao_id = ANY(%s)", (ids,)).rowcount
    conn.execute("DELETE FROM challenges WHERE avaliacao_id = ANY(%s)", (ids,))
    conn.execute("DELETE FROM labels WHERE avaliacao_id = ANY(%s)", (ids,))
    return {"eventos": ev, "rotulos": len(ids)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dias", type=int, default=30, help="retention of unlabelled events")
    ap.add_argument("--participante", help="a volunteer's code: erase their recordings")
    ap.add_argument("--dry-run", action="store_true", help="only count what would go")
    a = ap.parse_args()
    with psycopg.connect(DSN) as conn:
        if a.participante:
            out = erase_participant(conn, a.participante, a.dry_run)
        else:
            out = expire(conn, a.dias, a.dry_run)
    verb = "seriam apagados" if a.dry_run else "apagados"
    print(", ".join(f"{n} {k}" for k, n in out.items()), verb)


if __name__ == "__main__":
    main()
