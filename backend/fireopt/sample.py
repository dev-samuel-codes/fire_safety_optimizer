# -*- coding: utf-8 -*-
"""
sample — 데모/테스트용 '한국형 건축 평면도 DXF' 생성기.

실무 CAD 관례를 모사: 단위 mm($INSUNITS=4), 한글 실명, 닫힌 폴리라인 룸(A-실구획),
벽체(A-벽체), 문(A-문). dxf_loader 가 이 도면을 읽어 소방 파이프라인을 태운다.
buildingSMART 공개 IFC 와 별개로, '국내 사무소 평면'을 즉시 데모할 입력을 제공.
"""
from __future__ import annotations

import os
import ezdxf

# (이름, x0, y0, x1, y1) — mm. 20m × 12m 사무소 1개층.
_ROOMS = [
    ("사무실 A", 0, 0, 8000, 7000),
    ("사무실 B", 8000, 0, 14000, 7000),
    ("회의실", 14000, 0, 20000, 7000),
    ("복도", 0, 7000, 20000, 9000),
    ("로비", 0, 9000, 6000, 12000),
    ("탕비실", 6000, 9000, 10000, 12000),
    ("화장실", 10000, 9000, 14000, 12000),
    ("계단실", 14000, 9000, 20000, 12000),
]

# 출입문 위치(개략) — (x, y) mm, A-문 레이어
_DOORS = [(4000, 7000), (11000, 7000), (17000, 7000),
          (3000, 9000), (8000, 9000), (12000, 9000), (17000, 9000)]

_FONT = "NanumGothic.ttf"


_WALL_T = 150            # 벽 두께(mm)
_GRID_X = [0, 8000, 14000, 20000]   # 기둥 그리드 X (X1..X4)
_GRID_Y = [0, 7000, 9000, 12000]    # 기둥 그리드 Y (Y1..Y4)


def _wall_body(msp, ax, ay, bx, by, t):
    """축정렬 벽선(ax,ay)-(bx,by) 을 두께 t 의 닫힌 사각(이중선 벽체)으로."""
    h = t / 2.0
    if abs(ay - by) < 1e-6:          # 수평 벽
        x0, x1 = sorted((ax, bx))
        ring = [(x0, ay - h), (x1, ay - h), (x1, ay + h), (x0, ay + h)]
    else:                            # 수직 벽
        y0, y1 = sorted((ay, by))
        ring = [(ax - h, y0), (ax + h, y0), (ax + h, y1), (ax - h, y1)]
    msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": "A-벽체"})


def make_korean_office(path: str = "sample_dxf/sample_office_kr.dxf") -> str:
    """한국형 사무소 평면도 DXF 생성(실무 CAD 근사) → 경로 반환.

    실구획(A-실구획) 닫힌 폴리라인 + 이중선 벽체 + 기둥 그리드(X1~/Y1~) + 치수 + 문 스윙.
    룸 폴리라인은 그리드 그대로라 면적·다운스트림 계산은 단순본과 동일.
    """
    from ezdxf.enums import TextEntityAlignment
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4          # mm
    if "FP_KR" not in doc.styles:
        doc.styles.add("FP_KR", font=_FONT)
    for name, color in (("A-벽체", 7), ("A-실구획", 8), ("A-기둥", 6),
                        ("그리드", 4), ("치수", 1), ("A-문", 3), ("문자", 7)):
        if name not in doc.layers:
            doc.layers.add(name, color=color)
    msp = doc.modelspace()

    # 1) 실구획(닫힌 폴리라인) + 실명/실번호
    for i, (name, x0, y0, x1, y1) in enumerate(_ROOMS, 1):
        msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                           close=True, dxfattribs={"layer": "A-실구획"})
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        t = msp.add_text(name, dxfattribs={"layer": "문자", "height": 350, "style": "FP_KR"})
        t.set_placement((cx, cy + 220), align=TextEntityAlignment.MIDDLE_CENTER)
        t2 = msp.add_text(f"R-{100 + i}", dxfattribs={"layer": "문자", "height": 230,
                                                      "style": "FP_KR", "color": 8})
        t2.set_placement((cx, cy - 220), align=TextEntityAlignment.MIDDLE_CENTER)

    # 2) 이중선 벽체 — 실 경계의 고유 변마다 벽체 생성(공유변 1회)
    edges = {}
    for _, x0, y0, x1, y1 in _ROOMS:
        for ax, ay, bx, by in [(x0, y0, x1, y0), (x1, y0, x1, y1),
                               (x1, y1, x0, y1), (x0, y1, x0, y0)]:
            key = tuple(sorted([(round(ax), round(ay)), (round(bx), round(by))]))
            edges[key] = (ax, ay, bx, by)
    for ax, ay, bx, by in edges.values():
        _wall_body(msp, ax, ay, bx, by, _WALL_T)

    # 3) 기둥(400×400) + 그리드 버블/라벨
    for gx in _GRID_X:
        for gy in _GRID_Y:
            msp.add_lwpolyline([(gx - 200, gy - 200), (gx + 200, gy - 200),
                                (gx + 200, gy + 200), (gx - 200, gy + 200)],
                               close=True, dxfattribs={"layer": "A-기둥"})
    for i, gx in enumerate(_GRID_X, 1):
        msp.add_line((gx, 12000), (gx, 13200), dxfattribs={"layer": "그리드"})
        msp.add_circle((gx, 13550), 350, dxfattribs={"layer": "그리드"})
        tt = msp.add_text(f"X{i}", dxfattribs={"layer": "그리드", "height": 300, "style": "FP_KR"})
        tt.set_placement((gx, 13550), align=TextEntityAlignment.MIDDLE_CENTER)
    for j, gy in enumerate(_GRID_Y, 1):
        msp.add_line((0, gy), (-1200, gy), dxfattribs={"layer": "그리드"})
        msp.add_circle((-1550, gy), 350, dxfattribs={"layer": "그리드"})
        tt = msp.add_text(f"Y{j}", dxfattribs={"layer": "그리드", "height": 300, "style": "FP_KR"})
        tt.set_placement((-1550, gy), align=TextEntityAlignment.MIDDLE_CENTER)

    # 4) 치수선(그리드 간격) — 상단 X, 좌측 Y
    ds = "EZDXF" if "EZDXF" in doc.dimstyles else "Standard"
    ov = {"dimtxt": 280, "dimasz": 200, "dimexe": 120, "dimexo": 120,
          "dimtxsty": "FP_KR", "dimlfac": 1.0, "dimdec": 0, "dimscale": 1.0}
    for i in range(len(_GRID_X) - 1):
        d = msp.add_linear_dim(base=(0, 14600), p1=(_GRID_X[i], 12000),
                               p2=(_GRID_X[i + 1], 12000), dimstyle=ds, override=ov,
                               dxfattribs={"layer": "치수"})
        d.render()
    for j in range(len(_GRID_Y) - 1):
        d = msp.add_linear_dim(base=(-3200, 0), p1=(0, _GRID_Y[j]), p2=(0, _GRID_Y[j + 1]),
                               angle=90, dimstyle=ds, override=ov, dxfattribs={"layer": "치수"})
        d.render()

    # 5) 문(개구부) — 도어스윙 호
    for dx, dy in _DOORS:
        msp.add_arc((dx, dy), 800, 0, 90, dxfattribs={"layer": "A-문"})
        msp.add_line((dx, dy), (dx + 800, dy), dxfattribs={"layer": "A-문"})

    # 6) 표제란(간이)
    tb = msp.add_text("○○빌딩 기준층 평면도  |  S=1/100  |  단위 mm",
                      dxfattribs={"layer": "문자", "height": 320, "style": "FP_KR"})
    tb.set_placement((10000, -4200), align=TextEntityAlignment.MIDDLE_CENTER)

    doc.saveas(path)
    return path


