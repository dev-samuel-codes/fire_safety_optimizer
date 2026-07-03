# -*- coding: utf-8 -*-
"""
room_extract — 룸 레이어가 없는 **실 도면에서 벽선으로 실(室) 재구성** (Phase 6 보강).

실도면 교훈(산업단지 창고 DWG): 깨끗한 'A-실구획' 룸 레이어가 없으면 dxf_loader 의
'모든 닫힌 폴리라인=룸' 폴백이 가구·기둥·외곽을 룸으로 오검출(129개 쓰레기). 대안:
  벽 레이어의 선분 → shapely.polygonize 로 **닫힌 면(face)** 생성 → 면적·종횡비로
  벽 사이 슬리버 제거 → 실 폴리곤. (이중선 벽의 빈 공간 슬리버를 걸러내는 게 핵심)

완벽하진 않다(이중벽·열린 구획·문 개구부로 면이 합쳐질 수 있음). dxf_loader 룸이
부실할 때의 보강책이며, 결과는 실측으로 검증해야 한다.
"""
from __future__ import annotations

from shapely.geometry import LineString, Polygon, Point
from shapely.ops import polygonize, unary_union


_WALL_KEYS = ("벽", "wall", "wal", "구조", "옹벽")


def _layer_is(name, keys):
    n = (name or "").lower()
    return any(k in n for k in keys)


def _wall_segments(msp, scale, wall_keys):
    segs = []
    for e in msp.query("LINE"):
        if not _layer_is(e.dxf.layer, wall_keys):
            continue
        a, b = e.dxf.start, e.dxf.end
        segs.append(LineString([(a[0] * scale, a[1] * scale), (b[0] * scale, b[1] * scale)]))
    for e in msp.query("LWPOLYLINE"):
        if not _layer_is(e.dxf.layer, wall_keys):
            continue
        pts = [(p[0] * scale, p[1] * scale) for p in e.get_points("xy")]
        if len(pts) >= 2:
            if e.closed:
                pts.append(pts[0])
            segs.append(LineString(pts))
    return segs


def _aspect_ok(poly, max_aspect=8.0):
    """벽 사이 슬리버(가늘고 긴 면) 제거 — bbox 종횡비."""
    minx, miny, maxx, maxy = poly.bounds
    w, h = maxx - minx, maxy - miny
    if w < 1e-6 or h < 1e-6:
        return False
    return max(w / h, h / w) <= max_aspect


def extract_rooms(doc, scale, wall_keys=_WALL_KEYS, min_area=4.0, max_area=3000.0,
                  max_aspect=8.0):
    """벽선 → 실 폴리곤 리스트. (이름매칭 없이 기하만; 호출측에서 텍스트 매칭.)"""
    msp = doc.modelspace()
    segs = _wall_segments(msp, scale, wall_keys)
    if not segs:
        return []
    merged = unary_union(segs)                 # 교차점에서 분할 → polygonize 입력
    faces = []
    for f in polygonize(merged):
        if not isinstance(f, Polygon) or f.is_empty:
            continue
        if min_area <= f.area <= max_area and _aspect_ok(f, max_aspect):
            faces.append(f)
    # 큰 면이 작은 면을 다수 포함하면(복도가 실들을 감싸는 등) 컨테이너로 보고 제거
    return _drop_containers(faces)


def _drop_containers(faces, contain_thresh=2):
    if len(faces) <= 1:
        return faces
    keep = []
    for i, f in enumerate(faces):
        contained = 0
        for j, g in enumerate(faces):
            if i != j and f.contains(g.representative_point()):
                contained += 1
                if contained >= contain_thresh:
                    break
        if contained < contain_thresh:
            keep.append(f)
    return keep


def stats(faces):
    if not faces:
        return {"n": 0}
    ar = sorted(f.area for f in faces)
    return {"n": len(faces), "area_min": round(ar[0], 1),
            "area_med": round(ar[len(ar) // 2], 1), "area_max": round(ar[-1], 1),
            "total_area": round(sum(ar), 1)}
