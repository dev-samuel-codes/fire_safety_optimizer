# -*- coding: utf-8 -*-
"""
export — ezdxf 로 '실무 소방 평면도' DXF 출력.

단순 마커가 아니라 실제 시공도면과 동일한 구성요소를 모두 그린다:
  · 도면틀(border) + 표제란(title block)         · 벽체/실구획 배경 + 실명·면적
  · 소방 표준기호(블록 INSERT) — 시설별 레이어   · 스프링클러 배관망 + 구간 호칭경(25A…)
  · 입상관/알람밸브 기점 기호                      · 주요 치수(overall dimension)
  · 타공종 간섭(clash) 구름표시 + 콜아웃            · 범례(legend) · 수량·산정표

레이어 체계(실무 관례, 한글):  A-* 건축배경 / F-* 소방 / M-* 기계전기 / 간섭 / 치수 / 문자 / 도면틀
좌표·치수 단위: 미터. 기호/문자 크기는 건물 규모에 비례(scale)해 어떤 도면에서도 가독.
"""
from __future__ import annotations

import math
import datetime

import ezdxf
from ezdxf.enums import TextEntityAlignment
from shapely.geometry import Polygon, MultiPolygon

from . import symbols as SYM

# ---------------------------------------------------------------------------
# 레이어 정의:  이름 → (ACI색, 선가중치[mm*100], 선종)
# ---------------------------------------------------------------------------
_LAYER_DEFS = {
    "A-벽체":       (8,   35, "CONTINUOUS"),
    "A-실구획":     (9,   13, "CONTINUOUS"),
    "A-문":         (8,   13, "CONTINUOUS"),
    "M-기계전기":   (9,   18, "DASHED"),
    "F-스프링클러": (5,   25, "CONTINUOUS"),
    "F-감지기":     (3,   25, "CONTINUOUS"),
    "F-소화기":     (6,   25, "CONTINUOUS"),
    "F-소화전":     (1,   30, "CONTINUOUS"),
    "F-피난":       (30,  30, "CONTINUOUS"),
    "F-배관":       (4,   40, "CONTINUOUS"),
    "F-배관문자":   (4,   18, "CONTINUOUS"),
    "간섭":         (1,   50, "CONTINUOUS"),
    "치수":         (7,   13, "CONTINUOUS"),
    "문자-실명":    (7,   18, "CONTINUOUS"),
    "도면틀":       (7,   35, "CONTINUOUS"),
    "범례":         (7,   18, "CONTINUOUS"),
}

_FAC_LAYER = {"sprinkler": "F-스프링클러", "detector": "F-감지기",
              "extinguisher": "F-소화기", "hydrant": "F-소화전",
              "evacuation": "F-피난"}

# 범례 표기(블록, 레이어, 한글명)
_LEGEND_ROWS = [
    (SYM.SPRINKLER,    "F-스프링클러", "스프링클러 헤드 (폐쇄형)"),
    (SYM.SMOKE,        "F-감지기",     "연기감지기 (광전식)"),
    (SYM.HEAT,         "F-감지기",     "열감지기 (차동식)"),
    (SYM.EXTINGUISHER, "F-소화기",     "소화기 (능력단위 3단위)"),
    (SYM.HYDRANT,      "F-소화전",     "옥내소화전함"),
    (SYM.EXIT,         "F-피난",       "피난구유도등 (비상구)"),
    (SYM.RISER,        "F-배관",       "입상관 / 알람밸브"),
]

_FONT_CANDIDATES = ("NanumGothic.ttf", "NanumSquare_acR.ttf",
                    "NanumSquareR.ttf", "malgun.ttf", "Arial.ttf")


