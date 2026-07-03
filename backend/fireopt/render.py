# -*- coding: utf-8 -*-
"""
render — matplotlib 2D 시각화 (배치/MEP/클래시, before/after).

라벨은 영문 사용(matplotlib 기본폰트의 한글 결손 회피). 색상 범례 고정.
"""
from __future__ import annotations

import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import matplotlib.font_manager as fm
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
from shapely.geometry import Polygon, MultiPolygon, Point

# 한글 폰트(있으면) 등록 — 대시보드 PNG 의 한글 라벨용
_KFONT = None
for _p in ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
           "/usr/share/fonts/truetype/nanum/NanumSquare_acR.ttf",
           "/usr/share/fonts/truetype/nanum/NanumSquareRoundR.ttf"):
    if os.path.exists(_p):
        try:
            fm.fontManager.addfont(_p)
            _KFONT = fm.FontProperties(fname=_p).get_name()
            plt.rcParams["font.family"] = _KFONT
            plt.rcParams["axes.unicode_minus"] = False
            break
        except Exception:
            pass

# 시설별 색/마커 (영문 라벨)
STYLE = {
    "sprinkler":   ("#1f77b4", "o", "Sprinkler"),
    "detector":    ("#2ca02c", "s", "Detector"),
    "extinguisher": ("#9467bd", "^", "Extinguisher"),
    "hydrant":     ("#d62728", "P", "Hydrant"),
    "evacuation":  ("#ff7f0e", "*", "Exit"),
}


def _draw_poly(ax, geom, **kw):
    if geom is None or geom.is_empty:
        return
    polys = [geom] if isinstance(geom, Polygon) else (
        list(geom.geoms) if isinstance(geom, MultiPolygon) else [])
    for p in polys:
        xs, ys = p.exterior.xy
        ax.fill(xs, ys, **kw)


def plot_layout(ax, rooms, meps, layout, clashes=None, title=""):
    # 룸
    for r in rooms:
        _draw_poly(ax, r.polygon, facecolor="#f5f5f5", edgecolor="#888888", linewidth=0.8)
    # MEP(전기/기계)
    for m in (meps or []):
        _draw_poly(ax, m.geom, facecolor="#cccccc", edgecolor="#999999",
                   linewidth=0.4, alpha=0.6)
    # 소방 장치
    for fac, lst in layout.placements.items():
        if not lst:
            continue
        color, marker, _ = STYLE.get(fac, ("#000000", "x", fac))
        xs = [p.point[0] for p in lst]; ys = [p.point[1] for p in lst]
        ax.scatter(xs, ys, c=color, marker=marker, s=28, zorder=5,
                   edgecolors="white", linewidths=0.4)
    # 클래시
    if clashes:
        hx = [c.x for c in clashes if c.severity == "hard"]
        hy = [c.y for c in clashes if c.severity == "hard"]
        sx = [c.x for c in clashes if c.severity == "soft"]
        sy = [c.y for c in clashes if c.severity == "soft"]
        ax.scatter(sx, sy, facecolors="none", edgecolors="#ff9900", marker="o",
                   s=140, linewidths=1.6, zorder=6, label="soft clash")
        ax.scatter(hx, hy, c="red", marker="x", s=120, linewidths=2.2,
                   zorder=7, label="hard clash")
    ax.set_aspect("equal", "box")
    ax.set_title(title, fontsize=11)
    ax.tick_params(labelsize=7)


def _legend(fig, layout, clashes):
    handles = []
    for fac, (color, marker, label) in STYLE.items():
        if layout.placements.get(fac):
            handles.append(Line2D([0], [0], color="w", marker=marker,
                                  markerfacecolor=color, markeredgecolor="white",
                                  markersize=8, label=label, linestyle=""))
    handles.append(Patch(facecolor="#cccccc", edgecolor="#999999", label="MEP (elec.)"))
    if clashes:
        handles.append(Line2D([0], [0], color="red", marker="x", markersize=9,
                              linestyle="", label="hard clash"))
        handles.append(Line2D([0], [0], marker="o", markerfacecolor="none",
                              markeredgecolor="#ff9900", markersize=10,
                              linestyle="", label="soft clash"))
    fig.legend(handles=handles, loc="lower center", ncol=len(handles),
               fontsize=8, frameon=False, bbox_to_anchor=(0.5, -0.02))


