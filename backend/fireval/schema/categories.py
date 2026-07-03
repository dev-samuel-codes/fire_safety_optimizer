# -*- coding: utf-8 -*-
"""
categories — 객체 카테고리 분류체계 (실행가이드 Phase 0 ①).

도면에서 **인식(detect/segment)** 할 대상의 고정 분류표. class_id 는 한 번 정하면
바꾸지 않는다(라벨·모델 가중치가 이 정수에 묶이므로). 새 클래스는 그룹 내 빈 번호로 추가.

각 카테고리는 다음을 들고 있어 합성 생성기(Phase 3)·DXF 파서(Phase 6)·전문가
라벨링(Phase 4)이 동일한 정의를 공유한다:
  · group         : 상위 묶음(building/fire_*/mep/annotation)
  · geometry      : 라벨 기하 종류(point/polyline/polygon/arc/text/dimension)
  · dxf_layers    : 실무·합성 DXF 에서 이 객체가 보통 놓이는 레이어 키워드
                    (fireopt.dxf_loader 의 _ROOM_KEYS 등과 정렬)
  · dxf_block     : fireopt.symbols 의 블록명(있으면) — 합성 GT 의 INSERT 근거
  · symbol_desc   : 도면상 도시기호 모양(소방청 고시 별표 준용)
  · facility      : 연관 fireopt 설비키(규칙 매핑용; rules.target_category 와 교차검증)
  · recognize_via : 이 객체 라벨을 어느 데이터 경로에서 얻나(가이드 Phase 1~4)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType


# 라벨 기하 종류 — COCO 는 bbox/segmentation, 벡터는 entity 기준. point 는 1px bbox 로도 표현.
GEOMETRY_TYPES = ("point", "arc", "polyline", "polygon", "text", "dimension")

# 인식 데이터 출처(가이드 Phase): 합성(3), 공개데이터(1), 한국실도면(2), 전문가(4)
RECOGNIZE_SOURCES = ("synthetic", "public_dataset", "real_drawing", "expert")


@dataclass(frozen=True)
class Category:
    """객체 카테고리 1종(불변)."""
    id: int                       # COCO/YOLO class id (고정)
    key: str                      # 안정 슬러그(코드·라벨 상호참조 키)
    name_ko: str                  # 한국어 표기
    group: str                    # 상위 그룹
    geometry: str                 # GEOMETRY_TYPES 중 하나
    dxf_layers: tuple = ()        # 레이어 이름 키워드(소문자 부분일치)
    dxf_block: str = ""           # fireopt.symbols 블록명(있으면)
    symbol_desc: str = ""         # 도시기호 모양 설명
    facility: str = ""            # fireopt 설비키(sprinkler/detector/...) 또는 ""
    recognize_via: tuple = ()     # RECOGNIZE_SOURCES 부분집합
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "key": self.key, "name_ko": self.name_ko,
            "group": self.group, "geometry": self.geometry,
            "dxf_layers": list(self.dxf_layers), "dxf_block": self.dxf_block,
            "symbol_desc": self.symbol_desc, "facility": self.facility,
            "recognize_via": list(self.recognize_via), "notes": self.notes,
        }


# 상위 그룹 정의(설명·표시색 힌트)
GROUPS = MappingProxyType({
    "building":      "건축 요소(벽·기둥·문·창·계단·실·복도)",
    "fire_detect":   "화재탐지(감지기·발신기·경계구역)",
    "fire_suppress": "소화(스프링클러·소화전·소화기·배관)",
    "fire_evac":     "피난(유도등·피난기구·방화문·동선·제연)",
    "fire_alarm":    "경보(경종·사이렌)",
    "mep":           "전기/기계(충돌검토 대상)",
    "annotation":    "도면 주기(실명·치수·표제란)",
})


# ──────────────────────────────────────────────────────────────────────────
# 카테고리 표 — id 는 그룹별 10단위로 띄워 확장 여지 확보. 절대 재배정 금지.
# ──────────────────────────────────────────────────────────────────────────
_CATEGORIES: tuple = (
    # ── 건축(1x) ──────────────────────────────────────────────────────────
    Category(1, "wall", "벽체", "building", "polygon",
             dxf_layers=("벽", "wall", "a-벽", "구조", "외벽", "내벽"),
             symbol_desc="이중선 또는 해치 채움",
             recognize_via=("synthetic", "public_dataset", "real_drawing")),
    Category(2, "column", "기둥", "building", "polygon",
             dxf_layers=("기둥", "column", "a-기둥"),
             symbol_desc="채운 사각/원 + 그리드 교점",
             recognize_via=("synthetic", "real_drawing")),
    Category(3, "door", "문(개폐방향)", "building", "arc",
             dxf_layers=("문", "door", "a-문"),
             symbol_desc="개구부 + 도어스윙 호(90°)",
             recognize_via=("synthetic", "public_dataset", "real_drawing"),
             notes="피난 그래프의 통로 연결점. 스윙 호로 개폐방향 파악."),
    Category(4, "window", "창호", "building", "polyline",
             dxf_layers=("창", "창호", "window"),
             symbol_desc="벽 개구부 + 평행 2~4선",
             recognize_via=("synthetic", "public_dataset", "real_drawing")),
    Category(5, "stair", "계단", "building", "polygon",
             dxf_layers=("계단", "stair", "계단실"),
             symbol_desc="평행 디딤판 선 + 진행방향 화살표",
             facility="evacuation",
             recognize_via=("synthetic", "real_drawing"),
             notes="직통계단 보행거리·2개소 요구의 기준점."),
    Category(6, "room", "실(室) 경계", "building", "polygon",
             dxf_layers=("실구획", "실", "room", "space", "area", "거실", "zone"),
             symbol_desc="닫힌 폴리라인(구획선)",
             recognize_via=("synthetic", "public_dataset", "real_drawing"),
             notes="면적·구획수 산정 단위. 닫힌 폴리라인 1개 = 1실."),
    Category(7, "corridor", "복도", "building", "polygon",
             dxf_layers=("복도", "corridor"),
             symbol_desc="긴 통로형 실",
             facility="evacuation",
             recognize_via=("synthetic", "real_drawing"),
             notes="유효너비·보행거리 그래프의 간선. room 의 특수형."),
    Category(8, "grid_axis", "기둥 그리드축", "building", "polyline",
             dxf_layers=("그리드", "grid", "축"),
             symbol_desc="일점쇄선 + 원형 버블(X1/Y1)",
             recognize_via=("synthetic", "real_drawing")),

    # ── 화재탐지(1xx) ────────────────────────────────────────────────────
    Category(110, "smoke_detector", "연기감지기", "fire_detect", "point",
             dxf_layers=("감지기", "detector", "fp_smoke"), dxf_block="FP_SMOKE",
             symbol_desc="사각 + 'S' (소방청 고시 별표)", facility="detector",
             recognize_via=("synthetic", "public_dataset", "real_drawing", "expert")),
    Category(111, "heat_detector", "열감지기", "fire_detect", "point",
             dxf_layers=("감지기", "detector", "fp_heat"), dxf_block="FP_HEAT",
             symbol_desc="평평한 윗변 + 하반원 돔(차동/정온 스포트형)", facility="detector",
             recognize_via=("synthetic", "real_drawing", "expert")),
    Category(112, "detector_linear", "감지선형/공기관", "fire_detect", "polyline",
             dxf_layers=("공기관", "감지선", "linear"),
             symbol_desc="실선(공기관식)·점선(감지선형)", facility="detector",
             recognize_via=("synthetic", "expert")),
    Category(113, "manual_call", "발신기(P형)", "fire_detect", "point",
             dxf_layers=("발신기", "수동발신기", "call"),
             symbol_desc="원 + 'P' (수동발신기)", facility="detector",
             recognize_via=("real_drawing", "expert")),
    Category(114, "detection_zone", "경계구역", "fire_detect", "polygon",
             dxf_layers=("경계구역", "zone"),
             symbol_desc="굵은 일점쇄선 구획 + 구역번호", facility="detector",
             recognize_via=("synthetic", "expert"),
             notes="600/1000㎡·변 50m 상위 제약(NFTC 203 2.1.1) 검사 단위."),

    # ── 소화(2xx) ────────────────────────────────────────────────────────
    Category(210, "sprinkler_head", "스프링클러 헤드", "fire_suppress", "point",
             dxf_layers=("스프링클러", "sprinkler", "헤드", "fp_sprinkler"),
             dxf_block="FP_SPRINKLER",
             symbol_desc="원 + 내부 십자(⊕)", facility="sprinkler",
             recognize_via=("synthetic", "public_dataset", "real_drawing", "expert")),
    Category(211, "hydrant_box", "옥내소화전함", "fire_suppress", "point",
             dxf_layers=("소화전", "hydrant", "fp_hydrant"), dxf_block="FP_HYDRANT",
             symbol_desc="사각 함체 + 좌상 삼각 반채움", facility="hydrant",
             recognize_via=("synthetic", "real_drawing", "expert")),
    Category(212, "extinguisher", "소화기", "fire_suppress", "point",
             dxf_layers=("소화기", "extinguisher", "fp_extinguisher"),
             dxf_block="FP_EXTINGUISHER",
             symbol_desc="원 + '소' (정삼각+F 통용기호도 가능)", facility="extinguisher",
             recognize_via=("synthetic", "public_dataset", "real_drawing", "expert")),
    Category(213, "riser_alarm", "입상관/알람밸브", "fire_suppress", "point",
             dxf_layers=("입상관", "알람밸브", "riser", "fp_riser"), dxf_block="FP_RISER",
             symbol_desc="원 + 채운 위향 삼각(습식)", facility="sprinkler",
             recognize_via=("synthetic", "expert"),
             notes="계통 기점. 습식=solid 채움/건식=미채움."),
    Category(214, "sprinkler_pipe", "스프링클러 배관", "fire_suppress", "polyline",
             dxf_layers=("배관", "pipe", "sp", "가지배관"),
             symbol_desc="굵은 실선 + 구경 주기(예 65A)", facility="sprinkler",
             recognize_via=("synthetic", "real_drawing", "expert"),
             notes="담당 헤드수→관경(NFTC 103 별표) 검사 대상."),
    Category(215, "hydrant_pipe", "소화전 배관", "fire_suppress", "polyline",
             dxf_layers=("소화전배관", "hydrant pipe"),
             symbol_desc="굵은 실선 + 구경 주기", facility="hydrant",
             recognize_via=("synthetic", "expert")),

    # ── 피난(3xx) ────────────────────────────────────────────────────────
    Category(310, "exit_light", "피난구유도등", "fire_evac", "point",
             dxf_layers=("유도등", "피난구", "exit", "fp_exit"), dxf_block="FP_EXIT",
             symbol_desc="원/사각 + 세로 나비넥타이(피난구)", facility="evacuation",
             recognize_via=("synthetic", "real_drawing", "expert")),
    Category(311, "directional_light", "통로유도등", "fire_evac", "point",
             dxf_layers=("통로유도등", "directional"),
             symbol_desc="사각 + 방향 화살표(러닝맨)", facility="evacuation",
             recognize_via=("real_drawing", "expert")),
    Category(312, "escape_device", "피난기구(완강기 등)", "fire_evac", "point",
             dxf_layers=("완강기", "피난기구", "escape"),
             symbol_desc="원 + 약호(완강기 등)", facility="evacuation",
             recognize_via=("synthetic", "expert"),
             notes="개구부 0.5×1.0m·적응층 3~10층(NFTC 301) 검사."),
    Category(313, "emergency_light", "비상조명등", "fire_evac", "point",
             dxf_layers=("비상조명", "emergency light"),
             symbol_desc="원 안 빗금/사각 안 ×", facility="evacuation",
             recognize_via=("real_drawing", "expert")),
    Category(314, "fire_door", "방화문", "fire_evac", "polyline",
             dxf_layers=("방화문", "fd", "fire door"),
             symbol_desc="문 + 방화등급 표기(갑종/을종, FD)", facility="evacuation",
             recognize_via=("synthetic", "real_drawing", "expert")),
    Category(315, "evac_route", "피난동선", "fire_evac", "polyline",
             dxf_layers=("피난", "동선", "evac route", "f"),
             symbol_desc="화살표 경로선(피난방향)", facility="evacuation",
             recognize_via=("synthetic", "expert"),
             notes="가이드의 도면 'F' 라벨 경로. 직통계단까지 보행거리 산정선."),
    Category(316, "smoke_control", "제연설비", "fire_evac", "point",
             dxf_layers=("제연", "급기", "배기", "smoke control"),
             symbol_desc="급/배기 댐퍼 사각 + 화살표", facility="evacuation",
             recognize_via=("real_drawing", "expert")),

    # ── 경보(4xx) ────────────────────────────────────────────────────────
    Category(410, "alarm_bell", "경종/사이렌", "fire_alarm", "point",
             dxf_layers=("경종", "사이렌", "bell", "sounder"),
             symbol_desc="반원 안 빗금(경종)/원+물결(사이렌)", facility="detector",
             recognize_via=("real_drawing", "expert")),

    # ── 전기/기계 — 충돌검토(5xx) ────────────────────────────────────────
    Category(510, "cable_tray", "케이블트레이", "mep", "polyline",
             dxf_layers=("e-트레이", "트레이", "tray", "e-"),
             symbol_desc="이중 평행선(천장 직하 배선)", facility="",
             recognize_via=("synthetic", "real_drawing"),
             notes="fireopt.clash 의 z-밴드 간섭검토 대상. 소방기기 평면교차=충돌."),
    Category(511, "elec_conduit", "전선관/배선", "mep", "polyline",
             dxf_layers=("전선", "conduit", "배선", "e-"),
             symbol_desc="가는 실선 + 회로기호",
             recognize_via=("real_drawing",)),

    # ── 주기(6xx) ────────────────────────────────────────────────────────
    Category(610, "room_label", "실명/실번호", "annotation", "text",
             dxf_layers=("문자", "text", "주기"),
             symbol_desc="TEXT/MTEXT(실명 + R-101 등 실번호)",
             recognize_via=("synthetic", "real_drawing"),
             notes="room 폴리곤에 실명 매칭(dxf_loader._name_for)."),
    Category(611, "dimension", "치수", "annotation", "dimension",
             dxf_layers=("치수", "dim"),
             symbol_desc="치수선 + 치수보조선 + 치수문자",
             recognize_via=("synthetic", "real_drawing")),
    Category(612, "titleblock", "표제란", "annotation", "polyline",
             dxf_layers=("표제란", "title", "titleblock"),
             symbol_desc="우하단 박스(도면명·축척·단위)",
             recognize_via=("real_drawing",)),
)


# 조회 인덱스(불변 뷰)
CATEGORIES = MappingProxyType({c.key: c for c in _CATEGORIES})
CATEGORIES_BY_ID = MappingProxyType({c.id: c for c in _CATEGORIES})


def by_key(key: str) -> Category:
    return CATEGORIES[key]


def by_id(cid: int) -> Category:
    return CATEGORIES_BY_ID[cid]


def in_group(group: str) -> list:
    return [c for c in _CATEGORIES if c.group == group]


def all_categories() -> list:
    return list(_CATEGORIES)


def fire_categories() -> list:
    """소방 관련(검증 핵심) 카테고리 — 건축/주기 제외."""
    return [c for c in _CATEGORIES if c.group.startswith("fire_")]


def validate() -> list:
    """자기검증: id/key 유일성, geometry·group·source 도메인 준수. 문제 목록 반환(빈 = OK)."""
    problems: list = []
    seen_id, seen_key = set(), set()
    for c in _CATEGORIES:
        if c.id in seen_id:
            problems.append(f"중복 class_id: {c.id} ({c.key})")
        if c.key in seen_key:
            problems.append(f"중복 key: {c.key}")
        seen_id.add(c.id)
        seen_key.add(c.key)
        if c.geometry not in GEOMETRY_TYPES:
            problems.append(f"{c.key}: 잘못된 geometry '{c.geometry}'")
        if c.group not in GROUPS:
            problems.append(f"{c.key}: 알 수 없는 group '{c.group}'")
        for s in c.recognize_via:
            if s not in RECOGNIZE_SOURCES:
                problems.append(f"{c.key}: 알 수 없는 recognize_via '{s}'")
    return problems
