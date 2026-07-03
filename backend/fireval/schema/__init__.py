# -*- coding: utf-8 -*-
"""
fireval.schema — Phase 0 설계 산출물(객체 카테고리 · 규칙 ID · 라벨 포맷).

이 서브패키지는 데이터셋을 한 줄도 모으기 전에 "무엇을 어떤 라벨로 담을지"를
**머신리더블 + 사람가독** 두 형태로 확정한다(실행가이드 §1 Phase 0).

세 기둥:
  ① categories : 도면에서 인식할 객체들의 분류체계(class_id 고정)
  ② labels     : 객체검출 라벨(COCO/DXF-entity) + 위반판정 라벨(JSON 테이블)
  ③ rules      : 각 NFTC/건축법 조항을 고유 rule_id 로 쪼갠 카탈로그

세 기둥은 서로 ID 로 연결된다:
  ObjectLabel.category_key  ─▶ categories.CATEGORIES
  ViolationLabel.rule_id    ─▶ rules.RULE_CATALOG
  Rule.target_category      ─▶ categories.CATEGORIES   (규칙이 검사하는 객체)
"""
from . import categories, labels, rules   # noqa: F401

__all__ = ["categories", "labels", "rules"]