# ---------------------------------------------------------------------------
# 문서 셋업
# ---------------------------------------------------------------------------
def _setup(doc):
    """레이어 · 선종 · 한글 텍스트 스타일 · 소방기호 블록 등록."""
    # DASHED 선종(없으면 추가)
    if "DASHED" not in doc.linetypes:
        doc.linetypes.add("DASHED", pattern="A,.5,-.25", description="- - - -")
    for name, (color, lw, lt) in _LAYER_DEFS.items():
        if name not in doc.layers:
            doc.layers.add(name, color=color, linetype=lt)
        doc.layers.get(name).dxf.lineweight = lw

    # 한글 텍스트 스타일(설치된 TTF 중 첫 후보)
    if SYM.TEXT_STYLE not in doc.styles:
        doc.styles.add(SYM.TEXT_STYLE, font=_FONT_CANDIDATES[0])

    SYM.define_blocks(doc, text_style=SYM.TEXT_STYLE)


def _mtext(msp, s, x, y, h, layer, *, align=TextEntityAlignment.MIDDLE_CENTER,
           rotation=0.0):
    t = msp.add_text(s, dxfattribs={"height": h, "layer": layer,
                                    "style": SYM.TEXT_STYLE, "rotation": rotation})
    t.set_placement((x, y), align=align)
    return t


def _add_poly(msp, geom, layer, close=True):
    if geom is None or getattr(geom, "is_empty", True):
        return
    polys = [geom] if isinstance(geom, Polygon) else (
        list(geom.geoms) if isinstance(geom, MultiPolygon) else [])
    for p in polys:
        msp.add_lwpolyline(list(p.exterior.coords), close=close,
                           dxfattribs={"layer": layer})


# ---------------------------------------------------------------------------
# 도면 구성요소
# ---------------------------------------------------------------------------
def _draw_background(msp, rooms, walls, doors):
    """건축 배경: 벽체 풋프린트 · 실 경계 · 문 개구부."""
    for w in (walls or []):
        _add_poly(msp, w, "A-벽체")
    for r in rooms:
        _add_poly(msp, r.polygon, "A-실구획")
    for d in (doors or []):
        x, y = (d.x, d.y) if hasattr(d, "x") else (d[0], d[1])
        msp.add_circle((x, y), 0.12, dxfattribs={"layer": "A-문"})


def _draw_room_labels(msp, rooms, th):
    """실명 + 면적(㎡). 라벨은 실 상단부에 배치해 중앙 그리드 헤드와 겹침 최소화."""
    from shapely.geometry import Point as _P
    for r in rooms:
        c = r.polygon.centroid
        minx, miny, maxx, maxy = r.polygon.bounds
        lx, ly = c.x, min(maxy - th * 2.0, c.y + (maxy - c.y) * 0.55)
        if not r.polygon.contains(_P(lx, ly)):     # L자형 등 → centroid 폴백
            lx, ly = c.x, c.y
        name = (r.name or "실").strip()
        _mtext(msp, name, lx, ly, th, "문자-실명")
        _mtext(msp, f"{r.area:,.1f}㎡", lx, ly - th * 1.1, th * 0.85, "문자-실명")


def _draw_symbols(msp, layout, sym_scale):
    """소방 표준기호를 시설별 레이어에 블록 INSERT 로 배치."""
    counts = {}
    for fac, lst in layout.placements.items():
        layer = _FAC_LAYER.get(fac, "0")
        for p in lst:
            blk = SYM.block_for(fac, p.kind)
            msp.add_blockref(blk, p.point, dxfattribs={
                "layer": layer, "xscale": sym_scale,
                "yscale": sym_scale, "color": 256})  # BYLAYER
            counts[fac] = counts.get(fac, 0) + 1
    return counts


def _draw_piping(msp, segments, riser, th, sym_scale):
    """스프링클러 배관망: 구간 선 + 구간별 호칭경(예 50A) + 입상관 기호."""
    if not segments:
        return
    for s in segments:
        p1, p2 = s["p1"], s["p2"]
        if s["kind"] == "riser":
            continue
        msp.add_line(p1, p2, dxfattribs={"layer": "F-배관"})
        # 구경 라벨(구간 중앙, 선 방향에 맞춰 회전)
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        ang = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))
        if ang > 90 or ang < -90:
            ang += 180
        off = th * 0.8
        ox, oy = -math.sin(math.radians(ang)) * off, math.cos(math.radians(ang)) * off
        _mtext(msp, f"{s['dia']}A", mx + ox, my + oy, th * 0.8, "F-배관문자",
               rotation=ang)
    if riser is not None:
        msp.add_blockref(SYM.RISER, riser, dxfattribs={
            "layer": "F-배관", "xscale": sym_scale, "yscale": sym_scale, "color": 256})
        _mtext(msp, "입상관", riser[0] + 0.4 * sym_scale, riser[1], th * 0.8,
               "F-배관", align=TextEntityAlignment.MIDDLE_LEFT)


