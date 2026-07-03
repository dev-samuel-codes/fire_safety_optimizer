# -*- coding: utf-8 -*-
"""
report — 최종 산출물(비용 CSV + 마크다운 요약) 작성.
"""
from __future__ import annotations

import csv


_FAC_KR = {"sprinkler": "스프링클러", "detector": "감지기", "extinguisher": "소화기",
           "hydrant": "옥내소화전", "evacuation": "피난출구"}

_REWORK_KR = {"pipe_redo": "가지배관·헤드 철거/재시공 자재", "fire_labor": "소방 인건비",
              "mep_relocate": "간섭 전기·기계 이설/재결선", "finish_restore": "천장·마감 복구",
              "delay_indirect": "검측 재실시·공정지연 간접비"}


def write_cost_csv(report, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["항목", "before", "after", "변화"])
        b, a, d = report.before, report.after, report.delta
        w.writerow(["하드 클래시(건)", b["hard_clashes"], a["hard_clashes"],
                    -d["hard_clashes_resolved"]])
        w.writerow(["배관길이(m)", b["pipe_len_m"], a["pipe_len_m"], d["pipe_len_change_m"]])
        w.writerow(["자재비(원)", b["material_cost"], a["material_cost"], d["material_cost_change"]])
        w.writerow(["재시공리스크비(원)", b["rework_risk_cost"], a["rework_risk_cost"],
                    -d["rework_risk_saved"]])
        w.writerow(["총비용(원)", b["total_cost"], a["total_cost"], d["total_cost_change"]])
        w.writerow([])
        w.writerow(["시설", "개수"])
        for fac, n in b["counts"].items():
            w.writerow([_FAC_KR.get(fac, fac), n])
    return path


def write_summary(report, checks, path):
    b, a, d = report.before, report.after, report.delta
    lines = []
    lines.append("# FireOpt 설계 요약\n")
    lines.append("## 충돌 해소 (before → after)\n")
    lines.append(f"- 하드 클래시: **{b['hard_clashes']}건 → {a['hard_clashes']}건** "
                 f"(**{d['hard_clashes_resolved']}건 해소**)")
    lines.append(f"- 재시공 리스크비: {b['rework_risk_cost']:,}원 → {a['rework_risk_cost']:,}원 "
                 f"(**{d['rework_risk_saved']:,}원 절감**)")
    if d.get("rework_saved_formula"):
        lines.append(f"  - 산식: **{d['rework_saved_formula']}**")
    lines.append(f"- 배관길이(프록시): {b['pipe_len_m']}m → {a['pipe_len_m']}m "
                 f"({d['pipe_len_change_m']:+}m)")
    lines.append(f"- 총비용(자재+리스크): {b['total_cost']:,}원 → {a['total_cost']:,}원 "
                 f"({d['total_cost_change']:+,}원)\n")
    lines.append("## 시설 배치 수량\n")
    lines.append("| 시설 | 개수 |")
    lines.append("|---|---|")
    for fac, n in a["counts"].items():
        lines.append(f"| {_FAC_KR.get(fac, fac)} | {n} |")
    lines.append("")
    if checks:
        lines.append("## 코드 준수 산정\n")
        hyd = checks.get("hydrant", {})
        if hyd:
            lines.append(f"- 옥내소화전: {hyd.get('count')}개, 수원 {hyd.get('water_source_m3')}㎥, "
                         f"펌프 {hyd.get('pump_flow_Lpm')}L/min (동시개구 N={hyd.get('sim_open_cap')})")
        ext = checks.get("extinguisher", {})
        if ext:
            lines.append(f"- 소화기: 배치 {ext.get('placed_count')}개 / 능력단위 {ext.get('capacity_units_required')}단위, "
                         f"보행거리 최대 {ext.get('max_walk_m')}m (≤20 {'OK' if ext.get('walk_ok') else 'NG'})")
        ev = checks.get("evacuation", {})
        if ev:
            lines.append(f"- 피난: 출구 {ev.get('exit_count')}개, 직통계단 보행한도 {ev.get('stair_walk_limit_m')}m, "
                         f"피난기구 {ev.get('escape_devices_required')}개 필요")
    # 재시공비 산정 근거 (왜 이 값인지)
    basis = getattr(report, "basis", None) or {}
    if basis.get("items"):
        lines.append("## 재시공 리스크비 산정 근거\n")
        lines.append(f"FireOpt 가 **확정 계산**하는 값은 충돌 **건수**({b['hard_clashes']}→{a['hard_clashes']})이며, "
                     f"건당 재시공비는 아래 항목 합({basis['per_clash']:,}원/건)으로 추정한다.\n")
        lines.append("| 항목 | 금액(원/건) | 근거 |")
        lines.append("|---|---:|---|")
        for k, v in basis["items"].items():
            lines.append(f"| {_REWORK_KR.get(k, k)} | {v['value']:,} | {v['condition']} |")
        lines.append(f"| **합계(건당)** | **{basis['per_clash']:,}** | 충돌 1지점 재시공 직접비 |")
        lines.append("")
        lines.append(f"- **절감액 산식**: {d.get('rework_saved_formula','')}")
        lines.append(f"- **규모 근거(앵커)**: {basis.get('anchor','')}")
        lines.append("- 모든 건당 단가는 **조정 가능한 가정값**이며, 충돌 '건수'(6→0)만이 도면 기하로 확정된 값이다.\n")
    lines.append("> ⚠️ 비용 단가는 데모 추정치이며 상대 비교용입니다. 모든 연산 로컬 처리.\n")
    text = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(text)
    return path
