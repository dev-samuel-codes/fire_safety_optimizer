# -*- coding: utf-8 -*-
"""
checks — plan-checkable 규칙의 기하 평가기(생성기·규칙엔진 공유).

각 평가기는 룸/장치 기하 + fireopt.constants 만으로 ViolationLabel 을 만든다.
도메인 결합은 constants 값 조회뿐, 거리·면적·피복 계산은 fireopt.geometry 재사용.
좌표는 모두 미터. 반환 status: violation / compliant.

대응 규칙 패밀리(rules.checkability == "plan" 중 합성 가능한 핵심 4종):
  · area_coverage   감지기 감지면적          (FV-DET-*_lt4* 등)
  · radial_coverage 스프링클러 R / 소화전 25m (FV-SPK-R_*, FV-HYD-horizontal_radius)
  · walking_coverage 소화기 20m / 직통계단     (FV-EXT-small_walk_max, FV-EVA-stair_walk_*)
  · zone_area_limit 경계구역 600㎡·변 50m     (FV-DET-detection_zone_*)
"""
from __future__ import annotations

import math

import numpy as np
import networkx as nx
from shapely.geometry import Point
from shapely.ops import unary_union
from scipy.spatial import cKDTree

from fireopt import constants as C
from fireopt import geometry as G
from ..schema.labels import ViolationLabel
from ..schema.rules import RULE_CATALOG


def _pt_evidence(points) -> list:
    """미피복/최악 점들을 evidence_geometry(점 목록)로."""
    pts = np.asarray(points, float).reshape(-1, 2)
    return [{"kind": "point", "coords": [round(float(x), 3), round(float(y), 3)]}
            for x, y in pts]


def _poly_evidence(polygon) -> list:
    ring = list(polygon.exterior.coords)
    return [{"kind": "polygon",
             "coords": [[round(float(x), 3), round(float(y), 3)] for x, y in ring]}]


def _mk(rule_id, status, *, measured=None, required=None, unit="",
        desc="", evidence=None, obj_ids=None) -> ViolationLabel:
    r = RULE_CATALOG.get(rule_id)
    sev = r.severity if r else ""
    return ViolationLabel(
        rule_id=rule_id, status=status, description=desc,
        evidence_geometry=evidence or [], evidence_object_ids=obj_ids or [],
        measured_value=measured, required_value=required, unit=unit,
        severity=sev, source="rule_engine")


# ── 감지기 종별 → constants 키(constants.detector_area 의 키 규칙과 동일) ──────
def detector_rule_key(dtype: str = "smoke_12", mount_height: float = 3.0,
                      structure: str = "fireproof") -> str:
    if dtype.startswith("smoke"):
        if dtype == "smoke_3":
            return "smoke_3_lt4"
        return "smoke_12_4to20" if mount_height >= 4 else "smoke_12_lt4"
    suffix = "fire" if structure in ("fireproof", "noncombustible") else "other"
    return f"{dtype}_lt4_{suffix}"


# ── ① 감지기 감지면적 ────────────────────────────────────────────────────────
def check_detector_area(room, detector_pts, *, dtype="smoke_12", mount_height=3.0,
                        structure="fireproof", obj_ids=None) -> ViolationLabel:
    """실 면적 대비 감지기 개수. n_required = ceil(area/감지면적). 미달 = 위반."""
    key = detector_rule_key(dtype, mount_height, structure)
    rule_id = f"FV-DET-{key}"
    try:
        allowed = C.detector_area(dtype, mount_height, structure)
    except (ValueError, KeyError) as e:           # 설치불가 부착높이/종별 → 크래시 대신 보류
        return _mk(rule_id, "not_applicable",
                   desc=f"{room.name}: {dtype} 부착높이 {mount_height}m 설치불가/범위외 ({e})",
                   evidence=_poly_evidence(room.polygon), obj_ids=obj_ids)
    n_placed = len(detector_pts)
    n_req = max(1, math.ceil(room.area / allowed))
    ok = n_placed >= n_req
    eff_area = (room.area / n_placed) if n_placed else None   # 미설치면 '개당 담당' 무의미
    sign = "≤" if (eff_area is not None and eff_area <= allowed + 1e-9) else ">"
    detail = (f", 개당 담당 {eff_area:.1f}㎡ {sign} 기준 {allowed:.0f}㎡"
              if eff_area is not None else f", 미설치 — 기준 {allowed:.0f}㎡/개")
    return _mk(
        rule_id, "compliant" if ok else "violation",
        measured=(round(eff_area, 1) if eff_area is not None else None),
        required=allowed, unit="m2",
        desc=f"{room.name}: 감지기 {n_placed}개(필요 {n_req}개){detail}",
        evidence=_poly_evidence(room.polygon), obj_ids=obj_ids)


