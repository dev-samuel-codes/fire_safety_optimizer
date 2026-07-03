# -*- coding: utf-8 -*-
"""
overlay — 다공종 오버레이 '장면(scene)' 생성/캐시.

건축·소방(시설/배관)·전기·기계를 한 좌표계의 좌표 리스트로 직렬화해 JSON 으로 저장.
웹 뷰어가 이 JSON 으로 SVG 레이어를 그려 체크박스 토글(선택 표시)을 지원한다.
MEP 메싱이 무거우므로(기계 ~50s) 한 번 계산해 캐시하는 것이 핵심.
"""
from __future__ import annotations

import os
import json
import warnings

from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union

from . import ifc_loader as L
from . import placement as P
from . import clash as X
from . import routing as RT


def _ring(geom):
    if isinstance(geom, Polygon):
        return [[round(x, 3), round(y, 3)] for x, y in geom.exterior.coords]
    if isinstance(geom, MultiPolygon):
        big = max(geom.geoms, key=lambda g: g.area)
        return [[round(x, 3), round(y, 3)] for x, y in big.exterior.coords]
    return []


# 알려진 공종 정의 (key, 라벨, 채움색, 외곽색)
DISCIPLINE_DEFS = [
    ("electrical",   "전기 (MEP)", "#54a0ff", "#1e6fd6"),
    ("mechanical",   "기계/HVAC (MEP)", "#ff9f43", "#e08e00"),
    ("mechanical_m", "기계설비 (M모델·배관/덕트)", "#a55eea", "#8854d0"),
    ("plumbing",     "위생/배관", "#1dd1a1", "#0e9c79"),
]
# 임의 업로드 공종용 색상 팔레트
_PALETTE = [("#54a0ff", "#1e6fd6"), ("#ff9f43", "#e08e00"), ("#1dd1a1", "#0e9c79"),
            ("#a55eea", "#8854d0"), ("#ff6b81", "#c0392b"), ("#feca57", "#e0a800")]


def classify_discipline(filename: str):
    """파일명으로 공종 추정 → (key, label). 모르면 (sanitized, 원본명)."""
    f = filename.lower()
    if any(k in f for k in ("elec", "전기", "_e_", "power")):
        return "electrical", "전기 (MEP)"
    if any(k in f for k in ("mep", "mech", "hvac", "기계", "_m_", "duct")):
        return "mechanical", "기계/HVAC"
    if any(k in f for k in ("plumb", "위생", "sanit", "_p_", "pipe")):
        return "plumbing", "위생/배관"
    key = "".join(c if c.isalnum() else "_" for c in os.path.splitext(filename)[0])[:24] or "mep"
    return key, os.path.basename(filename)


def disc_list_from_paths(disc_paths: dict):
    """{key: path} → build_scene 용 disc_list. 알려진 key 는 정의색, 그 외 팔레트."""
    defs = {k: (l, f, s) for k, l, f, s in DISCIPLINE_DEFS}
    out = []
    for k, p in disc_paths.items():
        if k in defs:
            l, f, s = defs[k]
        else:
            f, s = _PALETTE[len(out) % len(_PALETTE)]
            l = k
        out.append({"key": k, "label": l, "fill": f, "stroke": s, "path": p})
    return out


def disc_list_from_files(file_paths: list):
    """업로드 파일 경로 리스트 → disc_list(파일명으로 공종 추정, 중복 key 구분)."""
    out, seen = [], {}
    for p in file_paths:
        key, label = classify_discipline(os.path.basename(p))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = f"{key}{seen[key]}"; label = f"{label} #{seen[key]}"
        f, s = _PALETTE[len(out) % len(_PALETTE)]
        out.append({"key": key, "label": label, "fill": f, "stroke": s, "path": p})
    return out


