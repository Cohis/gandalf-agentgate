from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml
from gandalf_contracts import Decisao
from gandalf_features import FEATURE_NAMES, FeatureVector

_OPS: dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge,
    ">": operator.gt,
    "<=": operator.le,
    "<": operator.lt,
    "==": operator.eq,
}

# Traps are signals for the model, never grounds for an automatic block.
FORBIDDEN_IN_RULES = frozenset({"honeypot", "canario"})


@dataclass(frozen=True, slots=True)
class Condition:
    feature: str
    op: str
    valor: float

    def holds(self, f: FeatureVector) -> bool:
        v = f.get(self.feature)
        return v is not None and _OPS[self.op](v, self.valor)


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    decisao: Decisao
    motivo: str
    todas: tuple[Condition, ...]


def load_rules(path: Path) -> list[Rule]:
    rules: list[Rule] = []
    for raw in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
        conds = tuple(Condition(c["feature"], c["op"], float(c["valor"])) for c in raw["todas"])
        for c in conds:
            if c.feature in FORBIDDEN_IN_RULES:
                raise ValueError(f"rule {raw['id']}: trap signal '{c.feature}' cannot be a rule")
            if c.feature not in FEATURE_NAMES:
                raise ValueError(f"rule {raw['id']}: unknown feature '{c.feature}'")
            if c.op not in _OPS:
                raise ValueError(f"rule {raw['id']}: unknown operator '{c.op}'")
        decisao = Decisao(raw["decisao"])
        if decisao is Decisao.aprovar:
            raise ValueError(f"rule {raw['id']}: rules can only challenge or deny")
        rules.append(Rule(raw["id"], decisao, raw["motivo"], conds))
    return rules


def evaluate(rules: list[Rule], f: FeatureVector) -> list[Rule]:
    return [r for r in rules if all(c.holds(f) for c in r.todas)]