# ── ② 수평거리(radial) 피복 — 스프링클러 / 옥내소화전 ──────────────────────────
def check_radial_coverage(region, device_pts, *, rule_id, radius, step=None,
                          name="", obj_ids=None) -> ViolationLabel:
    """region 각 부분 → 가장 가까운 장치 수평거리 ≤ radius. 미피복점 존재 = 위반."""
    step = step or max(radius / 6, 0.5)
    demand = G.coverage_demand(region, step=step, inset=0.3)
    corners = _region_vertices(region)            # 실코너 강제포함('각 부분' — inset 0.3로 빠진 꼭짓점)
    if len(corners):
        demand = np.vstack([demand, corners]) if len(demand) else corners
    if len(demand) == 0:
        c = region.centroid
        demand = np.array([[c.x, c.y]])
    centers = np.asarray(device_pts, float).reshape(-1, 2)
    if len(centers) == 0:
        worst, uncovered = math.inf, demand
    else:
        tree = cKDTree(centers)
        d, _ = tree.query(demand, k=1)
        worst = float(d.max())
        uncovered = demand[d > radius + 1e-9]
    ok = len(uncovered) == 0
    return _mk(
        rule_id, "compliant" if ok else "violation",
        measured=(None if math.isinf(worst) else round(worst, 2)),
        required=radius, unit="m",
        desc=(f"{name or '영역'}: 최원 수평거리 "
              f"{'∞' if math.isinf(worst) else f'{worst:.2f}m'} "
              f"{'≤' if ok else '>'} 기준 {radius}m, 미피복 {len(uncovered)}점"),
        evidence=_pt_evidence(uncovered[:12]), obj_ids=obj_ids)


# ── ③ 보행거리(walking) — 소화기 / 직통계단 ──────────────────────────────────
def _worst_walk(rooms, source_pts, step=0.5):
    """모든 거실점 → 최근접 source 보행거리의 (최댓값, 최악점). 통로 그래프 geodesic."""
    if not rooms or len(source_pts) == 0:
        return math.inf, None
    region = unary_union([r.polygon for r in rooms])
    graph, coords, tree = G.free_space_graph(region, step=step)
    if tree is None or len(coords) == 0:
        return math.inf, None
    src_nodes = {int(tree.query(np.asarray(s, float))[1]) for s in source_pts}
    dist = nx.multi_source_dijkstra_path_length(graph, src_nodes, weight="weight")
    demand = np.vstack([G.grid_points(r.polygon, step=max(step * 2, 1.0)) for r in rooms])
    worst, wpt = 0.0, None
    for d in demand:
        n = int(tree.query(d)[1])
        dd = dist.get(n, math.inf)
        if dd > worst:
            worst, wpt = dd, d
    return worst, wpt


def check_walking_coverage(rooms, source_pts, *, rule_id, limit, step=0.5,
                           label="", obj_ids=None) -> ViolationLabel:
    """거실 각 부분 → 최근접 source 보행거리 ≤ limit. 초과 = 위반.

    ⚠ 통행영역을 룸 union(열린공간)으로 근사 → 공유벽을 무시하므로 보행거리를 **과소평가**할 수
    있다(문/개구부 미반영). 경로 비연결(inf)은 위반이 아니라 needs_review 로 분리.
    """
    worst, wpt = _worst_walk(rooms, source_pts, step=step)
    if math.isinf(worst):
        return _mk(rule_id, "needs_review", required=limit, unit="m",
                   desc=f"{label or '거실'}: 통행경로 미연결 — 판정보류(문/개구부 정보 필요)",
                   obj_ids=obj_ids)
    ok = worst <= limit
    return _mk(
        rule_id, "compliant" if ok else "violation",
        measured=round(worst, 2), required=limit, unit="m",
        desc=(f"{label or '거실'}: 최원 보행거리 {worst:.2f}m {'≤' if ok else '>'} 기준 {limit}m "
              f"(※벽 무시 추정, 과소평가 가능)"),
        evidence=(_pt_evidence([wpt]) if wpt is not None else []), obj_ids=obj_ids)


