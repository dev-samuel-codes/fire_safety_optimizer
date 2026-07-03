# -*- coding: utf-8 -*-
"""
ifc_loader — IFC(개방형 BIM) → 깨끗한 미터 단위 2D shapely 기하 추출.

설계 의도 / 함정 캡슐화
-----------------------
1) **이중 단위 스케일 함정** (THE silent killer)
   - ifcopenshell.geom.create_shape(...) 의 메쉬 정점(use-world-coords)은
     지오메트리 커널이 이미 **미터(SI)** 로 변환해 반환한다 → *scale 곱하면 안 됨.
   - 반면 ObjectPlacement 행렬(get_local_placement)·IfcBuildingStorey.Elevation 은
     **모델 원시 단위**(mm 등) → 반드시 *scale 해서 미터로 변환.
   본 모듈은 이 두 경로를 한 곳에서만 다루고, open_model() 직후 한 변을 측정해 검증한다.

2) **SWIG float-buffer GC 버그**: np.array(geom.verts).reshape 직접 사용 시 첫 정점이
   깨져 면적이 ~1e296 으로 폭주할 수 있음 → ifcopenshell.util.shape.get_vertices() 사용,
   create_shape 결과를 변수에 살려둔 채로(.geometry 읽기 전에) 사용.

3) IFC 품질 편차: IfcSpace 가 없을 수 있음 → 벽 풋프린트 합집합으로 룸 대체(fallback).
   body geometry 가 없으면 placement 원점만으로 처리(warn-and-continue).
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.unit
import ifcopenshell.util.shape
import ifcopenshell.util.placement

from shapely.geometry import Polygon, MultiPoint, Point
from shapely.ops import unary_union


# ---------------------------------------------------------------------------
# 데이터 구조
# ---------------------------------------------------------------------------
@dataclass
class Room:
    name: str
    polygon: Polygon          # 미터 단위 2D 풋프린트
    area: float               # m²  (polygon.area)
    storey: str
    elevation: float          # m
    structure: str = "fireproof"     # 설정값(내화/비내화)
    occupancy: str = "common"        # 설정값(용도)
    ifc_id: int | None = None
    area_source: str = "space"        # 'space' | 'wall_fallback'


@dataclass
class BuildingModel:
    path: str
    schema: str
    scale: float                       # 모델단위 → 미터 변환 계수
    storeys: list                      # [(name, elevation_m), ...]
    rooms: list = field(default_factory=list)
    wall_polys: list = field(default_factory=list)   # shapely Polygon (미터)
    door_points: list = field(default_factory=list)  # shapely Point (미터)

    @property
    def total_area(self) -> float:
        return sum(r.area for r in self.rooms)


# ---------------------------------------------------------------------------
# 기하 설정 / 오픈
# ---------------------------------------------------------------------------
def _settings() -> "ifcopenshell.geom.settings":
    """월드좌표 메쉬 설정. 0.7+ 문자열 키 API 사용."""
    s = ifcopenshell.geom.settings()
    s.set("use-world-coords", True)
    return s


def open_model(path: str) -> tuple["ifcopenshell.file", float]:
    """IFC 열기 + 모델단위→미터 스케일 계산."""
    model = ifcopenshell.open(path)
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)  # 예: mm 모델 → 0.001
    return model, scale


# ---------------------------------------------------------------------------
# 풋프린트 추출
# ---------------------------------------------------------------------------
def _xy_and_z(element, settings):
    """요소 메쉬 → (XY convex_hull Polygon, zmin, zmax). 월드좌표(미터)."""
    try:
        shape = ifcopenshell.geom.create_shape(settings, element)
    except Exception:
        return None, 0.0, 0.0
    geom = shape.geometry  # shape 를 살려둔 채 읽기 (GC 버그 회피)
    verts = ifcopenshell.util.shape.get_vertices(geom)  # (N,3) numpy, 미터
    if verts is None or len(verts) < 3:
        return None, 0.0, 0.0
    zmin, zmax = float(verts[:, 2].min()), float(verts[:, 2].max())
    poly = MultiPoint([tuple(p) for p in verts[:, :2]]).convex_hull
    if not isinstance(poly, Polygon) or poly.area <= 1e-9:
        return None, zmin, zmax
    return poly, zmin, zmax


def footprint_polygon(element, settings) -> Polygon | None:
    """요소의 body geometry → XY 투영 풋프린트 Polygon (미터)."""
    return _xy_and_z(element, settings)[0]


def _placement_point(element, scale: float) -> Point | None:
    """요소 배치원점 → 미터 단위 Point. (placement 행렬은 원시단위 → *scale)"""
    if not getattr(element, "ObjectPlacement", None):
        return None
    try:
        m = ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)
    except Exception:
        return None
    return Point(float(m[0, 3]) * scale, float(m[1, 3]) * scale)


# ---------------------------------------------------------------------------
# 층 / 룸 / 벽 / 문
# ---------------------------------------------------------------------------
def storeys(model, scale: float) -> list:
    out = []
    for s in model.by_type("IfcBuildingStorey"):
        elev = float(s.Elevation) * scale if s.Elevation is not None else 0.0
        out.append((s.Name or f"Storey#{s.id()}", elev))
    return sorted(out, key=lambda t: t[1])


def _storey_of(element) -> str:
    """요소가 속한 층 이름(컨테인먼트 역추적)."""
    try:
        for rel in getattr(element, "ContainedInStructure", []) or []:
            st = rel.RelatingStructure
            if st and st.is_a("IfcBuildingStorey"):
                return st.Name or f"Storey#{st.id()}"
    except Exception:
        pass
    return ""


def extract_spaces(model, scale: float, settings,
                   structure: str = "fireproof", occupancy: str = "common") -> list:
    rooms = []
    for sp in model.by_type("IfcSpace"):
        poly, zmin, _ = _xy_and_z(sp, settings)
        if poly is None:
            continue
        name = sp.LongName or sp.Name or f"Space#{sp.id()}"
        storey = _storey_of(sp)
        rooms.append(Room(name=name, polygon=poly, area=poly.area,
                          storey=storey, elevation=round(zmin, 3),
                          structure=structure, occupancy=occupancy,
                          ifc_id=sp.id(), area_source="space"))
    return rooms


def extract_walls(model, settings, limit: int | None = None) -> list:
    polys = []
    walls = list(model.by_type("IfcWall")) + list(model.by_type("IfcWallStandardCase"))
    for w in walls:
        poly = footprint_polygon(w, settings)
        if poly is not None:
            polys.append(poly)
        if limit and len(polys) >= limit:
            break
    return polys


def extract_doors(model, scale: float) -> list:
    pts = []
    for d in model.by_type("IfcDoor"):
        p = _placement_point(d, scale)
        if p is not None:
            pts.append(p)
    return pts


def _uniquify_names(rooms: list) -> list:
    """동일 이름 룸(예: 듀플렉스 'Living Room' 2개)을 '이름 #2'식으로 구분."""
    counts: dict = {}
    totals: dict = {}
    for r in rooms:
        totals[r.name] = totals.get(r.name, 0) + 1
    for r in rooms:
        if totals[r.name] > 1:
            counts[r.name] = counts.get(r.name, 0) + 1
            if counts[r.name] > 1:
                r.name = f"{r.name} #{counts[r.name]}"
    return rooms


def rooms_on_storey(rooms: list, elevation: float, tol: float = 1.5) -> list:
    """주어진 층 표고(미터) 근처의 룸만 선택(단층 처리용)."""
    return [r for r in rooms if abs(r.elevation - elevation) <= tol]


def auto_storey_elevation(rooms: list, tol: float = 1.5) -> float:
    """룸 표고를 군집화해 룸이 가장 많은 층의 대표 표고(미터) 반환 — 임의 IFC 자동설계용."""
    if not rooms:
        return 0.0
    elevs = [r.elevation for r in rooms]
    best_e, best_n = elevs[0], 0
    for e in elevs:
        n = sum(1 for v in elevs if abs(v - e) <= tol)
        if n > best_n:
            best_n, best_e = n, e
    band = [v for v in elevs if abs(v - best_e) <= tol]
    return sum(band) / len(band)


def storey_room_summary(rooms: list, tol: float = 1.5) -> list:
    """층별(표고 군집) 룸 개수 요약 [(elev, count), ...] — UI 선택지용."""
    out, used = [], []
    for r in sorted(rooms, key=lambda x: x.elevation):
        if any(abs(r.elevation - u) <= tol for u in used):
            continue
        used.append(r.elevation)
        cnt = sum(1 for x in rooms if abs(x.elevation - r.elevation) <= tol)
        out.append((round(r.elevation, 2), cnt))
    return out


def _rooms_from_walls(wall_polys: list, structure: str, occupancy: str) -> list:
    """IfcSpace 부재 시 폴백: 벽 풋프린트 합집합 외곽을 단일 '룸'으로."""
    if not wall_polys:
        return []
    hull = unary_union(wall_polys).convex_hull
    if not isinstance(hull, Polygon) or hull.area <= 1e-9:
        return []
    return [Room(name="(walls-fallback)", polygon=hull, area=hull.area,
                 storey="", elevation=0.0, structure=structure,
                 occupancy=occupancy, area_source="wall_fallback")]


# ---------------------------------------------------------------------------
# 통합 로더
# ---------------------------------------------------------------------------
def load(path: str, structure: str = "fireproof", occupancy: str = "common",
         wall_limit: int | None = 400) -> BuildingModel:
    """IFC → BuildingModel(rooms/walls/doors/storeys, 모두 미터)."""
    model, scale = open_model(path)
    settings = _settings()

    st = storeys(model, scale)
    walls = extract_walls(model, settings, limit=wall_limit)
    doors = extract_doors(model, scale)
    rooms = extract_spaces(model, scale, settings, structure, occupancy)
    if not rooms:
        warnings.warn("IfcSpace 없음 → 벽 풋프린트 폴백으로 룸 생성", RuntimeWarning)
        rooms = _rooms_from_walls(walls, structure, occupancy)

    _uniquify_names(rooms)
    bm = BuildingModel(path=path, schema=model.schema, scale=scale,
                       storeys=st, rooms=rooms, wall_polys=walls, door_points=doors)

    # 단위-스케일 정합성 가드: 룸 면적이 비정상(≈0 또는 폭주)인지 조기 경고
    if bm.rooms:
        a = bm.rooms[0].area
        if a < 0.1 or a > 1e5:
            warnings.warn(
                f"룸 면적 비정상({a:.3g} m²) — 단위 스케일/기하 점검 필요(이중 스케일 함정?)",
                RuntimeWarning)
    return bm
