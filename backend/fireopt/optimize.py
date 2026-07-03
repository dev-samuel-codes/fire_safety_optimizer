# -*- coding: utf-8 -*-
"""
optimize — 클래시 해소 재배치.

전략: 충돌한 소방 장치를 룸 내부에서 최소변위로 nudge(나선 탐색)해 MEP 를 회피.
nudge 가 실패하면 해당 룸의 시설을 클래시 영역을 피해 set-cover 로 재시드(recover).
이동 후에도 스프링클러 피복이 깨지지 않도록 룸별로 재검증·top-up 한다.
"""
from __future__ import annotations

import copy
import math

import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

from . import geometry as G
from . import clash as X
from .placement import Layout, Placement


DEVICE_SIZE = 0.1


def _facility_mep_tree(meps, facility, ceiling_z, floor_z):
    """해당 시설의 설치 z구간과 수직으로 겹치는 MEP만 추려 STRtree 구성."""
    lo, hi = X.fire_extent(facility, ceiling_z, floor_z)
    sub = [m for m in meps if not (m.zmax < lo or m.zmin > hi)]
    geoms = [m.geom for m in sub]
    return (STRtree(geoms) if geoms else None, geoms)


def _clear(point, tree, geoms, clearance) -> bool:
    """point 의 장치형상(+clearance)이 어떤 MEP 와도 안 겹치면 True."""
    if tree is None:
        return True
    probe = Point(point).buffer(DEVICE_SIZE + clearance)
    idxs = tree.query(probe, predicate="intersects")
    return len(np.atleast_1d(idxs)) == 0


def nudge(point, poly, tree, geoms, clearance=0.15, max_r=2.5, step=0.12):
    """룸(poly) 내부에서 최소변위로 MEP 를 회피하는 새 위치. 실패 시 None."""
    for dx, dy in G.spiral_offsets(step, max_r):
        cand = (point[0] + dx, point[1] + dy)
        if poly is not None and not poly.contains(Point(cand)):
            continue
        if _clear(cand, tree, geoms, clearance):
            return cand
    return None


def resolve(layout: Layout, meps, rooms, margin=0.15, ceiling_z=2.6, floor_z=0.0,
            cfg=None):
    """충돌 장치 재배치 → (after_layout, residual_clashes, total_displacement_m).

    시설별로 '수직으로 겹치는 MEP'만 회피 대상으로 본다(높이가 다르면 충돌 아님).
    """
    region = unary_union([r.polygon for r in rooms]) if rooms else None
    facs = [f for f in layout.placements if f != "evacuation"]
    ftree = {f: _facility_mep_tree(meps, f, ceiling_z, floor_z) for f in facs}

    new_placements = copy.deepcopy(layout.placements)
    total_disp = 0.0
    moved = 0
    for fac, lst in new_placements.items():
        if fac == "evacuation":
            continue
        tree, geoms = ftree.get(fac, (None, []))
        if tree is None:
            continue
        for p in lst:
            if _clear(p.point, tree, geoms, 0.0):   # 현재 hard 충돌 없으면 패스
                continue
            poly = _containing_poly(p.point, rooms, region)
            newpos = (nudge(p.point, poly, tree, geoms, clearance=margin)
                      or nudge(p.point, poly, tree, geoms, clearance=0.0))
            if newpos is not None:
                total_disp += math.hypot(newpos[0] - p.point[0], newpos[1] - p.point[1])
                p.attrs = dict(p.attrs); p.attrs["moved_from"] = p.point
                p.point = newpos
                moved += 1

    after = Layout(placements=new_placements, checks=dict(layout.checks),
                   free_graph=layout.free_graph)

    # 스프링클러 피복 재검증 + top-up (스프링클러와 수직으로 겹치는 MEP만 회피)
    str_tree, str_geoms = ftree.get("sprinkler", (None, []))
    _repair_sprinkler_coverage(after, rooms, str_tree, str_geoms, margin)

    residual = X.detect(X.fire_geoms(after, margin=margin, ceiling_z=ceiling_z, floor_z=floor_z),
                        meps, clearance=margin)
    after.checks["reopt"] = {"moved": moved, "total_displacement_m": round(total_disp, 3)}
    return after, residual, total_disp


def _repair_sprinkler_coverage(layout: Layout, rooms, tree, geoms, margin):
    """이동 후 룸별 스프링클러 피복 공백을 MEP 회피 위치로 top-up."""
    by_room = {}
    for p in layout.placements.get("sprinkler", []):
        by_room.setdefault(p.room, []).append(p)
    for r in rooms:
        heads = by_room.get(r.name, [])
        if not heads:
            continue
        R = heads[0].attrs.get("R", 2.3)
        pts = np.array([h.point for h in heads])
        demand = G.coverage_demand(r.polygon, step=max(min(R * 0.7, 0.6), 0.4),
                                   inset=min(0.3, _short(r.polygon) / 4))
        if len(demand) == 0:
            continue
        uncovered = demand[~G.coverage_mask(demand, pts, R)]
        while len(uncovered):
            cand = tuple(uncovered[0])
            # MEP 회피 위치로 살짝 이동
            if not _clear(cand, tree, geoms, 0.0):
                nz = nudge(cand, r.polygon, tree, geoms, clearance=0.0, max_r=0.8)
                if nz:
                    cand = nz
            layout.placements["sprinkler"].append(
                Placement("sprinkler", "head", cand, r.name, {"R": R, "added": "repair"}))
            pts = np.vstack([pts, np.array([cand])])
            uncovered = uncovered[~G.coverage_mask(uncovered, np.array([cand]), R)]


def _containing_poly(point, rooms, region):
    """점을 포함하는(또는 가장 가까운) 룸 폴리곤. 이름 충돌과 무관하게 동작."""
    pt = Point(point)
    best, bestd = None, 1e18
    for r in rooms:
        if r.polygon.contains(pt):
            return r.polygon
        d = r.polygon.distance(pt)
        if d < bestd:
            best, bestd = r.polygon, d
    if best is not None and bestd <= 0.75:
        return best
    return region


def _short(poly):
    minx, miny, maxx, maxy = poly.bounds
    return max(min(maxx - minx, maxy - miny), 1e-3)