def _region_vertices(region, inset=0.05):
    """region 외곽 꼭짓점을 중심방향으로 살짝(inset) 들여 demand 에 추가('각 부분' 강제포함)."""
    from shapely.geometry import Polygon as _P, MultiPolygon as _MP
    polys = [region] if isinstance(region, _P) else (
        list(region.geoms) if isinstance(region, _MP) else [])
    pts = []
    for p in polys:
        c = p.centroid
        for x, y in list(p.exterior.coords)[:-1]:
            dx, dy = c.x - x, c.y - y
            d = (dx * dx + dy * dy) ** 0.5 or 1.0
            pts.append([x + dx / d * inset, y + dy / d * inset])
    return np.array(pts) if pts else np.empty((0, 2))


# ── ④ 경계구역 면적/변 ───────────────────────────────────────────────────────
def check_detection_zone(zone_polygon, *, name="", obj_ids=None) -> list:
    """경계구역: 면적 ≤ 600㎡, 한 변 ≤ 50m. 둘 다 평가해 위반 라벨 목록 반환."""
    area_max = C.DETECTOR["detection_zone_area_max"]["value"]   # 600
    side_max = C.DETECTOR["detection_zone_side_max"]["value"]   # 50
    area = zone_polygon.area
    coords = list(zone_polygon.exterior.coords)
    longest = max((math.dist(coords[i], coords[i + 1])
                   for i in range(len(coords) - 1)), default=0.0)
    out = []
    out.append(_mk(
        "FV-DET-detection_zone_area_max",
        "violation" if area > area_max else "compliant",
        measured=round(area, 1), required=area_max, unit="m2",
        desc=f"{name or '경계구역'} 면적 {area:.1f}㎡ vs 기준 {area_max:.0f}㎡",
        evidence=_poly_evidence(zone_polygon), obj_ids=obj_ids))
    out.append(_mk(
        "FV-DET-detection_zone_side_max",
        "violation" if longest > side_max else "compliant",
        measured=round(longest, 1), required=side_max, unit="m",
        desc=f"{name or '경계구역'} 최장변 {longest:.1f}m vs 기준 {side_max:.0f}m",
        evidence=_poly_evidence(zone_polygon), obj_ids=obj_ids))
    return out


# ── 오케스트레이터: 도면(룸+장치) → 적용규칙 선택 → 위반 판정 ─────────────────
class _Room:
    """check_* 프리미티브가 기대하는 최소 룸(.name/.polygon/.area)."""
    __slots__ = ("name", "polygon", "area")

    def __init__(self, name, polygon):
        self.name, self.polygon, self.area = name, polygon, polygon.area


def _in(poly, p):
    pt = Point(p)
    return poly.contains(pt) or poly.distance(pt) < 0.3