def _draw_dimensions(msp, bbox, th):
    """주요 치수: 건물 전체 가로·세로 overall dimension."""
    minx, miny, maxx, maxy = bbox
    dimstyle = "EZDXF" if "EZDXF" in msp.doc.dimstyles else "Standard"
    ov = {"dimtxt": th * 0.9, "dimasz": th * 0.7, "dimexe": th * 0.4,
          "dimexo": th * 0.4, "dimgap": th * 0.3, "dimtad": 1, "dimtxsty": SYM.TEXT_STYLE,
          "dimlfac": 1.0, "dimdec": 2, "dimscale": 1.0}
    try:
        d = msp.add_linear_dim(base=(0, miny - th * 4), p1=(minx, miny), p2=(maxx, miny),
                               dimstyle=dimstyle, override=ov,
                               dxfattribs={"layer": "치수"})
        d.render()
        d = msp.add_linear_dim(base=(minx - th * 4, 0), p1=(minx, miny), p2=(minx, maxy),
                               angle=90, dimstyle=dimstyle, override=ov,
                               dxfattribs={"layer": "치수"})
        d.render()
    except Exception:
        pass  # 치수는 보조요소 — 실패해도 도면 산출 지속


def _draw_clashes(msp, clashes, th, sym_scale):
    """타공종 간섭: 구름형 표시 + 콜아웃 번호."""
    if not clashes:
        return
    for i, c in enumerate(clashes, 1):
        x, y = c.x, c.y
        r = 0.35 * sym_scale
        # 간단 구름(8 호) 표시
        n = 8
        pts = []
        for k in range(n):
            a = 2 * math.pi * k / n
            pts.append((x + r * math.cos(a), y + r * math.sin(a)))
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "간섭"})
        sev = "X" if c.severity == "hard" else "△"
        _mtext(msp, f"{sev}{i}", x, y, th * 0.8, "간섭")


# ---------------------------------------------------------------------------
# 우측 패널: 범례 · 수량/산정표 · 표제란
# ---------------------------------------------------------------------------
def _draw_legend(msp, x0, y_top, th, sym_scale):
    """범례 박스 — 기호 ↔ 명칭."""
    rows = _LEGEND_ROWS
    rh = th * 2.6
    w = th * 22
    y_bot = y_top - rh * (len(rows) + 1)
    msp.add_lwpolyline([(x0, y_bot), (x0 + w, y_bot), (x0 + w, y_top), (x0, y_top)],
                       close=True, dxfattribs={"layer": "범례"})
    _mtext(msp, "범   례", x0 + w / 2, y_top - rh * 0.5, th * 1.1, "범례")
    msp.add_line((x0, y_top - rh), (x0 + w, y_top - rh), dxfattribs={"layer": "범례"})
    for i, (blk, layer, label) in enumerate(rows):
        yy = y_top - rh * (i + 1.5)
        msp.add_blockref(blk, (x0 + rh * 0.7, yy), dxfattribs={
            "layer": layer, "xscale": sym_scale, "yscale": sym_scale, "color": 256})
        _mtext(msp, label, x0 + rh * 1.5, yy, th, "범례",
               align=TextEntityAlignment.MIDDLE_LEFT)
    return y_bot


