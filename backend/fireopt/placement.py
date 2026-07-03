# -*- coding: utf-8 -*-
"""
placement — 검증된 NFSC/NFTC 상수(constants.py)로 5개 소방시설을 자동 배치.

설계 원칙
  - 시설별 순수 함수 1개. IFC·클래시 인지 없음(기하 + 상수만).
  - 수평거리(스프링클러 R, 소화전 25m) → greedy set-cover(원형 피복, 헤드 수 최소화).
  - 면적기준(감지기 감지면적) → √면적 격자.
  - 보행거리(소화기 20m, 피난) → 통로 그래프 geodesic 로 검증.
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union

from . import constants as C
from . import geometry as G


# ---------------------------------------------------------------------------
@dataclass
class Placement:
    facility: str                 # sprinkler|detector|extinguisher|hydrant|evacuation
    kind: str                     # head|smoke_12|small|hydrant_box|evac_device ...
    point: tuple                  # (x, y) metres
    room: str = ""
    attrs: dict = field(default_factory=dict)


@dataclass
class PlaceConfig:
    structure: str = "fireproof"          # 내화 / 비내화(non_fireproof)
    occupancy: str = "common"             # 소화기 능력단위 용도군
    num_floors: int = 1
    detector_type: str = "smoke_12"
    mount_height: float = 3.0
    sprinkler_occupancy: str | None = None   # stage_special / apartment override
    fireproof_noncombustible_finish: bool = False
    walk_grid_step: float = 0.5


@dataclass
class Layout:
    placements: dict = field(default_factory=dict)   # facility -> list[Placement]
    checks: dict = field(default_factory=dict)        # 산정/준수 정보
    free_graph: object = None                          # (G, coords, tree) 보행거리용

    def all_points(self, facility=None):
        out = []
        for fac, lst in self.placements.items():
            if facility and fac != facility:
                continue
            out += [p.point for p in lst]
        return np.array(out) if out else np.empty((0, 2))


# ---------------------------------------------------------------------------
# 개별 시설 배치
# ---------------------------------------------------------------------------
def place_sprinkler(room, cfg: PlaceConfig) -> list:
    """스프링클러 헤드 배치.

    실무 표준인 정방형 격자(피치 S = √2·R)를 룸 중심에 정렬해 깔고(near-optimal 개수),
    격자만으로 덮이지 않는 잔여 수요점(코너 등)에만 top-up 헤드를 추가해 완전피복 보장.
    """
    R = C.sprinkler_radius(cfg.structure, cfg.sprinkler_occupancy or _room_occ(room))
    S = C.square_pitch(R)
    poly = room.polygon
    cx, cy = poly.centroid.x, poly.centroid.y
    minx, miny, maxx, maxy = poly.bounds

    # 중심 정렬 정방형 격자 노드 중 룸 내부 점
    heads = []
    nx = max(1, int(math.ceil((maxx - minx) / S)) + 1)
    ny = max(1, int(math.ceil((maxy - miny) / S)) + 1)
    from shapely.geometry import Point as _Pt
    for i in range(nx):
        for j in range(ny):
            x = cx + (i - (nx - 1) / 2) * S
            y = cy + (j - (ny - 1) / 2) * S
            if poly.contains(_Pt(x, y)):
                heads.append((x, y))
    if not heads:
        heads = [(cx, cy)]

    # 잔여 top-up: 미피복 수요점을 헤드로 추가(코너 보강)
    step = max(min(S / 2, 0.6), 0.4)
    demand = G.coverage_demand(poly, step=step, inset=min(0.3, _short_side(poly) / 4))
    if len(demand):
        uncovered = demand[~G.coverage_mask(demand, np.array(heads), R)]
        while len(uncovered):
            h = tuple(uncovered[0]); heads.append(h)
            uncovered = uncovered[~G.coverage_mask(uncovered, np.array([h]), R)]

    return [Placement("sprinkler", "head", tuple(h), room.name,
                      {"R": R, "S": round(S, 3)}) for h in heads]


def place_detector(room, cfg: PlaceConfig) -> list:
    """감지기: 개당 감지면적 기준 √면적 격자. n ≈ ceil(면적/감지면적)."""
    a_per = C.detector_area(cfg.detector_type, cfg.mount_height, cfg.structure)
    step = math.sqrt(a_per)
    pts = G.grid_points(room.polygon, step=step)
    n_req = max(1, math.ceil(room.area / a_per))
    # 격자 점이 너무 많으면(작은 방) n_req 개로 절감, 적으면 격자 그대로
    if len(pts) > n_req:
        # 균등 샘플
        idx = np.linspace(0, len(pts) - 1, n_req).round().astype(int)
        pts = pts[np.unique(idx)]
    elif len(pts) == 0:
        c = room.polygon.centroid
        pts = np.array([[c.x, c.y]])
    return [Placement("detector", cfg.detector_type, tuple(p), room.name,
                      {"area_per": a_per, "n_required": n_req}) for p in pts]


def place_extinguisher(rooms, cfg: PlaceConfig, free_graph=None) -> tuple:
    """소화기: 33㎡ 구획 규칙 + 보행거리 20m 검증. 방마다 최소 1개(33㎡↑면 ceil(면적/33))."""
    walk_max = C.EXTINGUISHER["small_walk_max"]["value"]            # 20 m
    part = C.EXTINGUISHER["room_partition_threshold"]["value"]      # 33 m2
    placements = []
    for room in rooms:
        n = max(1, math.ceil(room.area / part)) if room.area >= part else 1
        pts = G.grid_points(room.polygon, step=math.sqrt(room.area / n)) if n > 1 else None
        if pts is None or len(pts) == 0:
            c = room.polygon.centroid
            pts = np.array([[c.x, c.y]])
        elif len(pts) > n:
            idx = np.linspace(0, len(pts) - 1, n).round().astype(int)
            pts = pts[np.unique(idx)]
        for p in pts:
            placements.append(Placement("extinguisher", "small", tuple(p), room.name,
                                        {"walk_max": walk_max}))
    # 능력단위 요구량(건물 단위)
    unit_area = C.extinguisher_unit_area(cfg.occupancy, cfg.fireproof_noncombustible_finish)
    total_area = sum(r.area for r in rooms)
    units_required = math.ceil(total_area / unit_area)
    checks = {"unit_area_per_capacity": unit_area,
              "capacity_units_required": units_required,
              "placed_count": len(placements)}
    # 보행거리 검증
    if free_graph is not None:
        Gg, coords, tree = free_graph
        demand = np.vstack([G.grid_points(r.polygon, step=max(cfg.walk_grid_step * 2, 1.0))
                            for r in rooms]) if rooms else np.empty((0, 2))
        sources = np.array([p.point for p in placements])
        worst = G.max_walk_to_sources(Gg, coords, tree, demand, sources)
        checks["max_walk_m"] = None if math.isinf(worst) else round(worst, 2)
        checks["walk_ok"] = (worst <= walk_max)
        if not checks["walk_ok"] and not math.isinf(worst):
            warnings.warn(f"소화기 보행거리 {worst:.1f}m > {walk_max}m — 추가 배치 필요", RuntimeWarning)
    return placements, checks


def place_hydrant(rooms, cfg: PlaceConfig) -> tuple:
    """옥내소화전: 수평거리 25m 원형 피복 greedy set-cover + 수원/펌프 산정."""
    radius = C.HYDRANT["horizontal_radius"]["value"]               # 25 m
    region = unary_union([r.polygon for r in rooms]) if rooms else None
    if region is None or region.is_empty:
        return [], {}
    step = max(radius / 6, 1.5)
    demand = G.coverage_demand(region, step=step, inset=0.3)
    if len(demand) == 0:
        c = region.centroid
        demand = np.array([[c.x, c.y]])
    r_eff = max(radius - step / math.sqrt(2), radius * 0.5)
    chosen = G.greedy_set_cover(demand, demand, radius=r_eff)
    if not chosen:  # 아주 작은 건물
        chosen = [0]
    placements = [Placement("hydrant", "hydrant_box", tuple(demand[i]), "",
                            {"radius": radius}) for i in chosen]
    n = len(placements)
    water = C.hydrant_water_source(cfg.num_floors, n)
    pump = C.hydrant_pump_flow(cfg.num_floors, n)
    checks = {"count": n,
              "sim_open_cap": C.hydrant_sim_open_cap(cfg.num_floors),
              "water_source_m3": round(water, 2),
              "pump_flow_Lpm": round(pump, 1)}
    return placements, checks


def place_evacuation(rooms, doors, cfg: PlaceConfig, free_graph=None) -> tuple:
    """피난: 직통계단 보행거리 검증 + 피난기구 개수 산정 + 출구 마커."""
    limit = C.stair_walk_limit(cfg.structure)                       # 30/50 ...
    denom = C.escape_device_denominator(_occ_to_escape(cfg.occupancy))
    total_area = sum(r.area for r in rooms)
    devices_required = math.ceil(total_area / denom) if total_area > 0 else 0
    # 출구 후보: 문 점(없으면 영역 경계 최저 y 점)
    exits = [(d.x, d.y) for d in doors] if doors else []
    placements = [Placement("evacuation", "exit", e, "", {}) for e in exits]
    checks = {"stair_walk_limit_m": limit,
              "escape_denominator": denom,
              "escape_devices_required": devices_required,
              "exit_count": len(exits)}
    if free_graph is not None and exits:
        Gg, coords, tree = free_graph
        demand = np.vstack([G.grid_points(r.polygon, step=max(cfg.walk_grid_step * 2, 1.0))
                            for r in rooms]) if rooms else np.empty((0, 2))
        worst = G.max_walk_to_sources(Gg, coords, tree, demand, np.array(exits))
        checks["max_walk_to_exit_m"] = None if math.isinf(worst) else round(worst, 2)
        checks["walk_ok"] = (worst <= limit)
    return placements, checks


# ---------------------------------------------------------------------------
# 통합
# ---------------------------------------------------------------------------
def build_free_graph(rooms, step: float = 0.5):
    """룸 합집합을 이동가능영역으로 보행거리 그래프 구성."""
    if not rooms:
        return None
    region = unary_union([r.polygon for r in rooms])
    return G.free_space_graph(region, step=step)


def build_layout(rooms, cfg: PlaceConfig, doors=None) -> Layout:
    """5개 시설 전체 배치 → Layout."""
    doors = doors or []
    free_graph = build_free_graph(rooms, step=cfg.walk_grid_step)

    placements = {"sprinkler": [], "detector": [], "extinguisher": [],
                  "hydrant": [], "evacuation": []}
    checks = {}

    for room in rooms:
        placements["sprinkler"] += place_sprinkler(room, cfg)
        placements["detector"] += place_detector(room, cfg)

    ext, ext_chk = place_extinguisher(rooms, cfg, free_graph)
    placements["extinguisher"] = ext; checks["extinguisher"] = ext_chk

    hyd, hyd_chk = place_hydrant(rooms, cfg)
    placements["hydrant"] = hyd; checks["hydrant"] = hyd_chk

    evac, evac_chk = place_evacuation(rooms, doors, cfg, free_graph)
    placements["evacuation"] = evac; checks["evacuation"] = evac_chk

    checks["counts"] = {k: len(v) for k, v in placements.items()}
    return Layout(placements=placements, checks=checks, free_graph=free_graph)


# ---------------------------------------------------------------------------
def _short_side(poly) -> float:
    """폴리곤 bounding box 의 짧은 변 길이(인셋 상한 계산용)."""
    minx, miny, maxx, maxy = poly.bounds
    return max(min(maxx - minx, maxy - miny), 1e-3)


def _room_occ(room) -> str | None:
    """룸 occupancy → 스프링클러 R 결정용 보조."""
    occ = getattr(room, "occupancy", None)
    if occ in ("stage_special", "apartment"):
        return occ
    return None


def _occ_to_escape(occupancy: str) -> str:
    if occupancy in ("lodging", "medical", "elderly"):
        return "lodging"
    if occupancy in ("amusement", "culture", "sales"):
        return "amusement"
    return "other"
