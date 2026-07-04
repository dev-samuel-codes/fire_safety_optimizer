# -*- coding: utf-8 -*-
"""
room_extract_raster — 실 도면(문 개구부·이중벽)에서 **raster flood-fill**로 방 면적 추출.

vector polygonize(room_extract.py)는 문 개구부(열린 루프)를 면으로 못 닫아 실도면서 실패
(어린이집 도면: 방라벨 0/14 배정). 대안:
  벽선을 이미지로 래스터화 → morphological **closing으로 문틈 봉합** → 연결성분 분할 →
  방 라벨 시드가 속한 성분의 픽셀수 → 면적.

신뢰도(핵심): **closing 강도에 대한 면적 안정성.** 깨끗이 닫힌 방은 여러 closing 레벨에서
면적이 일정(고신뢰), 문틈으로 새거나 집기로 쪼개진 방은 레벨마다 널뛴다(저신뢰).
외부(가장 큰 성분)와 병합됐거나 면적이 비현실적이면 신뢰 0. 신뢰 방만 판정에 투입한다.

의존: numpy, scipy, Pillow (backend requirements에 포함 필요).
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage


def _seed_component(lab, cx, cy, search=16):
    """시드 픽셀의 성분 라벨. 벽 위(0)면 주변을 나선 탐색."""
    H, W = lab.shape
    if 0 <= cy < H and 0 <= cx < W and lab[cy, cx] > 0:
        return lab[cy, cx]
    for r in range(1, search + 1):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                yy, xx = cy + dy, cx + dx
                if 0 <= yy < H and 0 <= xx < W and lab[yy, xx] > 0:
                    return lab[yy, xx]
    return 0


def _rasterize(segments, x0, y1, mm_per_px, W, H):
    img = Image.new("1", (W, H), 0)
    dr = ImageDraw.Draw(img)
    for ax, ay, bx, by in segments:
        dr.line([((ax - x0) / mm_per_px, (y1 - ay) / mm_per_px),
                 ((bx - x0) / mm_per_px, (y1 - by) / mm_per_px)], fill=1, width=1)
    return np.array(img, dtype=bool)


def _areas_at(walls, seeds, mm_per_px, iters):
    """closing iters 회 후 각 시드의 방 면적(㎡)과 외부병합 여부."""
    closed = ndimage.binary_closing(walls, structure=np.ones((3, 3)), iterations=iters)
    lab, n = ndimage.label(~closed)
    if n == 0:
        return [0.0] * len(seeds), [True] * len(seeds)
    sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
    bg = 1 + int(np.argmax(sizes))          # 가장 큰 성분 = 외부/배경
    px_area = mm_per_px * mm_per_px / 1e6    # px → ㎡
    areas, merged = [], []
    for cx, cy in seeds:
        c = _seed_component(lab, cx, cy)
        areas.append(sizes[c - 1] * px_area if c > 0 else 0.0)
        merged.append(c == 0 or c == bg)
    return areas, merged


def extract_rooms_raster(wall_segments, room_labels, mm_per_px=25.0,
                         close_levels=(10, 14, 18), min_area=3.0, max_area=400.0):
    """
    wall_segments: [(ax,ay,bx,by), ...] 벽선 (도면 단위, mm 가정).
    room_labels:   [(name, (x,y)), ...] 방 이름 텍스트와 삽입점 (도면 단위).
    반환: [{name, area_m2, confidence(0~1), reliable(bool), merged(bool)}, ...]
      - confidence = closing 레벨 간 면적 안정성(1 - 변동/중앙값), 게이트 미통과 시 0.
      - reliable = confidence >= 0.6 (판정 투입 대상).
    """
    if not wall_segments or not room_labels:
        return [{"name": n, "area_m2": 0.0, "confidence": 0.0,
                 "reliable": False, "merged": True} for n, _ in room_labels]
    xs = [s[0] for s in wall_segments] + [s[2] for s in wall_segments]
    ys = [s[1] for s in wall_segments] + [s[3] for s in wall_segments]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    W = int((x1 - x0) / mm_per_px) + 2
    H = int((y1 - y0) / mm_per_px) + 2
    # 원점이탈/대형 도면 → 비트맵 폭주(OOM·CPU) 방지: 상한 초과 시 판정 보류(전부 needs_review).
    if W < 2 or H < 2 or W > 20000 or H > 20000 or W * H > 60_000_000:
        return [{"name": n, "area_m2": 0.0, "confidence": 0.0,
                 "reliable": False, "merged": True} for n, _ in room_labels]
    walls = _rasterize(wall_segments, x0, y1, mm_per_px, W, H)
    seeds = [(int((x - x0) / mm_per_px), int((y1 - y) / mm_per_px)) for _, (x, y) in room_labels]

    per_level = [_areas_at(walls, seeds, mm_per_px, it) for it in close_levels]
    out = []
    for i, (name, _) in enumerate(room_labels):
        areas = [per_level[k][0][i] for k in range(len(close_levels))]
        merged = any(per_level[k][1][i] for k in range(len(close_levels)))
        med = sorted(areas)[len(areas) // 2]
        if merged or not (min_area <= med <= max_area):
            conf = 0.0
        else:
            spread = (max(areas) - min(areas)) / med if med > 0 else 1.0
            conf = max(0.0, round(1.0 - spread, 2))
        out.append({"name": name, "area_m2": round(med, 1), "confidence": conf,
                    "reliable": conf >= 0.6, "merged": merged})
    return out


_ROOM_KW = ("보육", "유희", "놀이", "조리", "교사", "사무", "원장", "화장", "복도", "계단",
            "현관", "회의", "강의", "다목적", "세탁", "기계실", "창고", "샤워", "주방", "실습",
            "미용", "준비", "훈련", "로비", "숙소", "침실", "객실", "병실", "입원", "교재",
            "휴게", "식당", "수술", "교실", "거실", "교무", "보건", "관리")
# 가구·집기·도면주기(방 아님) — 이게 없으면 수납장/진열장/서랍장/강의대/벽체도장/전개도 등이 방으로 오검출.
_NON_ROOM = ("도면", "전개도", "입면", "단면", "상세", "범례", "평면", "일람", "진열", "수납",
             "서랍", "선반", "의자", "마네킹", "테이블", "책상", "캐비닛", "도장", "걸이",
             "행거", "가구", "집기", "목작업")


def is_room_name(text):
    """텍스트가 '방 이름'인지. '…실'로 끝나거나 방 키워드 포함하되, 가구/도면주기 마커나
    가구 접미(…장/…대/…판)는 제외 — 인테리어·구축 도면의 집기 라벨 오검출 방지."""
    t = (text or "").strip()
    if not (2 <= len(t) <= 12) or not any('가' <= c <= '힣' for c in t):
        return False
    if any(b in t for b in _NON_ROOM):
        return False
    if t.endswith(("장", "대", "판", "걸이")):        # 수납장·진열장·강의대 = 가구
        return False
    return t.endswith("실") or any(k in t for k in _ROOM_KW)


def guess_wall_layers(doc):
    """벽/칸막이일 법한 레이어 자동 추정 (휴리스틱 — 도면마다 달라 완벽하지 않음).

    이름 키워드(WAL/WALL/COL/WINDOW/ARCH/벽/구조/기둥) 매칭; 없으면 LINE 최다 레이어 폴백.
    실패해도(엉뚱한 레이어) 하류 flood-fill이 신뢰도 낮은 방을 needs_review로 걸러낸다.
    """
    from collections import Counter
    lc = Counter(e.dxf.layer for e in doc.modelspace().query("LINE"))
    if not lc:
        return []
    cand = [ln for ln in lc
            if any(k in ln.upper() for k in ("WAL", "WALL", "COL", "WINDOW", "WID", "ARCH"))
            or any(k in ln for k in ("벽", "구조", "옹벽", "기둥"))]
    return cand or [lc.most_common(1)[0][0]]


# AutoCAD $INSUNITS → mm 환산(비-mm 도면이 mm 가정으로 붕괴하는 것 방지).
_INSUNITS_MM = {1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0, 8: 0.0254, 9: 0.0254, 13: 1e-6}


def _to_mm_factor(doc):
    """도면 단위 → mm 배율. 미지정(0)/미지 단위는 mm(1.0) 가정."""
    try:
        return _INSUNITS_MM.get(int(doc.header.get("$INSUNITS", 0)), 1.0)
    except Exception:
        return 1.0


def rooms_from_dxf(doc, wall_layers, *, room_layers=None, mm_per_px=25.0, **kw):
    """dxf 문서 + 벽 레이어 → 방(name·area·confidence) 리스트.

    ⚠ 벽 레이어 선택은 도면마다 다르다(ARCH / WAL2+WAL+COL …) → 호출측이 지정한다.
       자동 선택 휴리스틱은 아직 견고하지 않음(별도 과제). 실명은 TEXT/MTEXT에서 추출.
       좌표는 $INSUNITS로 mm 환산(비-mm 도면 붕괴 방지) 후 라스터화.
    """
    msp = doc.modelspace()
    f = _to_mm_factor(doc)
    wall_set = set(wall_layers)
    segs = []
    for e in msp.query("LINE"):
        if e.dxf.layer in wall_set:
            a, b = e.dxf.start, e.dxf.end
            segs.append((a.x * f, a.y * f, b.x * f, b.y * f))
    room_set = set(room_layers) if room_layers else None
    labels = []
    for e in msp:
        if e.dxftype() not in ("TEXT", "MTEXT"):
            continue
        if room_set is not None and e.dxf.layer not in room_set:
            continue
        try:
            t = (e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text).strip()
        except Exception:
            continue
        if is_room_name(t):
            labels.append((t, (e.dxf.insert.x * f, e.dxf.insert.y * f)))
    return extract_rooms_raster(segs, labels, mm_per_px=mm_per_px, **kw)