def check_layout(rooms, devices, meta=None):
    """룸(_Room[]) + 장치({facility:[(x,y)]}) + 건물메타 → ViolationLabel[].

    건물 구조·용도로 **적용 규칙을 선택**(스프링클러 R, 직통계단 보행한도 등)해
    상호배타 규칙을 한 번만 판정한다. 생성기·규칙엔진 공용 진입점.
    """
    from shapely.geometry import Polygon  # 지역 import(상단 미사용 회피)
    meta = meta or {}
    structure = meta.get("structure", "fireproof")
    mount_height = float(meta.get("mount_height", 3.0) or 3.0)   # 부착높이(층고) — 감지면적 4m 분기
    region = unary_union([r.polygon for r in rooms]) if rooms else None
    out = []

    sp = devices.get("sprinkler") or []
    if region is not None and sp:
        occ = meta.get("sprinkler_occupancy")
        Rv = C.sprinkler_radius(structure, occ)
        rid = ({"stage_special": "FV-SPK-R_stage_special", "apartment": "FV-SPK-R_apartment"}.get(occ)
               or ("FV-SPK-R_fireproof" if structure in ("fireproof", "noncombustible")
                   else "FV-SPK-R_non_fireproof"))
        out.append(check_radial_coverage(region, sp, rule_id=rid, radius=Rv, name="스프링클러"))

    hy = devices.get("hydrant") or []
    if region is not None and hy:
        out.append(check_radial_coverage(
            region, hy, rule_id="FV-HYD-horizontal_radius",
            radius=C.HYDRANT["horizontal_radius"]["value"], name="옥내소화전"))

    # 감지기: 종별로 감지면적 상수가 다르다(연기 1·2종 150㎡ vs 차동식 열 2종 70㎡ vs 정온식 2종 20㎡).
    # 인식이 종별을 주므로 방의 감지기 종류에 맞는 상수로 분기해야 한다 — 안 하면 열감지기 방을
    # 연기 기준(150)으로 과소계산해 '미달인데 적합'으로 통과시키는 false-pass(위반 은폐)가 난다.
    smoke = devices.get("detector_smoke") or devices.get("detector") or []   # legacy "detector"=연기
    heat = devices.get("detector_heat") or []
    if rooms and (smoke or heat):
        default_dtype = meta.get("detector_type", "smoke_12")
        for r in rooms:
            s_in = [p for p in smoke if _in(r.polygon, p)]
            h_in = [p for p in heat if _in(r.polygon, p)]
            if h_in:
                n = len(h_in)
                # 열감지기 종별이 **명시**되면(생성기 시나리오 / HITL 라벨 / 도면 meta) 그 종별로
                # 정밀 판정한다. 종별 미상(기본 smoke_12 등)일 때만 관대/엄격 끝값 bounded로 보류.
                if str(default_dtype).startswith(("diff", "fixed", "heat")):
                    out.append(check_detector_area(r, h_in, dtype=default_dtype,
                                                   mount_height=mount_height, structure=structure))
                else:
                    # 종별 미상 → 최관대(차동식1종 90/50㎡)로도 미달=확정 위반, 최엄격(정온식2종
                    # 20/15㎡)도 충족=확정 적합, 사이=종별미상 확인필요(단정 금지, false-pass 방지).
                    try:
                        a_lo = C.detector_area("diff_spot_1", mount_height, structure)   # 최대면적=최소개수
                        a_hi = C.detector_area("fixed_spot_2", mount_height, structure)  # 최소면적=최대개수
                    except (ValueError, KeyError):     # 열 스포트형은 4m 미만 스펙 → 부착높이 ≥4m 범위외
                        out.append(_mk("FV-DET-heat_bounded", "not_applicable",
                            desc=f"{r.name}: 열감지기 {n}개, 부착높이 {mount_height:.0f}m — 열감지기 감지면적 범위외(≥4m), 확인 필요",
                            evidence=_poly_evidence(r.polygon)))
                    else:
                        need_lo = max(1, math.ceil(r.area / a_lo))
                        need_hi = max(1, math.ceil(r.area / a_hi))
                        if n >= need_hi:
                            out.append(_mk("FV-DET-heat_bounded", "compliant",
                                desc=f"{r.name}: 열감지기 {n}개 ≥ 최엄격 필요 {need_hi}개 → 종별무관 적합(부착높이 {mount_height:.0f}m)",
                                evidence=_poly_evidence(r.polygon)))
                        elif n < need_lo:
                            out.append(_mk("FV-DET-heat_bounded", "violation",
                                desc=f"{r.name}: 열감지기 {n}개 < 최관대 필요 {need_lo}개 → 종별무관 위반(부착높이 {mount_height:.0f}m)",
                                evidence=_poly_evidence(r.polygon)))
                        else:
                            out.append(_mk("FV-DET-heat_bounded", "not_applicable",
                                desc=f"{r.name}: 열감지기 {n}개(필요 {need_lo}~{need_hi}개) — 종별(차동식/정온식) 미상, 확인 필요",
                                evidence=_poly_evidence(r.polygon)))
            if s_in:
                # 도면이 연기 종별을 선언하면(smoke_3=50㎡ 등) 그대로, 아니면 smoke_12(150㎡).
                # heat 분기와 대칭 — 선언된 3종 설계를 150㎡로 관대판정하면 false-pass.
                smoke_dt = default_dtype if str(default_dtype).startswith("smoke") else "smoke_12"
                out.append(check_detector_area(r, s_in, dtype=smoke_dt,
                                               mount_height=mount_height, structure=structure))
            if not s_in and not h_in:   # 무설치 방
                from .detector_type import _MAYBE_EXEMPT
                if any(x in (r.name or "") for x in _MAYBE_EXEMPT):
                    # NFTC 2.4.5 설치제외 가능 장소(화장실·목욕실 등) → 미설치를 위반으로 단정하지
                    # 않음(false-violation 방지). 설계자 판단 영역이므로 '확인 필요'.
                    rk = f"FV-DET-{detector_rule_key(default_dtype, mount_height, structure)}"
                    out.append(_mk(rk, "not_applicable",
                        desc=f"{r.name}: 감지기 미설치 — NFTC 2.4.5 설치제외 가능 장소(확인 필요)",
                        evidence=_poly_evidence(r.polygon)))
                else:                    # 그 외 무설치 방 → 도면 기본종별로 미달 판정
                    out.append(check_detector_area(r, [], dtype=default_dtype,
                                                   mount_height=mount_height, structure=structure))

    ext = devices.get("extinguisher") or []
    if rooms and ext:
        thr = C.EXTINGUISHER["room_partition_threshold"]["value"]    # 33㎡
        for r in rooms:
            if r.area >= thr:
                req = math.ceil(r.area / thr)
                n = sum(1 for p in ext if _in(r.polygon, p))
                out.append(_mk("FV-EXT-room_partition_threshold",
                               "violation" if n < req else "compliant",
                               measured=n, required=req, unit="개",
                               desc=f"{r.name or '실'}: 소화기 {n}개(33㎡ 구획 필요 {req}개)",
                               evidence=_poly_evidence(r.polygon)))
        out.append(check_walking_coverage(
            rooms, ext, rule_id="FV-EXT-small_walk_max",
            limit=C.EXTINGUISHER["small_walk_max"]["value"], label="소화기"))

    ev = devices.get("evacuation") or []
    if rooms and ev:
        limit = C.stair_walk_limit(structure)
        rid = ("FV-EVA-stair_walk_fireproof" if structure in ("fireproof", "noncombustible")
               else "FV-EVA-stair_walk_default")
        out.append(check_walking_coverage(rooms, ev, rule_id=rid, limit=limit, label="직통계단"))
    return out


