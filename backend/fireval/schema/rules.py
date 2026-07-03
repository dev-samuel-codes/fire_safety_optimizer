# -*- coding: utf-8 -*-
"""
rules — 규칙 ID 체계 (실행가이드 Phase 0 ③).

핵심 설계: **규칙 값을 새로 만들지 않는다.** fireopt.constants(적대적 검증된 단일
출처, 122개 항목)를 순회해 각 조항을 고유 rule_id 로 쪼갠 **뷰**를 만든다. 따라서
NFTC 값이 바뀌면 constants.py 한 곳만 고치면 카탈로그가 자동 동기화된다.

rule_id 규약:  FV-<FAC>-<constant_key>
  FAC ∈ {SPK, DET, EXT, HYD, EVA} (설비 약호)
  예) FV-SPK-R_fireproof, FV-DET-smoke_12_lt4, FV-EVA-two_stairs_threshold_medical
  → 안정적·유일·정렬가능하며 constants 키와 1:1.

각 Rule 이 들고 있는 부가 메타(원 상수에 없던 분류):
  · clause        : condition 문자열에서 파싱한 법적 인용(예 "NFTC 103 2.7.3")
  · standard      : 소관 기준 패밀리(NFTC 103 / 건축법 시행령 / 피난·방화규칙 / NFTC 301 …)
  · check_type    : 검사 유형(radial_coverage/area_coverage/spacing/… → 규칙 엔진 분기)
  · checkability  : 무엇이 있어야 판정 가능한가
        plan    = 2D 평면 기하+라벨만으로 판정       (예 감지면적, 보행거리, 복도너비)
        section = 높이/수직정보(입면·단면) 필요        (예 부착높이, 수직이격, 경사각)
        context = 건물 메타 필요(구조·용도·층수·연면적) (예 설치제외, 적응층)
        calc    = 산정값 재계산으로 검증(평면 위반 아님) (예 수원·펌프·능력단위·관경)
  · target_category : 이 규칙이 검사하는 객체(categories.key) — 카테고리↔규칙 연결
  · severity      : critical/major/minor/info (전문가가 조정할 기본값)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType

from fireopt.constants import ALL_FACILITIES


# 설비키 → (약호, 기본 소관기준, 한글명)
FACILITY_META = MappingProxyType({
    "sprinkler":    ("SPK", "NFTC 103", "스프링클러설비"),
    "detector":     ("DET", "NFTC 203", "자동화재탐지설비 감지기"),
    "extinguisher": ("EXT", "NFTC 101", "소화기구"),
    "hydrant":      ("HYD", "NFTC 102", "옥내소화전설비"),
    "evacuation":   ("EVA", "건축법 시행령 제34조", "피난(계단·복도·피난기구)"),
})

CHECK_TYPES = (
    "radial_coverage", "walking_coverage", "vertical_coverage", "area_coverage",
    "zone_area_limit", "corridor_width", "opening_dimension", "spacing",
    "clearance", "mount_geometry", "angle", "applicability_threshold",
    "applicability_floor", "count_sizing", "capacity_sizing", "hydraulic_sizing",
    "pipe_spec", "factor", "exclusion", "other",
)
CHECKABILITY = ("plan", "section", "context", "calc")
SEVERITIES = ("critical", "major", "minor", "info")

# condition 문자열에서 법적 인용을 뽑는 패턴(우선순위 순)
_CITE_PATTERNS = (
    re.compile(r"NF(?:TC|PC)\s*\d+(?:\s+[\d.]+(?:\s*/\s*\d+)?)?"),  # NFTC 103 2.7.3 / 608
    re.compile(r"제\d+조(?:의\d+)?[①-⑨]?(?:\d+호)?"),               # 제34조②2호, 제15조의2②
    re.compile(r"표\s*[\d.]+(?:\s*비고)?"),                          # 표 2.1.1.2 비고
)


def _parse_clause(condition: str, default_std: str) -> tuple:
    """condition → (clause 문자열, standard 패밀리). 못 찾으면 설비 기본값."""
    cites: list = []
    for pat in _CITE_PATTERNS:
        cites += pat.findall(condition)
    # 표준 패밀리: 첫 인용 기준
    standard = default_std
    if cites:
        first = cites[0]
        if first.startswith(("NFTC", "NFPC")):
            standard = " ".join(first.split()[:2])               # "NFTC 103"
        elif first.startswith("표"):
            standard = default_std                                # 표만 있으면 설비 기본
        elif "제15조의2" in first:
            standard = "피난·방화규칙 제15조의2"
        elif first.startswith("제"):
            standard = "건축법 시행령 " + re.match(r"제\d+조(?:의\d+)?", first).group(0)
    clause = " · ".join(dict.fromkeys(cites)) if cites else default_std
    return clause, standard


def _classify(metric: str) -> str:
    """metric_type → check_type(규칙 엔진 분기 키)."""
    m = metric
    if "radial" in m:                                   return "radial_coverage"
    if "walking" in m:                                  return "walking_coverage"
    if "vertical" in m:                                 return "vertical_coverage"
    if "경계구역면적" in m:                              return "zone_area_limit"
    if "감지면적" in m:                                  return "area_coverage"
    if "유효너비" in m:                                  return "corridor_width"
    if "개구부 치수" in m:                               return "opening_dimension"
    if "부착높이" in m or "설치높이" in m or "부착면이격" in m:  return "mount_geometry"
    if "각도" in m:                                      return "angle"
    if "이격" in m or "공간" in m or "상호간격" in m:    return "clearance"
    if "직선거리" in m:                                  return "spacing"
    if "임계" in m:                                      return "applicability_threshold"
    if "층 범위" in m:                                   return "applicability_floor"
    if "산정분모" in m:                                  return "count_sizing"
    if "감소율" in m or "보정계수" in m or "비율상한" in m \
            or "배치산식계수" in m or "산정계수" in m:    return "factor"
    if "능력단위" in m or "방호면적" in m:               return "capacity_sizing"
    if "수원" in m or "펌프" in m or "방수성능" in m or "비상전원" in m:  return "hydraulic_sizing"
    if "규격" in m or "구경" in m:                       return "pipe_spec"
    if "설치제외" in m or "설치제한" in m or "면제" in m: return "exclusion"
    return "other"


def _checkability(check_type: str, metric: str) -> str:
    if check_type in ("vertical_coverage", "angle", "mount_geometry"):
        return "section"
    if "수직" in metric:
        return "section"
    if check_type in ("count_sizing", "capacity_sizing", "hydraulic_sizing",
                      "pipe_spec", "factor"):
        return "calc"
    if check_type in ("applicability_floor", "exclusion"):
        return "context"
    return "plan"


def _severity(check_type: str) -> str:
    if check_type in ("radial_coverage", "walking_coverage", "vertical_coverage",
                      "area_coverage"):
        return "critical"        # 소방기기 방호 미달 = 화재 시 직접 위험
    if check_type in ("factor", "pipe_spec"):
        return "info"
    return "major"


def _target_category(facility: str, key: str) -> str:
    """규칙이 검사하는 객체 카테고리(categories.key)."""
    if facility == "sprinkler":
        return "sprinkler_head"
    if facility == "detector":
        if "smoke" in key:                                          return "smoke_detector"
        if key.startswith(("diff_spot", "fixed_spot")) \
                or "thermocouple" in key or "airtube" in key \
                or key == "heat_to_ceiling_max" or "linear" in key:
            return "detector_linear" if "linear" in key else "heat_detector"
        if "detection_zone" in key:                                 return "detection_zone"
        return "smoke_detector"
    if facility == "extinguisher":
        return "extinguisher"
    if facility == "hydrant":
        return "hydrant_pipe" if ("pipe" in key or "diameter" in key) else "hydrant_box"
    if facility == "evacuation":
        if "corridor" in key:                                       return "corridor"
        if "escape" in key or "descent" in key:                     return "escape_device"
        if "stair" in key:                                          return "stair"
        return "evac_route"
    return ""


@dataclass(frozen=True)
class Rule:
    """규칙 1개(constants 항목 1개의 검증용 뷰, 불변)."""
    rule_id: str
    facility: str
    key: str
    value: object
    unit: str
    metric_type: str
    description: str            # = constants condition(교정이력·근거 포함)
    clause: str
    standard: str
    check_type: str
    checkability: str
    target_category: str
    severity: str

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id, "facility": self.facility, "key": self.key,
            "value": self.value, "unit": self.unit, "metric_type": self.metric_type,
            "clause": self.clause, "standard": self.standard,
            "check_type": self.check_type, "checkability": self.checkability,
            "target_category": self.target_category, "severity": self.severity,
            "description": self.description,
        }


def _build() -> tuple:
    rules: list = []
    for facility, table in ALL_FACILITIES.items():
        abbr, default_std, _ = FACILITY_META[facility]
        for key, entry in table.items():
            metric = entry.get("metric_type", "")
            cond = entry.get("condition", "")
            clause, standard = _parse_clause(cond, default_std)
            ctype = _classify(metric)
            rules.append(Rule(
                rule_id=f"FV-{abbr}-{key}",
                facility=facility, key=key,
                value=entry.get("value"), unit=entry.get("unit", ""),
                metric_type=metric, description=cond,
                clause=clause, standard=standard,
                check_type=ctype,
                checkability=_checkability(ctype, metric),
                target_category=_target_category(facility, key),
                severity=_severity(ctype),
            ))
    return tuple(rules)


RULES: tuple = _build()
RULE_CATALOG = MappingProxyType({r.rule_id: r for r in RULES})


# ── 조회 헬퍼 ──────────────────────────────────────────────────────────────
def by_id(rule_id: str) -> Rule:
    return RULE_CATALOG[rule_id]


def by_facility(facility: str) -> list:
    return [r for r in RULES if r.facility == facility]


def by_category(category_key: str) -> list:
    """이 객체 카테고리를 검사하는 규칙들."""
    return [r for r in RULES if r.target_category == category_key]


def by_check_type(check_type: str) -> list:
    return [r for r in RULES if r.check_type == check_type]


def plan_checkable() -> list:
    """2D 평면+라벨만으로 판정 가능한 규칙(규칙 엔진 Phase 7 의 1차 대상)."""
    return [r for r in RULES if r.checkability == "plan"]


def stats() -> dict:
    """카탈로그 요약(설비별·checkability별·check_type별 개수)."""
    def _count(attr):
        out: dict = {}
        for r in RULES:
            out[getattr(r, attr)] = out.get(getattr(r, attr), 0) + 1
        return out
    return {
        "total": len(RULES),
        "by_facility": _count("facility"),
        "by_checkability": _count("checkability"),
        "by_check_type": _count("check_type"),
        "by_severity": _count("severity"),
    }


def validate() -> list:
    """자기검증: rule_id 유일성, 도메인 준수, constants 전수 반영, target_category 실존."""
    from . import categories as C
    problems: list = []
    # 1) rule_id 유일 + 도메인
    seen = set()
    for r in RULES:
        if r.rule_id in seen:
            problems.append(f"중복 rule_id: {r.rule_id}")
        seen.add(r.rule_id)
        if r.check_type not in CHECK_TYPES:
            problems.append(f"{r.rule_id}: 잘못된 check_type '{r.check_type}'")
        if r.checkability not in CHECKABILITY:
            problems.append(f"{r.rule_id}: 잘못된 checkability '{r.checkability}'")
        if r.severity not in SEVERITIES:
            problems.append(f"{r.rule_id}: 잘못된 severity '{r.severity}'")
        # 2) target_category 가 카테고리표에 실존
        if r.target_category and r.target_category not in C.CATEGORIES:
            problems.append(f"{r.rule_id}: target_category '{r.target_category}' 카테고리표에 없음")
    # 3) constants 전수 반영(누락 0)
    n_const = sum(len(t) for t in ALL_FACILITIES.values())
    if len(RULES) != n_const:
        problems.append(f"규칙 수 {len(RULES)} ≠ constants 항목 {n_const} (누락/중복)")
    return problems