def make_korean_electrical(arch_dxf: str = "sample_dxf/sample_office_kr.dxf",
                           path: str = "sample_dxf/sample_office_elec.dxf",
                           n_branch: int = 6) -> str:
    """건축 DXF 와 짝이 되는 전기 DXF(천장 케이블트레이) 생성.

    실무처럼 복도 주간선(main tray)에서 각 실로 분기하되, 분기관이 스프링클러
    헤드를 관통하도록 배선해 **타공종 간섭(clash)** 데모를 만든다. 단위 mm, 레이어 'E-트레이'.
    """
    from . import dxf_loader as DL
    from . import placement as P
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    arch = DL.load(arch_dxf)
    cfg = P.PlaceConfig(structure="fireproof", occupancy="common", num_floors=5)
    layout = P.build_layout(arch.rooms, cfg, doors=arch.door_points)
    heads = [p.point for p in layout.placements.get("sprinkler", [])]   # 미터

    # 복도 주간선 y(복도 중앙 ≈ 8m), 분기 대상 헤드 선정(고르게)
    corridor_y = 8.0
    heads_sorted = sorted(heads, key=lambda h: (h[0], h[1]))
    picks = heads_sorted[:: max(1, len(heads_sorted) // max(n_branch, 1))][:n_branch]

    M = 1000.0   # m → mm
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    if "E-트레이" not in doc.layers:
        doc.layers.add("E-트레이", color=2)
    if "문자" not in doc.layers:
        doc.layers.add("문자", color=2)
    if "FP_KR" not in doc.styles:
        doc.styles.add("FP_KR", font=_FONT)
    msp = doc.modelspace()

    # 주간선(복도를 가로지르는 수평 트레이)
    msp.add_lwpolyline([(1.0 * M, corridor_y * M), (19.0 * M, corridor_y * M)],
                       dxfattribs={"layer": "E-트레이"})
    # 분기: 주간선 → 헤드 관통(수직강하)
    for hx, hy in picks:
        msp.add_lwpolyline([(hx * M, corridor_y * M), (hx * M, hy * M)],
                           dxfattribs={"layer": "E-트레이"})
    t = msp.add_text("전기 케이블트레이 (천장)", dxfattribs={
        "layer": "문자", "height": 300, "style": "FP_KR"})
    from ezdxf.enums import TextEntityAlignment
    t.set_placement((10000, 11500), align=TextEntityAlignment.MIDDLE_CENTER)

    doc.saveas(path)
    return path


if __name__ == "__main__":
    import sys
    which = sys.argv[1] if len(sys.argv) > 1 else "office"
    if which == "elec":
        print("생성:", make_korean_electrical())
    elif which.endswith(".dxf"):
        print("생성:", make_korean_office(which))
    else:
        a = make_korean_office()
        e = make_korean_electrical()
        print("생성:", a, "+", e)
