# -*- coding: utf-8 -*-
"""
dxf_loader — 실무 AutoCAD 도면(DXF)을 입력으로 받아 룸/벽/문 추출.

실무 현실: 중소 소방설계사는 IFC(BIM)가 아니라 **AutoCAD DWG/DXF** 평면도로 작업한다.
  · DWG 는 ezdxf 가 직접 못 읽으므로 'DXF 로 다른 이름 저장'(AutoCAD 기본) 또는
    ODA File Converter 로 변환해 입력한다(README 안내).
  · DXF 는 본 모듈이 직접 읽어 ifc_loader 와 동일한 BuildingModel 로 변환 →
    이후 배치/충돌/최적화/도면출력 파이프라인을 그대로 태운다.

추출 규약(실무 도면 관례에 맞춘 휴리스틱)
  룸  : '닫힌 폴리라인'(LWPOLYLINE/POLYLINE, closed). 실구획/ROOM/SPACE/AREA 류 레이어 우선,
        없으면 면적이 정상범위인 모든 닫힌 폴리라인. 실명은 폴리곤 내부 TEXT/MTEXT 매칭.
  벽  : WALL/벽 류 레이어의 LINE/폴리라인(배경 표시용; 닫혔으면 폴리곤, 아니면 가는 버퍼).
  문  : DOOR/문 류 레이어의 INSERT/ARC 삽입점.
  단위: $INSUNITS 헤더(mm/cm/m/inch/feet) → 미터 환산. 미지정 시 좌표 규모로 추정.
"""
from __future__ import annotations

import warnings

import numpy as np
import ezdxf
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union

from .ifc_loader import Room, BuildingModel   # 동일 자료구조 재사용


# $INSUNITS 코드 → 미터 환산계수
_INSUNITS_SCALE = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 8: 2.54e-5}

# 레이어 이름 분류 키워드(대소문자·한/영 혼용 도면 대응)
_ROOM_KEYS = ("실구획", "실", "room", "space", "area", "면적", "거실", "zone")
_WALL_KEYS = ("벽", "wall", "a-wall", "a-벽", "구조", "외벽", "내벽")
_DOOR_KEYS = ("문", "door", "a-door", "창호")
_TEXT_TYPES = ("TEXT", "MTEXT")


def _layer_is(name: str, keys) -> bool:
    n = (name or "").lower()
    return any(k in n for k in keys)


def _open_doc(path: str):
    """DXF 직접 / DWG 는 ODA File Converter(설치 시) 자동 변환해 읽기."""
    if path.lower().endswith(".dwg"):
        try:
            from ezdxf.addons import odafc
        except Exception as e:
            raise RuntimeError("DWG 입력엔 ezdxf odafc 애드온이 필요합니다.") from e
        if not odafc.is_installed():
            raise RuntimeError(
                "DWG 입력엔 ODA File Converter(무료) 설치가 필요합니다. "
                "또는 AutoCAD에서 '다른 이름 저장 → DXF' 후 입력하세요.")
        return odafc.readfile(path)
    return ezdxf.readfile(path)


def _detect_scale(doc, all_pts) -> float:
    """모델단위 → 미터 환산계수. $INSUNITS 우선, 없으면 좌표규모로 추정."""
    code = int(doc.header.get("$INSUNITS", 0))
    if code in _INSUNITS_SCALE:
        return _INSUNITS_SCALE[code]
    # 미지정: 전체 bbox 최대변으로 추정(건물 도면은 보통 수~수십 m)
    if len(all_pts):
        arr = np.asarray(all_pts, float)
        span = float(max(arr[:, 0].ptp(), arr[:, 1].ptp()))
        if span > 2000:        # 수천 단위 → mm 추정
            return 0.001
        if span > 200:         # 수백 단위 → cm 추정
            return 0.01
    return 1.0                  # 그대로 미터로 간주


def _closed_polylines(msp):
    """닫힌 LWPOLYLINE/POLYLINE → [(layer, [(x,y),...]), ...] (원시단위)."""
    out = []
    for e in msp.query("LWPOLYLINE"):
        if e.closed:
            pts = [(p[0], p[1]) for p in e.get_points("xy")]
            if len(pts) >= 3:
                out.append((e.dxf.layer, pts))
    for e in msp.query("POLYLINE"):
        try:
            if e.is_closed:
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                if len(pts) >= 3:
                    out.append((e.dxf.layer, pts))
        except Exception:
            continue
    return out


def _texts(msp):
    """TEXT/MTEXT → [(insert_xy, string), ...] (원시단위)."""
    out = []
    for e in msp:
        if e.dxftype() not in _TEXT_TYPES:
            continue
        try:
            ins = e.dxf.insert
            s = e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text
            out.append(((ins[0], ins[1]), (s or "").strip()))
        except Exception:
            continue
    return out


