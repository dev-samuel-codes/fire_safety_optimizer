# -*- coding: utf-8 -*-
"""
detector_type — 방 이름/용도 → NFTC 203 상 **요구 감지기 종류** 분류 + 요구 개수(N).

정직성 원칙(측정·근거 기반, 감(感) 상수 금지):
  · 감지기 "종류"를 법이 정하는 경우는 **NFTC 203 2.4.2 연기감지기 의무 장소**뿐이다.
    (계단·복도(30m↑)·승강로·취침/숙박/입원 거실 등 — 아래 출처.)
  · 그 외 일반 실(사무·창고·조리·기계 등)은 **종류가 설계자 선택**이라, 이름만으로 종류를
    확정할 수 없다. "조리실=열" 같은 매핑은 NFTC 조문이 아니라 설계 관행 → 코드로 단정 금지.
  · 종류 미확정 방을 연기(150㎡)로 디폴트하면, 실제 열감지기 방을 과소계산해 위반을 은폐하는
    **false-pass**가 난다(checks.py 주석 참조). 따라서 미확정 방은 연기 디폴트 대신
    **조건부 N(연기면 N1·열이면 N2) + needs_review**로 정직하게 표시한다.

면적당 기준 수치는 여기서 재인코딩하지 않고 fireopt.constants.detector_area 를 그대로 쓴다
(단일 출처 = 드리프트 방지). checks.check_detector_area 와 같은 상수를 공유한다.

출처(2026-07 확인):
  NFTC 203(자동화재탐지설비 및 시각경보장치의 화재안전기술기준) 2.4.2 — 국가법령정보센터.
  2.4.2.1 계단·경사로 및 에스컬레이터 경사로 / 2.4.2.2 복도(30m 미만 제외)
  2.4.2.3 승강로·린넨슈트·파이프피트·덕트 등 / 2.4.2.4 천장 15~20m 미만
  2.4.2.5 취침·숙박·입원 등 용도 거실(공동주택·숙박·노유자·의료·수련(합숙)·고시원 등)
미구현(별도 조문 — 여기서 단정하지 않음): 2.4.1 단서(지하·무창·환기불량·40㎡미만 특정종류),
  2.4.5 감지기 설치제외 장소(화장실·목욕실 등).
"""
from __future__ import annotations

import math

from fireopt import constants as C

_SMOKE = "smoke_12"

# NFTC 203 2.4.2 — 이름에 이 키워드가 있으면 '연기 의무'(조건 없음).
_SMOKE_UNCONDITIONAL = {
    "계단": "2.4.2.1", "경사로": "2.4.2.1", "에스컬레이터": "2.4.2.1",
    "승강로": "2.4.2.3", "린넨슈트": "2.4.2.3", "린넨": "2.4.2.3",
    "파이프피트": "2.4.2.3", "파이프덕트": "2.4.2.3", "피트": "2.4.2.3",
}
# 2.4.2.5 — 취침류 실명(용도 조건 충족 시 연기 의무).
_SLEEP_NAMES = ("숙소", "침실", "객실", "병실", "입원실", "생활실", "숙직", "취침",
                "기숙사", "생활관")
# 2.4.2.5 — 위 실명이 '연기 의무'가 되는 건물 용도.
_SLEEP_OCCUPANCY = ("공동주택", "숙박", "노유자", "의료", "수련", "합숙", "고시원", "오피스텔")
# 2.4.5 감지기 설치제외 대상 '가능성'(원문 미확보 → 단정 않고 flag만; 요구 N은 보수적으로 유지).
_MAYBE_EXEMPT = ("화장실", "목욕실", "샤워실", "변소")
# 대표 열감지기 종별 편차가 큼(차동식2종 70㎡ vs 정온식2종 20㎡) → 조건부 N은 범위로 표시.
_HEAT_LOOSE = "diff_spot_2"    # 흔한 열(차동식 2종) — 개수 하한
_HEAT_STRICT = "fixed_spot_2"  # 정온식 2종 — 개수 상한(보수적)


