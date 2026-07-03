# -*- coding: utf-8 -*-
"""
cost — before/after 비용 비교. 자재비(장치+배관) + 재시공 리스크비(미해소 하드클래시).

⚠️ 단가는 constants.COST 의 DEMO 추정치 — 절대금액이 아닌 before/after 상대 비교용.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from . import constants as C


_DEVICE_COST_KEY = {
    "sprinkler": "sprinkler_head",
    "detector": "detector",
    "extinguisher": "extinguisher",
    "hydrant": "hydrant_box",
    "evacuation": "evac_device",
}


def facility_counts(layout) -> dict:
    return {fac: len(lst) for fac, lst in layout.placements.items()}


def material_cost(layout, pipe_len_m: float) -> tuple:
    """자재비 합계와 항목별 내역."""
    breakdown = {}
    total = 0.0
    for fac, lst in layout.placements.items():
        key = _DEVICE_COST_KEY.get(fac)
        if not key:
            continue
        unit = C.COST[key]["value"]
        amt = len(lst) * unit
        breakdown[fac] = amt
        total += amt
    pipe_amt = pipe_len_m * C.COST["branch_pipe"]["value"]
    breakdown["branch_pipe"] = pipe_amt
    total += pipe_amt
    return total, breakdown


def rework_risk(clashes) -> float:
    hard = sum(1 for c in clashes if c.severity == "hard")
    return hard * C.rework_per_clash()


def rework_basis() -> dict:
    """충돌 1건당 재시공비 산정 근거(항목 분해 + 합 + 앵커)."""
    items = {k: {"value": v["value"], "condition": v["condition"]}
             for k, v in C.REWORK_BASIS.items()}
    return {"per_clash": C.rework_per_clash(), "items": items, "anchor": C.REWORK_ANCHOR}


@dataclass
class CostReport:
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    delta: dict = field(default_factory=dict)
    basis: dict = field(default_factory=dict)   # 재시공비 산정 근거

    def to_dict(self):
        return asdict(self)


def compare(before_layout, after_layout, before_pipe, after_pipe,
            before_clashes, after_clashes) -> CostReport:
    bm, bb = material_cost(before_layout, before_pipe)
    am, ab = material_cost(after_layout, after_pipe)
    br = rework_risk(before_clashes)
    ar = rework_risk(after_clashes)

    def hard(cl):
        return sum(1 for c in cl if c.severity == "hard")

    before = {
        "counts": facility_counts(before_layout),
        "pipe_len_m": round(before_pipe, 1),
        "material_cost": round(bm),
        "material_breakdown": {k: round(v) for k, v in bb.items()},
        "hard_clashes": hard(before_clashes),
        "rework_risk_cost": round(br),
        "total_cost": round(bm + br),
    }
    after = {
        "counts": facility_counts(after_layout),
        "pipe_len_m": round(after_pipe, 1),
        "material_cost": round(am),
        "material_breakdown": {k: round(v) for k, v in ab.items()},
        "hard_clashes": hard(after_clashes),
        "rework_risk_cost": round(ar),
        "total_cost": round(am + ar),
    }
    resolved = before["hard_clashes"] - after["hard_clashes"]
    per = C.rework_per_clash()
    delta = {
        "hard_clashes_resolved": resolved,
        "rework_risk_saved": round(br - ar),
        "rework_per_clash": per,
        # 절감액 산식: 해소 건수 × 건당 재시공비
        "rework_saved_formula": f"{resolved}건 해소 × {per:,}원/건 = {resolved*per:,}원",
        "material_cost_change": round(am - bm),
        "total_cost_change": round((am + ar) - (bm + br)),
        "pipe_len_change_m": round(after_pipe - before_pipe, 1),
    }
    return CostReport(before=before, after=after, delta=delta, basis=rework_basis())