def _name_for(poly: Polygon, texts, scale) -> str:
    """폴리곤 내부(또는 최근접) 텍스트를 실명으로. 면적 표기(㎡/m2)는 제외."""
    best, bestd = "", 1e18
    for (tx, ty), s in texts:
        if not s or "㎡" in s or "m2" in s.lower():
            continue
        p = Point(tx * scale, ty * scale)
        if poly.contains(p):
            return s
        d = poly.distance(p)
        if d < bestd:
            best, bestd = s, d
    # 너무 멀면 무명
    return best if bestd <= max(poly.length * 0.15, 1.0) else ""


def load(path: str, structure: str = "fireproof", occupancy: str = "common") -> BuildingModel:
    """건축 DXF/DWG → BuildingModel(rooms/walls/doors, 미터). ifc_loader.load 와 호환."""
    doc = _open_doc(path)
    msp = doc.modelspace()

    polylines = _closed_polylines(msp)
    texts = _texts(msp)

    # 단위 추정: 모든 폴리라인 점으로 규모 파악
    all_pts = [p for _, pts in polylines for p in pts]
    scale = _detect_scale(doc, all_pts)

    # --- 룸 후보: 실구획류 레이어 우선, 없으면 면적 정상범위 닫힌 폴리라인 ---
    room_layer_hits = [(lyr, pts) for lyr, pts in polylines if _layer_is(lyr, _ROOM_KEYS)]
    candidates = room_layer_hits or polylines

    rooms = []
    for lyr, pts in candidates:
        poly = Polygon([(x * scale, y * scale) for x, y in pts])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if not isinstance(poly, Polygon) or poly.area < 1.0 or poly.area > 1e5:
            continue
        name = _name_for(poly, texts, scale) or f"실-{len(rooms)+1}"
        rooms.append(Room(name=name, polygon=poly, area=poly.area, storey="1F",
                          elevation=0.0, structure=structure, occupancy=occupancy,
                          area_source="dxf_polyline"))

    # 룸이 서로 포함관계면(외곽+내부 중복) 큰 외곽 제거: 다른 룸을 2개 이상 포함하면 컨테이너로 간주
    rooms = _drop_containers(rooms)

    # --- 벽: 벽류 레이어 폴리라인(닫힘=폴리곤 / 열림=가는 버퍼) ---
    walls = []
    for e in msp.query("LWPOLYLINE"):
        if not _layer_is(e.dxf.layer, _WALL_KEYS):
            continue
        pts = [(p[0] * scale, p[1] * scale) for p in e.get_points("xy")]
        if len(pts) < 2:
            continue
        if e.closed and len(pts) >= 3:
            wp = Polygon(pts)
            if wp.is_valid and wp.area > 1e-6:
                walls.append(wp)
        else:
            from shapely.geometry import LineString
            walls.append(LineString(pts).buffer(0.05))
    # 벽 레이어가 없으면 룸 외곽 합집합 경계를 배경으로
    if not walls and rooms:
        walls = [r.polygon for r in rooms]

    # --- 문: 문류 레이어 INSERT/ARC 삽입점 ---
    doors = []
    for e in msp:
        if e.dxftype() in ("INSERT", "ARC") and _layer_is(e.dxf.layer, _DOOR_KEYS):
            try:
                c = e.dxf.insert if e.dxftype() == "INSERT" else e.dxf.center
                doors.append(Point(c[0] * scale, c[1] * scale))
            except Exception:
                continue

    if not rooms:
        warnings.warn("DXF 에서 닫힌 폴리라인(룸)을 찾지 못함 — 실구획 레이어/폴리곤 확인 필요",
                      RuntimeWarning)

    bm = BuildingModel(path=path, schema=f"DXF/{doc.dxfversion}", scale=scale,
                       storeys=[("1F", 0.0)], rooms=rooms,
                       wall_polys=walls, door_points=doors)
    return bm


def _drop_containers(rooms: list) -> list:
    """다른 룸을 2개 이상 포함하는 큰 폴리곤(건물 외곽선 등)은 룸에서 제외."""
    if len(rooms) <= 1:
        return rooms
    keep = []
    for i, r in enumerate(rooms):
        contained = sum(1 for j, o in enumerate(rooms)
                        if i != j and r.polygon.contains(o.polygon.representative_point()))
        if contained >= 2:
            continue
        keep.append(r)
    return keep or rooms


# auto_storey_elevation / rooms_on_storey 호환: DXF 는 단층이므로 그대로 통과시키는 헬퍼
def auto_storey_elevation(rooms: list, tol: float = 1.5) -> float:
    return 0.0