def plot_single(rooms, meps, layout, clashes, out_png, title="FireOpt layout"):
    fig, ax = plt.subplots(figsize=(9, 8))
    plot_layout(ax, rooms, meps, layout, clashes, title)
    _legend(fig, layout, clashes)
    fig.tight_layout()
    fig.savefig(out_png, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return out_png


def plot_drawing(rooms, layout, out_png, meta=None, pipe_edges=None, pipe_pts=None,
                 evidence=None):
    """소방시설 평면 배치도(시공도면 스타일) — 벽체/실명/표준기호/배관/범례/표제란/방위/축척.

    분석 산점도가 아니라 CAD 평면도에 가깝게 작도. (PNG 미리보기; 편집은 DXF 사용)
    evidence: 법규위반 근거 오버레이([{kind:'point'|'polygon', coords}]) — 점=빨간 ✕, 면=빨간 강조.
    """
    import numpy as np
    from matplotlib.patches import Circle, Rectangle, RegularPolygon, FancyArrow, Polygon as MplPoly
    meta = meta or {}

    # 도면 영역 산정
    minx = min(r.polygon.bounds[0] for r in rooms); miny = min(r.polygon.bounds[1] for r in rooms)
    maxx = max(r.polygon.bounds[2] for r in rooms); maxy = max(r.polygon.bounds[3] for r in rooms)
    W, H = maxx - minx, maxy - miny
    pad = max(W, H) * 0.12

    fig, ax = plt.subplots(figsize=(13, 12))
    ax.set_aspect("equal")
    ax.set_xlim(minx - pad, maxx + pad * 2.4)   # 우측 여백: 범례/표제란
    ax.set_ylim(miny - pad * 1.4, maxy + pad)
    ax.axis("off")

    # 1) 벽체(실 외곽을 이중선 느낌의 굵은 선) + 실명/면적
    for r in rooms:
        xs, ys = r.polygon.exterior.xy
        ax.fill(xs, ys, facecolor="white", edgecolor="#222", linewidth=2.2, zorder=2)
        ax.plot(xs, ys, color="#222", linewidth=0.6, zorder=2)  # 내측선(이중선 느낌)
        c = r.polygon.centroid
        _minx, _miny, _maxx, _maxy = r.polygon.bounds
        ly = min(_maxy - (_maxy - _miny) * 0.12, c.y + (_maxy - c.y) * 0.6)
        if not r.polygon.contains(Point(c.x, ly)):
            ly = c.y
        ax.text(c.x, ly, f"{r.name}\n{r.area:.1f}㎡", ha="center", va="center",
                fontsize=7.5, color="#444", zorder=3,
                bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.7))

    # 2) 배관 계통(가지배관) — 입상관(riser) 기준 MST
    if pipe_edges is not None and pipe_pts is not None and len(pipe_pts):
        for i, j in pipe_edges:
            ax.plot([pipe_pts[i][0], pipe_pts[j][0]], [pipe_pts[i][1], pipe_pts[j][1]],
                    color="#c0392b", linewidth=1.3, zorder=4, solid_capstyle="round")
        # 입상관 = 마지막 점(connect point)
        rx, ry = pipe_pts[-1]
        ax.add_patch(Circle((rx, ry), max(W, H) * 0.012, facecolor="#c0392b",
                            edgecolor="k", lw=0.8, zorder=6))
        ax.text(rx, ry - max(W, H) * 0.025, "입상관", ha="center", fontsize=6.5, color="#c0392b")

    # 3) 소방 표준기호
    u = max(W, H) * 0.010          # 기호 기준 크기(데이터 단위)
    _draw_symbols(ax, layout, u)

    # 3.5) 법규위반 근거 오버레이(점=빨간 ✕, 면=빨간 강조)
    if evidence:
        for ev in evidence:
            if ev.get("kind") == "point":
                ex, ey = ev["coords"]
                ax.plot([ex - u * 1.3, ex + u * 1.3], [ey - u * 1.3, ey + u * 1.3],
                        color="#e10000", lw=2.2, zorder=8, solid_capstyle="round")
                ax.plot([ex - u * 1.3, ex + u * 1.3], [ey + u * 1.3, ey - u * 1.3],
                        color="#e10000", lw=2.2, zorder=8, solid_capstyle="round")
            elif ev.get("kind") == "polygon":
                ax.add_patch(MplPoly(ev["coords"], closed=True, facecolor="#e10000",
                                     edgecolor="#e10000", alpha=0.15, linewidth=2, zorder=3))

    # 4) 범례 · 표제란 · 방위 · 축척
    _draw_legend(ax, minx, miny, maxx, maxy, pad, u)
    _draw_titleblock(ax, maxx, miny, pad, meta)
    _draw_north(ax, maxx, maxy, pad)
    _draw_scalebar(ax, minx, miny, pad, W)

    ax.set_title(meta.get("title", "소방시설 평면 배치도"), fontsize=14, fontweight="bold", pad=12)
    fig.savefig(out_png, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_png


_SYM_FS = 5.5   # 심볼 내부 문자 고정 글자크기(pt)


def _sym_sprinkler(ax, x, y, u):
    from matplotlib.patches import Circle
    ax.add_patch(Circle((x, y), u, facecolor="white", edgecolor="#1f4e79", lw=1.2, zorder=6))
    ax.plot([x - u, x + u], [y, y], color="#1f4e79", lw=0.9, zorder=7)
    ax.plot([x, x], [y - u, y + u], color="#1f4e79", lw=0.9, zorder=7)


def _sym_detector(ax, x, y, u, label="S"):
    from matplotlib.patches import Circle
    ax.add_patch(Circle((x, y), u * 1.05, facecolor="#eaf6ea", edgecolor="#2e7d32", lw=1.1, zorder=6))
    ax.text(x, y, label, ha="center", va="center", fontsize=_SYM_FS, color="#2e7d32",
            fontweight="bold", zorder=7)


def _sym_extinguisher(ax, x, y, u):
    from matplotlib.patches import RegularPolygon
    ax.add_patch(RegularPolygon((x, y), 3, radius=u * 1.3, orientation=0,
                                facecolor="#fde2e2", edgecolor="#c0392b", lw=1.1, zorder=6))
    ax.text(x, y - u * 0.2, "소", ha="center", va="center", fontsize=_SYM_FS - 1.0,
            color="#c0392b", fontweight="bold", zorder=7)


def _sym_hydrant(ax, x, y, u):
    from matplotlib.patches import Rectangle
    s = u * 1.8
    ax.add_patch(Rectangle((x - s / 2, y - s / 2), s, s, facecolor="#fff3cd",
                           edgecolor="#b9770e", lw=1.3, zorder=6))
    ax.text(x, y, "H", ha="center", va="center", fontsize=_SYM_FS, color="#9c6500",
            fontweight="bold", zorder=7)


def _sym_exit(ax, x, y, u):
    from matplotlib.patches import Rectangle
    s = u * 2.0
    ax.add_patch(Rectangle((x - s / 2, y - s * 0.42), s, s * 0.84, facecolor="#16a34a",
                           edgecolor="#0b6", lw=0.8, zorder=6))
    ax.text(x, y, "E", ha="center", va="center", fontsize=_SYM_FS - 0.5, color="white",
            fontweight="bold", zorder=7)


def _draw_symbols(ax, layout, u):
    for p in layout.placements.get("sprinkler", []):
        _sym_sprinkler(ax, p.point[0], p.point[1], u)
    for p in layout.placements.get("detector", []):
        _sym_detector(ax, p.point[0], p.point[1], u)
    for p in layout.placements.get("extinguisher", []):
        _sym_extinguisher(ax, p.point[0], p.point[1], u)
    for p in layout.placements.get("hydrant", []):
        _sym_hydrant(ax, p.point[0], p.point[1], u)
    for p in layout.placements.get("evacuation", []):
        _sym_exit(ax, p.point[0], p.point[1], u)


def _draw_legend(ax, minx, miny, maxx, maxy, pad, u):
    from matplotlib.patches import Rectangle
    x0 = maxx + pad * 0.35
    y0 = maxy - pad * 0.2
    ax.text(x0, y0 + pad * 0.25, "범 례", fontsize=10, fontweight="bold")
    items = [("sprinkler", "스프링클러 헤드"), ("detector", "감지기(연기)"),
             ("extinguisher", "소화기"), ("hydrant", "옥내소화전"),
             ("exit", "피난구(비상구)"), ("pipe", "가지배관")]
    dy = pad * 0.34
    for k, (sym, label) in enumerate(items):
        yy = y0 - k * dy
        sx = x0 + pad * 0.12
        if sym == "sprinkler": _sym_sprinkler(ax, sx, yy, u)
        elif sym == "detector": _sym_detector(ax, sx, yy, u)
        elif sym == "extinguisher": _sym_extinguisher(ax, sx, yy, u)
        elif sym == "hydrant": _sym_hydrant(ax, sx, yy, u)
        elif sym == "exit": _sym_exit(ax, sx, yy, u)
        elif sym == "pipe": ax.plot([sx - u, sx + u], [yy, yy], color="#c0392b", lw=1.6)
        ax.text(x0 + pad * 0.32, yy, label, fontsize=8, va="center")


def _draw_titleblock(ax, maxx, miny, pad, meta):
    from matplotlib.patches import Rectangle
    bw, bh = pad * 2.0, pad * 1.25
    x0 = maxx + pad * 0.35
    y0 = miny - pad * 1.3
    ax.add_patch(Rectangle((x0, y0), bw, bh, facecolor="white", edgecolor="#222", lw=1.4, zorder=8))
    rows = [("프로젝트", meta.get("project", "-")),
            ("도면명", meta.get("title", "소방시설 평면 배치도")),
            ("축척", meta.get("scale", "N.T.S (참고)")),
            ("작성일", meta.get("date", "")),
            ("도면번호", meta.get("dwg_no", "FP-101")),
            ("작성", "FireOpt 자동생성")]
    n = len(rows)
    for i, (k, v) in enumerate(rows):
        yy = y0 + bh - (i + 0.5) * (bh / n)
        ax.plot([x0, x0 + bw], [y0 + bh - (i) * (bh / n)] * 2, color="#bbb", lw=0.5, zorder=9)
        ax.text(x0 + bw * 0.04, yy, k, fontsize=7, va="center", color="#666", zorder=9)
        ax.text(x0 + bw * 0.34, yy, str(v), fontsize=7.5, va="center", fontweight="bold", zorder=9)


def _draw_north(ax, maxx, maxy, pad):
    from matplotlib.patches import RegularPolygon
    x, y = maxx + pad * 1.9, maxy - pad * 0.1
    ax.add_patch(RegularPolygon((x, y), 3, radius=pad * 0.18, orientation=0,
                                facecolor="#222", edgecolor="#222", zorder=8))
    ax.text(x, y + pad * 0.26, "N", ha="center", fontsize=10, fontweight="bold")


def _draw_scalebar(ax, minx, miny, pad, W):
    # 5 m 축척 막대 (좌표가 미터)
    bar = 5.0 if W > 8 else max(1.0, round(W / 4))
    x0, y0 = minx, miny - pad * 0.55
    ax.plot([x0, x0 + bar], [y0, y0], color="#222", lw=3, solid_capstyle="butt")
    ax.plot([x0, x0], [y0 - pad * 0.05, y0 + pad * 0.05], color="#222", lw=1)
    ax.plot([x0 + bar, x0 + bar], [y0 - pad * 0.05, y0 + pad * 0.05], color="#222", lw=1)
    ax.text(x0 + bar / 2, y0 - pad * 0.18, f"{bar:.0f} m", ha="center", fontsize=8)


_CAD_THEME = {
    "dark":  {"BG": "#0b0e14", "WALL": "#5b6b7a", "HEAD": "#39d353", "BR": "#33c1ff",
              "MAIN": "#ff3b30", "TXT": "#e6edf3", "YEL": "#ffd60a", "PANEL": "#11161f",
              "PLINE": "#2a3340", "SUB": "#8aaabb"},
    "light": {"BG": "#ffffff", "WALL": "#333333", "HEAD": "#1a7f37", "BR": "#0969da",
              "MAIN": "#cf222e", "TXT": "#1a1a1a", "YEL": "#b35900", "PANEL": "#f6f8fa",
              "PLINE": "#d0d7de", "SUB": "#57606a"},
}


def plot_piping_cad(rooms, layout, segments, riser, out_png, meta=None, theme="dark"):
    """CAD 스타일 스프링클러 배관 평면도 (theme: 'dark' | 'light').

    직교 배관망 + 구간별 호칭경(SP.nn) 라벨 + 입상관/알람밸브 + 범례/표제란.
    """
    from matplotlib.patches import Circle, Rectangle
    meta = meta or {}
    pal = _CAD_THEME.get(theme, _CAD_THEME["dark"])
    BG, WALL, HEAD = pal["BG"], pal["WALL"], pal["HEAD"]
    BR, MAIN, TXT, YEL = pal["BR"], pal["MAIN"], pal["TXT"], pal["YEL"]
    PANEL, PLINE, SUB = pal["PANEL"], pal["PLINE"], pal["SUB"]

    minx = min(r.polygon.bounds[0] for r in rooms); miny = min(r.polygon.bounds[1] for r in rooms)
    maxx = max(r.polygon.bounds[2] for r in rooms); maxy = max(r.polygon.bounds[3] for r in rooms)
    W, H = maxx - minx, maxy - miny
    pad = max(W, H) * 0.12

    fig, ax = plt.subplots(figsize=(14, 12))
    fig.patch.set_facecolor(BG); ax.set_facecolor(BG)
    ax.set_aspect("equal")
    ax.set_xlim(minx - pad, maxx + pad * 2.6)
    ax.set_ylim(miny - pad * 1.4, maxy + pad)
    ax.axis("off")

    # 벽체(실 외곽)
    for r in rooms:
        xs, ys = r.polygon.exterior.xy
        ax.plot(xs, ys, color=WALL, linewidth=1.4, zorder=2)

    # 배관: 가지배관/교차배관 + 호칭경 라벨
    def _lw(dia): return 0.8 + dia / 45.0
    for s in segments:
        (x0, y0), (x1, y1) = s["p1"], s["p2"]
        col = MAIN if s["kind"] in ("main", "riser") else BR
        ax.plot([x0, x1], [y0, y1], color=col, linewidth=_lw(s["dia"]),
                solid_capstyle="round", zorder=4)
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        rot = 90 if abs(x1 - x0) < 1e-6 else 0
        ax.text(mx, my + (0 if rot else pad * 0.06), f"SP.{s['dia']}",
                color=YEL, fontsize=6.5, rotation=rot, ha="center", va="center", zorder=8)

    # 스프링클러 헤드
    for p in layout.placements.get("sprinkler", []):
        ax.add_patch(Circle(p.point, max(W, H) * 0.006, facecolor=BG,
                            edgecolor=HEAD, lw=1.0, zorder=6))
        ax.plot([p.point[0] - max(W, H) * 0.006, p.point[0] + max(W, H) * 0.006],
                [p.point[1], p.point[1]], color=HEAD, lw=0.6, zorder=7)
        ax.plot([p.point[0], p.point[0]],
                [p.point[1] - max(W, H) * 0.006, p.point[1] + max(W, H) * 0.006],
                color=HEAD, lw=0.6, zorder=7)

    # 입상관 + 알람밸브 심볼
    if riser is not None:
        rr = max(W, H) * 0.016
        ax.add_patch(Circle(riser, rr, facecolor="none", edgecolor=MAIN, lw=1.6, zorder=9))
        ax.plot([riser[0] - rr, riser[0] + rr], [riser[1], riser[1]], color=MAIN, lw=1.2, zorder=9)
        ax.plot([riser[0], riser[0]], [riser[1] - rr, riser[1] + rr], color=MAIN, lw=1.2, zorder=9)
        ax.text(riser[0], riser[1] - rr * 2.2, "입상관\n(알람밸브)", color=MAIN, fontsize=6.5,
                ha="center", va="top", zorder=9)

    # 범례
    lx = maxx + pad * 0.4; ly = maxy
    ax.text(lx, ly + pad * 0.2, "범 례 (LEGEND)", color=TXT, fontsize=10, fontweight="bold")
    leg = [(HEAD, "스프링클러 헤드", "head"), (BR, "가지배관 (SP)", "line"),
           (MAIN, "교차/주배관 (SP)", "line"), (YEL, "배관 호칭경(mm)", "txt")]
    for i, (c, lbl, kind) in enumerate(leg):
        yy = ly - i * pad * 0.32
        if kind == "line":
            ax.plot([lx, lx + pad * 0.18], [yy, yy], color=c, lw=2.2)
        elif kind == "head":
            ax.add_patch(Circle((lx + pad * 0.09, yy), pad * 0.05, facecolor=BG, edgecolor=c, lw=1.2))
        else:
            ax.text(lx + pad * 0.04, yy, "SP.nn", color=c, fontsize=7, va="center")
        ax.text(lx + pad * 0.26, yy, lbl, color=TXT, fontsize=8, va="center")

    # 표제란
    bw, bh = pad * 2.15, pad * 1.25
    bx, by = maxx + pad * 0.4, miny - pad * 1.3
    ax.add_patch(Rectangle((bx, by), bw, bh, facecolor=PANEL, edgecolor=WALL, lw=1.2, zorder=8))
    rows = [("프로젝트", meta.get("project", "-")), ("도면명", "스프링클러 배관 평면도"),
            ("배관기준", "NFTC 103 별표(헤드수별 구경)"), ("축척", meta.get("scale", "N.T.S")),
            ("작성일", meta.get("date", "")), ("도면번호", meta.get("dwg_no", "FP-201"))]
    for i, (k, v) in enumerate(rows):
        yy = by + bh - (i + 0.5) * (bh / len(rows))
        ax.plot([bx, bx + bw], [by + bh - i * (bh / len(rows))] * 2, color=PLINE, lw=0.5, zorder=9)
        ax.text(bx + bw * 0.04, yy, k, color=SUB, fontsize=7, va="center", zorder=9)
        ax.text(bx + bw * 0.32, yy, str(v), color=TXT, fontsize=7.5, va="center", fontweight="bold", zorder=9)

    # 방위 + 축척바
    nx_, ny_ = maxx + pad * 2.2, maxy
    ax.plot([nx_, nx_], [ny_, ny_ + pad * 0.25], color=TXT, lw=1.5)
    ax.text(nx_, ny_ + pad * 0.34, "N", color=TXT, fontsize=10, fontweight="bold", ha="center")
    bar = 5.0 if W > 8 else max(1.0, round(W / 4))
    ax.plot([minx, minx + bar], [miny - pad * 0.55] * 2, color=TXT, lw=3)
    ax.text(minx + bar / 2, miny - pad * 0.75, f"{bar:.0f} m", color=TXT, fontsize=8, ha="center")

    ax.set_title("스프링클러 배관 평면도 (FireOpt 1차 자동산정)", color=TXT, fontsize=14,
                 fontweight="bold", pad=12)
    fig.savefig(out_png, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    return out_png


def plot_dashboard(result, ba_png, out_png):
    """result(dict) + before_after.png → 단일 대시보드 PNG (서버 없이 바로 열람)."""
    b, a, cost = result["before"], result["after"], result["cost"]
    chk = result.get("checks", {})
    fig = plt.figure(figsize=(13.5, 13))
    gs = fig.add_gridspec(3, 1, height_ratios=[0.14, 0.60, 0.26], hspace=0.08)

    # --- 헤더 + KPI ---
    ax = fig.add_subplot(gs[0]); ax.axis("off"); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.text(0, 0.82, "FireOpt — 소방 설계 자동 최적화", fontsize=20, fontweight="bold")
    ax.text(0, 0.62, f"{result.get('schema','')} · 룸 {result.get('rooms')}개 · "
            f"연면적 {result.get('total_area_m2')}㎡ · 전기(MEP) {result.get('mep_count')}개",
            fontsize=11, color="#666")
    mv = a.get("moved", {})
    kpis = [("충돌(하드) 해소", f"{b['clashes']['hard']} → {a['clashes']['hard']} 건", "#16a34a"),
            ("재시공 리스크 절감", f"{cost['delta']['rework_risk_saved']:,} 원", "#16a34a"),
            ("총비용(자재+리스크)", f"{cost['after']['total_cost']:,} 원", "#1a1a2e"),
            ("재배치 장치", f"{mv.get('moved',0)}개 / {mv.get('total_displacement_m',0)}m", "#1a1a2e")]
    for i, (l, v, c) in enumerate(kpis):
        x = 0.02 + i * 0.25
        ax.text(x, 0.32, l, fontsize=11, color="#888")
        ax.text(x, 0.04, v, fontsize=15.5, fontweight="bold", color=c)

    # --- before/after 이미지 ---
    axi = fig.add_subplot(gs[1]); axi.axis("off")
    if os.path.exists(ba_png):
        axi.imshow(mpimg.imread(ba_png))

    # --- 비용/수량 표 ---
    axt = fig.add_subplot(gs[2]); axt.axis("off")
    d = cost["delta"]
    rows = [
        ["하드 충돌(건)", b["clashes"]["hard"], a["clashes"]["hard"], f"-{d['hard_clashes_resolved']}"],
        ["배관길이(m, 프록시)", cost["before"]["pipe_len_m"], cost["after"]["pipe_len_m"], f"{d['pipe_len_change_m']:+}"],
        ["자재비(원)", f"{cost['before']['material_cost']:,}", f"{cost['after']['material_cost']:,}", f"{d['material_cost_change']:+,}"],
        ["재시공 리스크비(원)", f"{cost['before']['rework_risk_cost']:,}", f"{cost['after']['rework_risk_cost']:,}", f"-{d['rework_risk_saved']:,}"],
        ["총비용(원)", f"{cost['before']['total_cost']:,}", f"{cost['after']['total_cost']:,}", f"{d['total_cost_change']:+,}"],
    ]
    cnt = a["counts"]
    counts_txt = (f"배치 수량 —  스프링클러 {cnt['sprinkler']} · 감지기 {cnt['detector']} · "
                  f"소화기 {cnt['extinguisher']} · 옥내소화전 {cnt['hydrant']} · 피난출구 {cnt['evacuation']}")
    axt.text(0.5, 1.02, counts_txt, fontsize=10.5, ha="center", transform=axt.transAxes, color="#333")
    tbl = axt.table(cellText=rows, colLabels=["항목", "Before", "After", "변화"],
                    cellLoc="center", colLoc="center", loc="center",
                    colWidths=[0.34, 0.22, 0.22, 0.22])
    tbl.auto_set_font_size(False); tbl.set_fontsize(10.5); tbl.scale(1, 1.5)
    for (rr, cc), cell in tbl.get_celld().items():
        if rr == 0:
            cell.set_facecolor("#7f1d1d"); cell.set_text_props(color="white", fontweight="bold")
        elif rows[rr - 1][0].startswith("총비용"):
            cell.set_facecolor("#fef2f2"); cell.set_text_props(fontweight="bold")
        cell.set_edgecolor("#e5e7eb")
    # 재시공 리스크비 산정 근거(왜 이 값인지)
    basis = cost.get("basis", {})
    items = basis.get("items", {})
    brk = " + ".join(f"{int(v['value'])//10000}만" for v in items.values()) if items else ""
    axt.text(0.5, -0.06, f"재시공 절감 산식:  {d.get('rework_saved_formula','')}",
             fontsize=9.5, ha="center", color="#c0392b", fontweight="bold", transform=axt.transAxes)
    axt.text(0.5, -0.13, f"건당 {basis.get('per_clash',0):,}원 내역 = {brk}  "
             f"(배관재시공·인건비·타공종이설·마감복구·지연간접)",
             fontsize=8, ha="center", color="#666", transform=axt.transAxes)
    axt.text(0.5, -0.19, "⚠ 단가는 조정가능 가정값 · 도면 기하로 확정되는 값은 ‘충돌 건수’ · 모든 연산 로컬 처리",
             fontsize=7.5, ha="center", color="#999", transform=axt.transAxes)

    fig.savefig(out_png, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return out_png


def plot_before_after(rooms, meps, before_layout, before_clashes,
                      after_layout, after_clashes, out_png):
    fig, axes = plt.subplots(1, 2, figsize=(16, 8))
    nb = len(before_clashes or [])
    na = len(after_clashes or [])
    plot_layout(axes[0], rooms, meps, before_layout, before_clashes,
                f"BEFORE  (clashes: {nb})")
    plot_layout(axes[1], rooms, meps, after_layout, after_clashes,
                f"AFTER re-optimize  (clashes: {na})")
    _legend(fig, before_layout, before_clashes or after_clashes)
    fig.tight_layout()
    fig.savefig(out_png, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return out_png