_CAT2FAC = {
    "sprinkler_head": "sprinkler", "smoke_detector": "detector_smoke", "heat_detector": "detector_heat",
    "detector_linear": "detector_heat", "hydrant_box": "hydrant", "extinguisher": "extinguisher",
    "exit_light": "evacuation", "directional_light": "evacuation",
    "stair": "evacuation", "evac_route": "evacuation", "escape_device": "evacuation",
}


def from_annotation(ann):
    """DrawingAnnotation → (rooms[_Room], devices{facility:[(x,y)]}, meta)."""
    from shapely.geometry import Polygon
    rooms, devices = [], {}
    for o in ann.objects:
        wg = o.world_geometry
        if not wg:
            continue
        if o.category_key == "room" and isinstance(wg[0], (list, tuple)):
            if len(wg) < 3:                        # 퇴화룸(점/선) → skip(전체 크래시 방지)
                continue
            try:
                poly = Polygon(wg)
                if not poly.is_valid:
                    poly = poly.buffer(0)
            except Exception:
                continue
            if isinstance(poly, Polygon) and poly.area > 1e-6:
                rooms.append(_Room(o.attributes.get("name", ""), poly))
        elif len(wg) == 2 and isinstance(wg[0], (int, float)):
            fac = _CAT2FAC.get(o.category_key)
            if fac:
                devices.setdefault(fac, []).append((float(wg[0]), float(wg[1])))
    return rooms, devices, (ann.building_meta or {})


def check_drawing(ann):
    """규칙엔진 진입점: DrawingAnnotation → ViolationLabel[](source=rule_engine)."""
    return check_layout(*from_annotation(ann))


def summarize(violations):
    v = [x for x in violations if x.status == "violation"]
    return {"checked": len(violations), "violations": len(v),
            "by_severity": {s: sum(1 for x in v if x.severity == s)
                            for s in ("critical", "major", "minor", "info")},
            "violated_rules": sorted({x.rule_id for x in v})}