def build_scene(arch_path, disc_list=None, disc_paths=None, structure="fireproof",
                occupancy="common", floors=2, elevation=None, ceiling_z=2.6,
                z_band=1.6, margin=0.15):
    """건축+소방(1회) + 공종별 도형·충돌을 각각 계산해 장면 생성.

    disc_list: [{"key","label","fill","stroke","path"}, ...] (우선)
    disc_paths: {key: path} (disc_list 없을 때 DISCIPLINE_DEFS 매핑)
    elevation None 이면 룸 최다 층 자동 선택.
    """
    if disc_list is None:
        disc_list = disc_list_from_paths(disc_paths or {})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        arch = L.load(arch_path, structure=structure, occupancy=occupancy)
        if elevation is None:
            elevation = L.auto_storey_elevation(arch.rooms)
        rooms = L.rooms_on_storey(arch.rooms, elevation, 1.5) or arch.rooms
        region = unary_union([r.polygon for r in rooms]) if rooms else None

        pcfg = P.PlaceConfig(structure=structure, occupancy=occupancy, num_floors=floors)
        layout = P.build_layout(rooms, pcfg, doors=arch.door_points)
        ceiling_abs = elevation + ceiling_z
        fires = X.fire_geoms(layout, margin=margin, ceiling_z=ceiling_abs, floor_z=elevation)

        z_lo, z_hi = elevation - 1.0, elevation + ceiling_z + 1.5
        disciplines = {}
        for spec in disc_list:
            key, label, fill, stroke, path = (spec["key"], spec["label"],
                                              spec["fill"], spec["stroke"], spec["path"])
            if not path or not os.path.exists(path):
                continue
            m, _ = L.open_model(path)
            band = [g for g in X.to_mep_geoms(m) if z_lo <= g.z <= z_hi]
            rings = [_ring(g.geom) for g in band]
            rings = [r for r in rings if len(r) >= 3]
            clashes = X.detect(fires, band, clearance=margin)
            disciplines[key] = {
                "label": label, "fill": fill, "stroke": stroke,
                "geoms": rings,
                "clashes": [[c.x, c.y, c.severity] for c in clashes],
                "geom_count": len(rings), "clash_count": len(clashes),
            }

        sp_pts = [p.point for p in layout.placements.get("sprinkler", [])]
        segs, riser, _ = RT.route_orthogonal(sp_pts)

        b = region.bounds if region is not None else (0, 0, 1, 1)
        fire = {fac: [[round(p.point[0], 3), round(p.point[1], 3), p.kind] for p in lst]
                for fac, lst in layout.placements.items()}
        return {
            "bounds": [round(v, 3) for v in b],
            "rooms": [_ring(r.polygon) for r in rooms],
            "walls": [_ring(w) for w in arch.wall_polys
                      if isinstance(w, (Polygon, MultiPolygon))],
            "doors": [[round(d.x, 3), round(d.y, 3)] for d in arch.door_points
                      if hasattr(d, "x")],
            "room_labels": [{"name": r.name, "area": round(r.area, 1),
                             "x": round(r.polygon.centroid.x, 3),
                             "y": round(r.polygon.centroid.y, 3)} for r in rooms],
            "fire": fire,
            "pipes": [{"p1": [round(s["p1"][0], 3), round(s["p1"][1], 3)],
                       "p2": [round(s["p2"][0], 3), round(s["p2"][1], 3)],
                       "dia": s["dia"], "kind": s["kind"]} for s in segs],
            "riser": [round(riser[0], 3), round(riser[1], 3)] if riser else None,
            "disciplines": disciplines,
            "counts": {f"fire_{k}": len(v) for k, v in fire.items()},
        }


def build_and_save(out_path, **kw):
    scene = build_scene(**kw)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(scene, fp, ensure_ascii=False)
    return scene


