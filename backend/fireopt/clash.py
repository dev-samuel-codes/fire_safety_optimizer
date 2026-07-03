# -*- coding: utf-8 -*-
"""
clash — 타 공종(전기/기계) IFC 를 2D 로 평탄화하고 소방시설과의 교차를 감지.

핵심 함정 캡슐화(검증된 STRtree 주의점)
  - STRtree.query 는 자기쌍/대칭쌍/동일공종쌍도 돌려준다. 여기서는 fire→mep 단방향
    질의만 하므로 자기/대칭 문제는 구조적으로 없으나, **공종 교차(fire vs mep)** 와
    **z-밴드(같은 층)** 필터는 반드시 적용(층이 다르면 평면상 겹쳐도 실제 충돌 아님).
  - hard = 장치 실제 형상이 MEP 와 겹침 / soft = 이격거리(margin) 내 근접.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, asdict

import numpy as np
import ifcopenshell.geom
from shapely.geometry import Point, Polygon, MultiPoint
from shapely.strtree import STRtree

from . import ifc_loader as L


# 전기/기계 공종에서 충돌 대상이 되는 IFC 타입(IFC2x3/IFC4 공통 다수 포함)
MEP_TYPES = (
    "IfcFlowSegment", "IfcCableCarrierSegment", "IfcCableSegment",
    "IfcDuctSegment", "IfcPipeSegment", "IfcFlowFitting", "IfcCableCarrierFitting",
    "IfcDuctFitting", "IfcPipeFitting", "IfcFlowTerminal", "IfcLightFixture",
    "IfcElectricAppliance", "IfcOutlet", "IfcEnergyConversionDevice",
    "IfcAirTerminal", "IfcSanitaryTerminal", "IfcFlowController",
    "IfcDistributionFlowElement", "IfcDistributionControlElement",
)


@dataclass
class MepGeom:
    geom: object        # shapely Polygon (XY, metres)
    zmin: float         # 요소 수직 범위(미터)
    zmax: float
    type: str
    ifc_id: int

    @property
    def z(self):
        return (self.zmin + self.zmax) / 2.0


@dataclass
class Clash:
    fire_facility: str
    fire_kind: str
    fire_room: str
    mep_type: str
    mep_id: int
    severity: str       # 'hard' | 'soft'
    x: float
    y: float
    gap: float          # 평면 최소거리(m); 평면 교차면 0
    fz: float           # 소방 설치높이 중앙(m)
    mz: float           # MEP 높이 중앙(m)
    dz: float           # 수직 이격(m); z구간 겹치면 0


# ---------------------------------------------------------------------------
def to_mep_geoms(model, settings=None) -> list:
    """전기/기계 IFC 요소 → 2D shapely Polygon(미터) 리스트(z 태그 포함)."""
    settings = settings or L._settings()
    out = []
    seen = set()
    for t in MEP_TYPES:
        try:
            elems = model.by_type(t)
        except Exception:
            continue
        for e in elems:
            if e.id() in seen:
                continue
            seen.add(e.id())
            g, zmin, zmax = _xy_footprint_with_z(e, settings)
            if g is not None:
                out.append(MepGeom(geom=g, zmin=zmin, zmax=zmax, type=e.is_a(), ifc_id=e.id()))
    return out


# 전기/기계 DXF 레이어 키워드(실무 도면 한/영 혼용)
_MEP_DXF_KEYS = ("e-", "m-", "elec", "전기", "cable", "케이블", "트레이", "tray",
                 "conduit", "전선", "배선", "duct", "덕트", "기계", "위생", "배관")


def to_mep_geoms_dxf(path, ceiling_z=2.6, width=0.2,
                     z_top_off=0.05, z_bot_off=0.30):
    """전기/기계 **DXF** → 천장 z-밴드를 부여한 MepGeom 리스트.

    2D DXF 는 높이정보가 없으므로, 전기류 레이어 요소를 '천장 직하 트레이/배선'으로
    간주해 z-밴드 [ceiling_z - z_bot_off, ceiling_z - z_top_off] 를 부여한다
    (스프링클러·감지기 설치 z 와 겹쳐 평면 교차 시 충돌로 검출됨).
    선/폴리라인은 width 로 버퍼해 폭을 준다.
    """
    from shapely.geometry import LineString
    from . import dxf_loader as DL
    doc = DL._open_doc(path)             # DXF 직접 / DWG 는 ODA 변환
    msp = doc.modelspace()
    # 단위 스케일(전체 점 규모로 추정)
    pts_all = []
    for e in msp.query("LINE LWPOLYLINE"):
        try:
            if e.dxftype() == "LINE":
                pts_all += [(e.dxf.start[0], e.dxf.start[1]), (e.dxf.end[0], e.dxf.end[1])]
            else:
                pts_all += [(p[0], p[1]) for p in e.get_points("xy")]
        except Exception:
            continue
    scale = DL._detect_scale(doc, pts_all)
    zmin, zmax = ceiling_z - z_bot_off, ceiling_z - z_top_off
    w = max(width, 0.05)

    out, idc = [], 0
    for e in msp:
        lyr = getattr(e.dxf, "layer", "")
        if not DL._layer_is(lyr, _MEP_DXF_KEYS):
            continue
        t = e.dxftype()
        g = None
        try:
            if t == "LINE":
                g = LineString([(e.dxf.start[0] * scale, e.dxf.start[1] * scale),
                                (e.dxf.end[0] * scale, e.dxf.end[1] * scale)]).buffer(w / 2)
            elif t == "LWPOLYLINE":
                pts = [(p[0] * scale, p[1] * scale) for p in e.get_points("xy")]
                if len(pts) >= 2:
                    g = (Polygon(pts) if e.closed and len(pts) >= 3
                         else LineString(pts).buffer(w / 2))
            elif t == "CIRCLE":
                g = Point(e.dxf.center[0] * scale, e.dxf.center[1] * scale).buffer(
                    max(e.dxf.radius * scale, w / 2))
            elif t in ("INSERT", "ARC"):
                c = e.dxf.insert if t == "INSERT" else e.dxf.center
                g = Point(c[0] * scale, c[1] * scale).buffer(w)
        except Exception:
            g = None
        if g is None or g.is_empty or g.area < 1e-9:
            continue
        idc += 1
        out.append(MepGeom(geom=g, zmin=zmin, zmax=zmax, type=f"DXF:{lyr}", ifc_id=idc))
    return out


def _xy_footprint_with_z(element, settings):
    """요소 메쉬 → XY convex hull Polygon + (zmin, zmax). 월드좌표(미터)."""
    try:
        shape = ifcopenshell.geom.create_shape(settings, element)
    except Exception:
        return None, 0.0, 0.0
    import ifcopenshell.util.shape as US
    verts = US.get_vertices(shape.geometry)
    if verts is None or len(verts) < 1:
        return None, 0.0, 0.0
    zmin, zmax = float(verts[:, 2].min()), float(verts[:, 2].max())
    if len(verts) < 3:
        g = MultiPoint([tuple(p) for p in verts[:, :2]]).buffer(0.05)
    else:
        g = MultiPoint([tuple(p) for p in verts[:, :2]]).convex_hull
        if not isinstance(g, Polygon):
            g = g.buffer(0.05)
    if g.is_empty or g.area < 1e-9:
        g = g.buffer(0.05)
    return g, zmin, zmax


# ---------------------------------------------------------------------------
@dataclass
class FireGeom:
    geom: object        # buffered shapely (장치+이격)
    core: object        # 장치 실제 형상(작은 버퍼)
    zlo: float          # 설치 수직 범위(미터)
    zhi: float
    facility: str
    kind: str
    room: str
    x: float
    y: float

    @property
    def z(self):
        return (self.zlo + self.zhi) / 2.0


def fire_extent(facility: str, ceiling_z: float, floor_z: float = 0.0):
    """시설별 설치 수직 범위(미터). 천장(스프링클러/감지기) vs 벽(소화기/소화전)."""
    if facility == "sprinkler":          # 헤드 + 가지배관(천장 직하)
        return (ceiling_z - 0.45, ceiling_z + 0.05)
    if facility == "detector":           # 천장면 부착
        return (ceiling_z - 0.10, ceiling_z + 0.05)
    if facility == "extinguisher":       # 벽 1.5m
        return (floor_z + 1.2, floor_z + 1.6)
    if facility == "hydrant":            # 소화전함
        return (floor_z + 0.5, floor_z + 1.5)
    return (ceiling_z - 0.2, ceiling_z + 0.05)


def fire_geoms(layout, margin: float = 0.15, ceiling_z: float = 2.6,
               floor_z: float = 0.0, device_size: float = 0.1) -> list:
    """소방 배치 → 이격(margin) 버퍼 폴리곤 + 시설별 z구간."""
    out = []
    for fac, lst in layout.placements.items():
        if fac == "evacuation":
            continue  # 피난 출구는 클래시 대상 제외
        zlo, zhi = fire_extent(fac, ceiling_z, floor_z)
        for p in lst:
            pt = Point(p.point[0], p.point[1])
            out.append(FireGeom(geom=pt.buffer(device_size + margin),
                                core=pt.buffer(device_size),
                                zlo=zlo, zhi=zhi, facility=fac, kind=p.kind,
                                room=p.room, x=p.point[0], y=p.point[1]))
    return out


def _vgap(alo, ahi, blo, bhi):
    """두 z구간의 수직 이격. 겹치면 0."""
    if ahi >= blo and bhi >= alo:
        return 0.0
    return blo - ahi if blo > ahi else alo - bhi


def detect(fires: list, meps: list, clearance: float = 0.15) -> list:
    """소방 ↔ MEP 3D(2.5D) 충돌 감지.

    hard = 평면 교차 ∧ z구간 겹침 (실제 3D 간섭)
    soft = 평면 근접(이격 내) ∧ z 근접(이격 내)  — 시공 이격 미달
    z가 떨어져 있으면(이격 초과) 평면이 겹쳐도 충돌 아님.
    """
    if not fires or not meps:
        return []
    mep_geoms = [m.geom for m in meps]
    tree = STRtree(mep_geoms)
    clashes = []
    for f in fires:
        idxs = tree.query(f.geom, predicate="intersects")   # 광역질의: 평면 이격 버퍼
        for mi in np.atleast_1d(idxs):
            m = meps[int(mi)]
            dz = _vgap(f.zlo, f.zhi, m.zmin, m.zmax)
            z_overlap = dz <= 1e-9
            z_near = dz <= clearance
            xy_core = f.core.intersects(m.geom)
            if xy_core and z_overlap:
                sev, gap = "hard", 0.0
            elif (f.geom.intersects(m.geom) and z_near) or (xy_core and z_near):
                sev = "soft"
                gap = 0.0 if xy_core else round(float(f.core.distance(m.geom)), 3)
            else:
                continue                                     # 높이 떨어짐 → 충돌 아님
            clashes.append(Clash(
                fire_facility=f.facility, fire_kind=f.kind, fire_room=f.room,
                mep_type=m.type, mep_id=m.ifc_id, severity=sev,
                x=round(f.x, 3), y=round(f.y, 3), gap=gap,
                fz=round(f.z, 2), mz=round(m.z, 2), dz=round(dz, 3)))
    return clashes


# ---------------------------------------------------------------------------
def export_csv(clashes: list, path: str):
    with open(path, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["fire_facility", "fire_kind", "fire_room", "mep_type",
                    "mep_id", "severity", "x", "y", "gap_m",
                    "fire_z", "mep_z", "vgap_z_m"])
        for c in clashes:
            w.writerow([c.fire_facility, c.fire_kind, c.fire_room, c.mep_type,
                        c.mep_id, c.severity, c.x, c.y, c.gap, c.fz, c.mz, c.dz])


def export_geojson(clashes: list, path: str):
    feats = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [c.x, c.y]},
        "properties": asdict(c),
    } for c in clashes]
    with open(path, "w", encoding="utf-8") as fp:
        json.dump({"type": "FeatureCollection", "features": feats}, fp,
                  ensure_ascii=False, indent=1)


def summarize(clashes: list) -> dict:
    hard = sum(1 for c in clashes if c.severity == "hard")
    return {"total": len(clashes), "hard": hard, "soft": len(clashes) - hard}
