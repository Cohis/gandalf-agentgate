from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).parent


@dataclass(frozen=True, slots=True)
class Settings:
    redis_url: str = "redis://localhost:6379/0"
    database_url: str | None = None  # None disables persistence (unit tests)
    service_key: str = "dev-only-change-me"  # shared secret between shop and engine
    ip_hmac_key: str = "dev-only-change-me"  # rotate to unlink stored IP hashes
    rules_path: Path = _HERE / "rules.yaml"
    policy_path: Path = _HERE / "policy.yaml"
    model_dir: Path | None = None  # holds <version>/ folders and an ACTIVE pointer

    @classmethod
    def from_env(cls) -> Settings:
        d = cls()
        return cls(
            redis_url=os.environ.get("GANDALF_REDIS_URL", d.redis_url),
            database_url=os.environ.get("GANDALF_DATABASE_URL") or None,
            service_key=os.environ.get("GANDALF_SERVICE_KEY", d.service_key),
            ip_hmac_key=os.environ.get("GANDALF_IP_HMAC_KEY", d.ip_hmac_key),
            rules_path=Path(os.environ.get("GANDALF_RULES_PATH", d.rules_path)),
            policy_path=Path(os.environ.get("GANDALF_POLICY_PATH", d.policy_path)),
            model_dir=Path(m) if (m := os.environ.get("GANDALF_MODEL_DIR")) else None,
        )
