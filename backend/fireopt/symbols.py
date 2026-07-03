# -*- coding: utf-8 -*-
"""
symbols — 소방 표준기호를 ezdxf BLOCK 정의로 등록(실무 심볼 라이브러리 모사).

실무 도면과 동일하게, 각 소방기호를 **재사용 가능한 블록**으로 1회 정의하고
도면에는 INSERT(블록참조)로 꽂는다. 블록 내부 엔티티는 모두 레이어 "0" +
color BYBLOCK(0) 으로 그려서, INSERT 가 놓인 레이어의 색/선가중치를 그대로 상속한다
(= AutoCAD 표준 심볼 라이브러리 관례).

기호 규약(KS C / 소방 도시기호 통용):
  스프링클러 헤드   원 + 내부 십자(⊕)
  연기감지기        원 + 'S'
  열(차동식)감지기  사각 + 'ΔT'         (분포형/스포트 구분 없이 대표기호)
  옥내소화전        사각 함체 + 대각선 + 방수구 점
  소화기            정삼각형 + 내부 'F'
  피난구(유도등)    사각 + 러닝맨 화살표 + 'EXIT'
  입상관/알람밸브   원 + 십자 + 굵은 외곽 (계통 기점)
좌표계: 미터(모델공간). 기호 크기는 평면도에서 식별 가능한 실측치(0.1~0.6 m)로 고정.
"""
from __future__ import annotations

import math

# 블록 이름 상수(외부에서 참조)
SPRINKLER = "FP_SPRINKLER"
SMOKE = "FP_SMOKE"
HEAT = "FP_HEAT"
HYDRANT = "FP_HYDRANT"
EXTINGUISHER = "FP_EXTINGUISHER"
EXIT = "FP_EXIT"
RISER = "FP_RISER"

# 블록 내부 엔티티 공통 속성: 레이어 0 + BYBLOCK 색 → INSERT 레이어 상속
_BB = {"layer": "0", "color": 0}        # color 0 = BYBLOCK
TEXT_STYLE = "FP_KR"                      # 한글 가능 트루타입 스타일(도면 빌더가 생성)


def _txt(blk, s, x, y, h, *, style=TEXT_STYLE):
    """블록 중앙정렬 텍스트(기호 내부 문자)."""
    t = blk.add_text(s, dxfattribs={"height": h, "style": style, "color": 0})
    # ezdxf: 중앙정렬
    try:
        from ezdxf.enums import TextEntityAlignment
        t.set_placement((x, y), align=TextEntityAlignment.MIDDLE_CENTER)
    except Exception:
        t.dxf.insert = (x, y)
    return t


def define_blocks(doc, text_style: str = TEXT_STYLE):
    """문서에 모든 소방 기호 블록을 1회 정의(이미 있으면 건너뜀)."""
    global TEXT_STYLE
    TEXT_STYLE = text_style

    def _new(name):
        if name in doc.blocks:
            return None
        return doc.blocks.new(name=name)

    # --- 스프링클러 헤드: 원 + 십자 ---
    b = _new(SPRINKLER)
    if b is not None:
        r = 0.13
        b.add_circle((0, 0), r, dxfattribs=_BB)
        b.add_line((-r, 0), (r, 0), dxfattribs=_BB)
        b.add_line((0, -r), (0, r), dxfattribs=_BB)

    # --- 연기감지기(법정 도시기호): 사각 + S ---
    b = _new(SMOKE)
    if b is not None:
        s = 0.17
        b.add_lwpolyline([(-s, -s), (s, -s), (s, s), (-s, s)], close=True, dxfattribs=_BB)
        _txt(b, "S", 0, 0, s * 1.3)

    # --- 차동·정온식 스포트형 열감지기(법정): 평평한 윗변 + 둥근 바닥 돔 ---
    b = _new(HEAT)
    if b is not None:
        r = 0.17
        b.add_line((-r, 0), (r, 0), dxfattribs=_BB)    # 평평한 윗변
        b.add_arc((0, 0), r, 180, 360, dxfattribs=_BB) # 하반원(둥근 바닥)

    # --- 옥내소화전함(법정): 사각 + 대각 반채움(좌상 삼각) ---
    b = _new(HYDRANT)
    if b is not None:
        w, h = 0.26, 0.20
        b.add_lwpolyline([(-w, -h), (w, -h), (w, h), (-w, h)], close=True, dxfattribs=_BB)
        hatch = b.add_hatch(color=0)                    # BYBLOCK 반채움(좌상 삼각: 좌상·우상·좌하)
        hatch.paths.add_polyline_path([(-w, h), (w, h), (-w, -h)], is_closed=True)

    # --- 소화기(통용): 원 + 소 ---
    b = _new(EXTINGUISHER)
    if b is not None:
        r = 0.17
        b.add_circle((0, 0), r, dxfattribs=_BB)
        _txt(b, "소", 0, 0, r * 1.0)

    # --- 피난구유도등(법정): 원 + 세로 나비넥타이 ---
    b = _new(EXIT)
    if b is not None:
        r = 0.20
        b.add_circle((0, 0), r, dxfattribs=_BB)
        bx, by = r * 0.5, r * 0.62
        b.add_lwpolyline([(-bx, by), (0, 0), (bx, by)], close=True, dxfattribs=_BB)   # 위 삼각
        b.add_lwpolyline([(-bx, -by), (0, 0), (bx, -by)], close=True, dxfattribs=_BB)  # 아래 삼각

    # --- 입상관/경보밸브(습식, 법정): 원 + 채운 위향 삼각 ---
    b = _new(RISER)
    if b is not None:
        r = 0.20
        b.add_circle((0, 0), r, dxfattribs=_BB)
        t = r * 0.62
        tri = [(0, t), (t * 0.87, -t * 0.5), (-t * 0.87, -t * 0.5)]
        b.add_lwpolyline(tri, close=True, dxfattribs=_BB)
        htri = b.add_hatch(color=0)                     # 습식=solid 채움(미채움이면 건식)
        htri.paths.add_polyline_path(tri, is_closed=True)


# 시설(facility) → (블록명, 삽입 레이어) 매핑. detector 는 종별로 분기.
def block_for(facility: str, kind: str = "") -> str:
    if facility == "sprinkler":
        return SPRINKLER
    if facility == "detector":
        return HEAT if str(kind).startswith(("diff", "fixed", "heat")) else SMOKE
    if facility == "hydrant":
        return HYDRANT
    if facility == "extinguisher":
        return EXTINGUISHER
    if facility == "evacuation":
        return EXIT
    return SPRINKLER