def required_detector_type(name: str, occupancy: str = "",
                           corridor_length_m: float | None = None) -> dict:
    """방 이름/용도 → 요구 감지기 종류 분류.

    반환:
      mandate: "smoke"(법상 연기 의무) | "conditional"(조건 충족 시 의무) | "designer_choice"(법 미지정)
      dtype:   "smoke_12" | None      (강제 종류가 있으면; designer_choice면 None)
      basis:   근거 조항(NFTC 203 …) | ""
      condition: 조건부일 때 그 조건 설명 | None
    """
    nm = (name or "").strip()
    occ = occupancy or ""

    for kw, clause in _SMOKE_UNCONDITIONAL.items():
        if kw in nm:
            if kw == "경사로" and any(x in nm for x in ("주차", "차량", "차로")):
                continue   # 차량용 경사로(주차램프)는 2.4.2.1(계단 대체 보행 경사로)이 아님
            return {"mandate": "smoke", "dtype": _SMOKE, "basis": f"NFTC 203 {clause}",
                    "condition": None}

    if "복도" in nm:
        if corridor_length_m is not None and corridor_length_m >= 30:
            return {"mandate": "smoke", "dtype": _SMOKE, "basis": "NFTC 203 2.4.2.2",
                    "condition": None}
        if corridor_length_m is not None:      # < 30 m → 법상 종류 미지정
            return {"mandate": "designer_choice", "dtype": None, "basis": "",
                    "condition": "복도 30m 미만 → 종류 미지정(설계자 선택)"}
        return {"mandate": "conditional", "dtype": _SMOKE, "basis": "NFTC 203 2.4.2.2",
                "condition": "복도 길이 ≥ 30m 이면 연기 의무(길이 미상)"}

    if any(s in nm for s in _SLEEP_NAMES):
        if any(o in occ for o in _SLEEP_OCCUPANCY):
            return {"mandate": "smoke", "dtype": _SMOKE, "basis": "NFTC 203 2.4.2.5",
                    "condition": None}
        return {"mandate": "conditional", "dtype": _SMOKE, "basis": "NFTC 203 2.4.2.5",
                "condition": "용도가 공동주택·숙박·노유자·의료·수련(합숙)·고시원 등이면 연기 의무"}

    maybe_exempt = any(x in nm for x in _MAYBE_EXEMPT)
    cond = ("NFTC 2.4.5 설치제외 대상일 수 있음 — 확인 필요(요구 N은 보수적으로 유지)"
            if maybe_exempt else
            "NFTC 미지정 — 종류는 설계자 선택(확정하려면 실제 감지기 인식 필요)")
    return {"mandate": "designer_choice", "dtype": None, "basis": "",
            "condition": cond, "maybe_exempt": maybe_exempt}


def _n_for(dtype: str, area_m2: float, mount_height: float, structure: str) -> int | None:
    """dtype·면적 → 필요 개수. 부착높이/종별 설치불가면 None."""
    try:
        allowed = C.detector_area(dtype, mount_height, structure)
    except (ValueError, KeyError):
        return None
    return max(1, math.ceil(area_m2 / allowed))