def _draw_quantity_table(msp, x0, y_top, th, counts, checks, hydraulics=None):
    """수량·산정표 — 시설 수량 + 주요 산정(수리계산/수원/펌프/피난기구/소화기 능력단위)."""
    rows = [
        ("스프링클러 헤드", f"{counts.get('sprinkler', 0)} EA"),
        ("감지기", f"{counts.get('detector', 0)} EA"),
        ("소화기", f"{counts.get('extinguisher', 0)} EA"),
        ("옥내소화전", f"{counts.get('hydrant', 0)} EA"),
        ("피난구유도등", f"{counts.get('evacuation', 0)} EA"),
    ]
    if hydraulics:
        rows.append(("SP 기준개수", f"{hydraulics.get('design_head_count', '-')} 개"))
        rows.append(("SP 설계유량", f"{hydraulics.get('design_flow_lpm', '-')} L/min"))
        rows.append(("SP 수원", f"{hydraulics.get('water_source_m3', '-')} ㎥"))
        rows.append(("SP 필요압력", f"{hydraulics.get('required_pressure_mpa', '-')} MPa"))
        rows.append(("SP 펌프양정", f"{hydraulics.get('pump_head_m', '-')} m"))
    hyd = (checks or {}).get("hydrant", {})
    if hyd:
        rows.append(("소화전 수원", f"{hyd.get('water_source_m3', '-')} ㎥"))
        rows.append(("소화전 펌프", f"{hyd.get('pump_flow_Lpm', '-')} L/min"))
    ext = (checks or {}).get("extinguisher", {})
    if ext:
        rows.append(("소화기 능력단위", f"{ext.get('capacity_units_required', '-')} 단위"))
    evac = (checks or {}).get("evacuation", {})
    if evac:
        rows.append(("피난기구 산정", f"{evac.get('escape_devices_required', '-')} 개"))

    rh = th * 2.4
    w = th * 22
    y_bot = y_top - rh * (len(rows) + 1)
    msp.add_lwpolyline([(x0, y_bot), (x0 + w, y_bot), (x0 + w, y_top), (x0, y_top)],
                       close=True, dxfattribs={"layer": "범례"})
    _mtext(msp, "수량 · 산정표", x0 + w / 2, y_top - rh * 0.5, th * 1.1, "범례")
    msp.add_line((x0, y_top - rh), (x0 + w, y_top - rh), dxfattribs={"layer": "범례"})
    midx = x0 + w * 0.62
    msp.add_line((midx, y_bot), (midx, y_top - rh), dxfattribs={"layer": "범례"})
    for i, (k, v) in enumerate(rows):
        yy = y_top - rh * (i + 1.5)
        _mtext(msp, k, x0 + th * 0.8, yy, th, "범례", align=TextEntityAlignment.MIDDLE_LEFT)
        _mtext(msp, str(v), midx + th * 0.8, yy, th, "범례",
               align=TextEntityAlignment.MIDDLE_LEFT)
    return y_bot


def _draw_titleblock(msp, x0, y_bot, th, meta):
    """표제란(title block) — 프로젝트/도면명/축척/도면번호/작성일/기준."""
    meta = meta or {}
    rows = [
        ("프로젝트", meta.get("project", "-")),
        ("도 면 명", meta.get("title", "소방시설 평면 배치도")),
        ("축    척", meta.get("scale", "N.T.S")),
        ("도면번호", meta.get("dwg_no", "FP-101")),
        ("작 성 일", meta.get("date", datetime.date.today().isoformat())),
        ("적용기준", meta.get("code", "NFTC/NFPC · 건축법 피난·방화규칙")),
        ("작 성", meta.get("author", "FireOpt 자동생성")),
    ]
    rh = th * 2.4
    w = th * 22
    y_top = y_bot - th * 1.2
    y0 = y_top - rh * len(rows)
    msp.add_lwpolyline([(x0, y0), (x0 + w, y0), (x0 + w, y_top), (x0, y_top)],
                       close=True, dxfattribs={"layer": "도면틀"})
    midx = x0 + w * 0.34
    msp.add_line((midx, y0), (midx, y_top), dxfattribs={"layer": "도면틀"})
    for i, (k, v) in enumerate(rows):
        yy = y_top - rh * (i + 0.5)
        if i:
            msp.add_line((x0, y_top - rh * i), (x0 + w, y_top - rh * i),
                         dxfattribs={"layer": "도면틀"})
        _mtext(msp, k, x0 + th * 0.6, yy, th, "도면틀", align=TextEntityAlignment.MIDDLE_LEFT)
        _mtext(msp, str(v), midx + th * 0.6, yy, th * 0.95, "도면틀",
               align=TextEntityAlignment.MIDDLE_LEFT)
    return y0


