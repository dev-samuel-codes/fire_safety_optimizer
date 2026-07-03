# -*- coding: utf-8 -*-
"""
fireval — 소방·건축 법규 **검증(validation)** 시스템 + 도면 **데이터셋 구축** 패키지.

`fireopt`(소방시설 자동배치·최적화)와 별개의 보완 모듈이다. 목표는
"도면(.dwg/이미지/.dxf) 업로드 → ① 소방·건축 법규 위반 검출 → ② 개선안 제시"이며,
그 연료인 **라벨 데이터셋**을 단계별로 구축한다(실행가이드 Phase 0~7).

현재 구현 범위(Phase 0 — 설계 확정):
  fireval.schema.categories  : 객체 카테고리 분류체계  (가이드 Phase 0 ①)
  fireval.schema.labels      : 표준 라벨 포맷(객체검출+위반판정) (가이드 Phase 0 ②)
  fireval.schema.rules       : 규칙 ID 체계 — fireopt.constants 122개에서 도출 (가이드 Phase 0 ③)

설계 원칙:
  · 규칙 값은 만들지 않는다. fireopt.constants(검증된 단일 출처)의 **뷰**일 뿐이다.
  · 카테고리 ↔ 규칙 ↔ 라벨이 ID로 연결된 하나의 그래프를 이룬다.
  · 위반 '판정'은 학습이 아니라 규칙 엔진(Phase 7)이 한다. 학습은 '무엇이 어디 있나'만.
"""

__version__ = "0.0.1"