def detector_requirement(name: str, area_m2: float, *, occupancy: str = "",
                         structure: str = "fireproof", mount_height: float = 3.0,
                         corridor_length_m: float | None = None) -> dict:
    """방 → 요구 감지기 (종류·개수). 종류 확정 방은 N 확정, 미확정 방은 조건부 N + needs_review.

    반환:
      status: "determined" | "needs_review"
      dtype/type_kr, n_required, basis           (determined일 때)
      conditional {연기:N, 열:N}, reason          (needs_review일 때)
      area_m2, detail
    """
    cls = required_detector_type(name, occupancy, corridor_length_m)
    if cls["mandate"] == "smoke":
        n = _n_for(cls["dtype"], area_m2, mount_height, structure)
        allowed = C.detector_area(cls["dtype"], mount_height, structure)
        return {"status": "determined", "room": name, "area_m2": round(area_m2, 1),
                "type_kr": "연기", "dtype": cls["dtype"], "n_required": n,
                "basis": cls["basis"],
                "detail": f"{name} {area_m2:.1f}㎡ → 연기감지기 {n}개 필요"
                          f"(기준 {allowed:.0f}㎡/개, {cls['basis']})"}

    # conditional / designer_choice → 종류 미확정 → 조건부 N (연기 vs 열 범위)
    n_smoke = _n_for(_SMOKE, area_m2, mount_height, structure)
    n_heat_lo = _n_for(_HEAT_LOOSE, area_m2, mount_height, structure)     # 차동식2종 70㎡
    n_heat_hi = _n_for(_HEAT_STRICT, area_m2, mount_height, structure)    # 정온식2종 20㎡(보수적)
    if n_heat_lo is None:
        heat_str = "설치범위외"
    elif n_heat_hi is None or n_heat_lo == n_heat_hi:
        heat_str = str(n_heat_lo)
    else:
        heat_str = f"{n_heat_lo}~{n_heat_hi}"
    reason = cls["condition"] or "감지기 종류 미확정(법 미지정=설계자 선택)"
    return {"status": "needs_review", "room": name, "area_m2": round(area_m2, 1),
            "reason": reason, "basis": cls.get("basis", ""),
            "maybe_exempt": cls.get("maybe_exempt", False),
            "conditional": {"연기": n_smoke, "열(종별따라)": heat_str},
            "detail": f"{name} {area_m2:.1f}㎡ → 종류 미확정: 연기 {n_smoke}개 / "
                      f"열 {heat_str}개(차동식2종~정온식2종) — 확정하려면 감지기 인식 필요"}


def judge_rooms(rooms, *, occupancy="", structure=None, mount_height=3.0):
    """추출된 방 리스트 → 방별 NFTC 요구 판정 (① 파이프라인 연결점).

    rooms: [{"name","area_m2","reliable",...}] = room_extract_raster.extract_rooms_raster 출력.
    안전 규칙(위반 은폐=false-pass 방지):
      · reliable=False(면적 붕괴/병합 의심) → needs_review.
      · structure 미상(None) → needs_review — 기타구조를 내화로 가정하면 열 과소계산(위험방향).
      · 그 외 → detector_requirement (2.4.2 종류확정 방은 N, 미확정 방은 조건부 N).
    """
    out = []
    for r in rooms:
        name = r.get("name", "")
        area = float(r.get("area_m2", 0.0) or 0.0)
        if not r.get("reliable", False):
            out.append({"room": name, "status": "needs_review", "area_m2": round(area, 1),
                        "reason": "면적 신뢰도 낮음(추출 불안정·병합/붕괴 의심)"})
        elif structure not in ("fireproof", "noncombustible", "other"):
            out.append({"room": name, "status": "needs_review", "area_m2": round(area, 1),
                        "reason": "건물 구조(내화/기타) 미상 — 열 감지기 과소계산 위험방향"})
        else:
            out.append(detector_requirement(name, area, occupancy=occupancy,
                                            structure=structure, mount_height=mount_height))
    return out


if __name__ == "__main__":   # 데모(어린이집·창고 실명)
    demo = [
        ("계단실", "교육연구시설", None), ("복도", "교육연구시설", 45.0),
        ("복도", "교육연구시설", 12.0), ("복도", "교육연구시설", None),
        ("숙소-1", "공장", None), ("숙소-1", "숙박시설", None),
        ("보육실-0세반", "노유자시설", None), ("조리실", "교육연구시설", None),
        ("기계실", "공장", None), ("사무실", "업무시설", None),
    ]
    for nm, occ, ln in demo:
        r = detector_requirement(nm, 46.0, occupancy=occ, corridor_length_m=ln)
        print(f"[{r['status']:12}] {r['detail']}")