def _draw_frame(msp, bbox):
    """도면틀(외곽 border)."""
    minx, miny, maxx, maxy = bbox
    msp.add_lwpolyline([(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)],
                       close=True, dxfattribs={"layer": "도면틀"})


# ---------------------------------------------------------------------------
# 오케스트레이터
# ---------------------------------------------------------------------------
def to_dxf(rooms, layout, meps, clashes, out_dxf, *, walls=None, doors=None,
           pipe_segments=None, riser=None, checks=None, meta=None, hydraulics=None):
    """실무 소방 평면도 DXF 생성.

    하위호환: 기존 호출 to_dxf(rooms, layout, meps, clashes, out_dxf) 그대로 동작.
    추가 입력(walls/doors/pipe_segments/riser/checks/meta)을 주면 도면 완성도가 높아진다.
    """
    doc = ezdxf.new("R2010", setup=True)
    _setup(doc)
    msp = doc.modelspace()

    # 건물 규모 → 문자/기호 스케일
    if rooms:
        xs = [c for r in rooms for c in (r.polygon.bounds[0], r.polygon.bounds[2])]
        ys = [c for r in rooms for c in (r.polygon.bounds[1], r.polygon.bounds[3])]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    else:
        minx, miny, maxx, maxy = 0, 0, 10, 10
    W, H = maxx - minx, maxy - miny
    diag = max(math.hypot(W, H), 1.0)
    scale = max(1.0, diag / 35.0)
    th = max(0.26, diag / 90.0)          # 기준 문자높이
    sym_scale = scale

    # 1) 건축 배경 + 실명
    _draw_background(msp, rooms, walls, doors)
    _draw_room_labels(msp, rooms, th)
    # 2) MEP 배경
    for m in (meps or []):
        _add_poly(msp, m.geom, "M-기계전기")
    # 3) 배관망 + 소방기호 + 치수 + 간섭
    _draw_piping(msp, pipe_segments, riser, th, sym_scale)
    counts = _draw_symbols(msp, layout, sym_scale)
    _draw_dimensions(msp, (minx, miny, maxx, maxy), th)
    _draw_clashes(msp, clashes, th, sym_scale)

    # 4) 우측 패널: 범례 → 수량표 → 표제란
    gutter = max(2.0, W * 0.10)
    px = maxx + gutter
    gap = th * 2.0
    y_after_legend = _draw_legend(msp, px, maxy, th, sym_scale)
    y_after_qty = _draw_quantity_table(msp, px, y_after_legend - gap, th, counts,
                                       checks, hydraulics)
    y_after_tb = _draw_titleblock(msp, px, y_after_qty - gap, th, meta)

    # 5) 전체 도면틀(콘텐츠 bbox + 여백)
    panel_right = px + th * 22
    fr_minx = minx - max(2.0, W * 0.06)
    fr_miny = min(miny - max(2.0, H * 0.10), y_after_tb - th * 2)
    fr_maxx = panel_right + th * 2
    fr_maxy = maxy + max(2.0, H * 0.06)
    _draw_frame(msp, (fr_minx, fr_miny, fr_maxx, fr_maxy))
    # 표제명 헤더
    _mtext(msp, meta.get("title", "소방시설 평면 배치도") if meta else "소방시설 평면 배치도",
           (fr_minx + fr_maxx) / 2, fr_maxy + th * 1.2, th * 1.8, "도면틀")

    doc.saveas(out_dxf)
    return out_dxf