# ---------------------------------------------------------------------------
# 파이프라인(IFC/DXF 공통) 결과 → 뷰어 장면. 이미 계산된 데이터를 직렬화만 한다.
# ---------------------------------------------------------------------------
def _classify_mep_type(t):
    """MEP 요소 타입(IFC is_a 또는 'DXF:레이어') → (공종key, 라벨, 채움, 외곽)."""
    s = (t or "").lower()
    if any(k in s for k in ("cable", "light", "outlet", "elec", "전기", "트레이", "tray", "e-")):
        return "electrical", "전기 (MEP)", "#54a0ff", "#1e6fd6"
    if any(k in s for k in ("duct", "air", "hvac", "기계", "fan", "m-")):
        return "mechanical", "기계/HVAC", "#ff9f43", "#e08e00"
    if any(k in s for k in ("sanit", "plumb", "위생")):
        return "plumbing", "위생/배관", "#1dd1a1", "#0e9c79"
    return "mep", "기타 MEP", "#a55eea", "#8854d0"


def scene_from_pipeline(out_path, rooms, walls, doors, layout, meps, region,
                        margin=0.15, ceiling_z=2.6, floor_z=0.0, state="before"):
    """파이프라인이 이미 구한 데이터(룸/벽/문/배치/MEP)로 뷰어 장면 생성·저장.

    IFC·DXF 어느 입력이든 동작(클래시 재계산만 공종별로). state 는 라벨용('before'=간섭검토).
    """
    fires = X.fire_geoms(layout, margin=margin, ceiling_z=ceiling_z, floor_z=floor_z)
    # 다층 입력 대비: 해당 층 z-밴드와 겹치는 MEP만(다른 층 MEP 표시·충돌 제외)
    meps = [m for m in (meps or []) if floor_z - 1.0 <= m.z <= ceiling_z + 1.0]
    groups = {}
    for m in meps:
        key, label, fill, stroke = _classify_mep_type(getattr(m, "type", ""))
        g = groups.setdefault(key, {"label": label, "fill": fill, "stroke": stroke, "meps": []})
        g["meps"].append(m)
    disciplines = {}
    for key, g in groups.items():
        rings = [_ring(m.geom) for m in g["meps"]]
        rings = [r for r in rings if len(r) >= 3]
        clashes = X.detect(fires, g["meps"], clearance=margin)
        disciplines[key] = {
            "label": g["label"], "fill": g["fill"], "stroke": g["stroke"],
            "geoms": rings, "clashes": [[round(c.x, 3), round(c.y, 3), c.severity] for c in clashes],
            "geom_count": len(rings), "clash_count": len(clashes)}

    sp_pts = [p.point for p in layout.placements.get("sprinkler", [])]
    segs, riser, _ = RT.route_orthogonal(sp_pts)
    b = region.bounds if region is not None else (0, 0, 1, 1)
    fire = {fac: [[round(p.point[0], 3), round(p.point[1], 3), p.kind] for p in lst]
            for fac, lst in layout.placements.items()}
    room_labels = [{"name": r.name, "area": round(r.area, 1),
                    "x": round(r.polygon.centroid.x, 3), "y": round(r.polygon.centroid.y, 3)}
                   for r in rooms]
    scene = {
        "state": state,
        "bounds": [round(v, 3) for v in b],
        "rooms": [_ring(r.polygon) for r in rooms],
        "walls": [_ring(w) for w in (walls or []) if isinstance(w, (Polygon, MultiPolygon))],
        "doors": [[round(d.x, 3), round(d.y, 3)] for d in (doors or []) if hasattr(d, "x")],
        "room_labels": room_labels,
        "fire": fire,
        "pipes": [{"p1": [round(s["p1"][0], 3), round(s["p1"][1], 3)],
                   "p2": [round(s["p2"][0], 3), round(s["p2"][1], 3)],
                   "dia": s["dia"], "kind": s["kind"]} for s in segs],
        "riser": [round(riser[0], 3), round(riser[1], 3)] if riser else None,
        "disciplines": disciplines,
        "counts": {f"fire_{k}": len(v) for k, v in fire.items()},
    }
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(scene, fp, ensure_ascii=False)
    return scene
