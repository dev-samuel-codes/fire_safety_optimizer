# -*- coding: utf-8 -*-
"""
report — 규칙엔진 위반 라벨 → 사람가독 '소방 법규 검토의견서'(Markdown/CSV).

규칙엔진(engine.checks)은 설명가능하다: 각 위반이 rule_id·근거 조항(rule.clause)·측정값·
기준값을 들고 있으므로, 블랙박스가 아니라 '왜 부적합인지'를 조항과 함께 제시한다.
"""
from __future__ import annotations

import csv

from .schema.rules import by_id

_SEV_KR = {"critical": "중대", "major": "주요", "minor": "경미", "info": "정보"}


def verification_report(ann, violations) -> str:
    """DrawingAnnotation + ViolationLabel[] → 검토의견서(Markdown)."""
    m = ann.building_meta or {}
    viol = [v for v in violations if v.status == "violation"]
    n_obj = len(ann.objects)
    sev_n = {s: sum(1 for v in viol if v.severity == s) for s in ("critical", "major", "minor", "info")}
    verdict = "적합 (지적사항 없음)" if not viol else f"부적합 — 시정 필요 ({len(viol)}건)"

    L = []
    L.append(f"# 소방 법규 검토의견서 (자동) — {ann.drawing_id}")
    L.append("")
    L.append(f"- 대상: 구조 **{m.get('structure','-')}** · 용도 **{m.get('occupancy','-')}** "
             f"· 층수 {m.get('floors','-')} · 객체 {n_obj}개")
    L.append(f"- 검토기준: 한국 화재안전기술기준(NFTC)·건축법 — FireVal 규칙엔진(plan-checkable)")
    L.append(f"- **종합 판정: {verdict}**  "
             f"(검토 {len(violations)}항목 · 위반 중대 {sev_n['critical']} / 주요 {sev_n['major']} / 정보 {sev_n['info']})")
    L.append("")
    if viol:
        L.append("## 지적사항")
        for i, v in enumerate(sorted(viol, key=lambda x: ("critical", "major", "minor", "info").index(x.severity)), 1):
            r = by_id(v.rule_id) if v.rule_id in _catalog() else None
            clause = r.clause if r else ""
            L.append(f"{i}. **[{_SEV_KR.get(v.severity, v.severity)}]** `{v.rule_id}`")
            L.append(f"   - 근거: {clause}")
            L.append(f"   - 내용: {v.description}")
            if v.measured_value is not None:
                L.append(f"   - 측정 {v.measured_value}{v.unit} vs 기준 {v.required_value}{v.unit}")
            L.append("")
    else:
        L.append("## 지적사항\n해당 검토 규칙 범위에서 위반이 발견되지 않았습니다.\n")
    L.append("> ⚠ 본 검토는 plan-checkable 규칙(2D 평면 기하)에 대한 1차 자동검토입니다. "
             "calc/section/context 규칙과 최종 적합 판정은 면허 소방기술사의 검토가 필요합니다.")
    return "\n".join(L)


def _catalog():
    from .schema.rules import RULE_CATALOG
    return RULE_CATALOG


def write_report_md(ann, violations, path) -> str:
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(verification_report(ann, violations))
    return path


def write_violations_csv(ann, violations, path) -> str:
    with open(path, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["drawing_id", "rule_id", "status", "severity", "clause",
                    "measured", "required", "unit", "description"])
        for v in violations:
            r = by_id(v.rule_id) if v.rule_id in _catalog() else None
            w.writerow([ann.drawing_id, v.rule_id, v.status, v.severity,
                        r.clause if r else "", v.measured_value, v.required_value,
                        v.unit, v.description])
    return path
