# -*- coding: utf-8 -*-
"""
build_artifacts — Phase 0 스키마를 머신리더블 JSON 으로 내보내고 자기검증.

    python -m fireval.schema.build_artifacts            # json/ 에 산출 + 검증
    python -m fireval.schema.build_artifacts --check    # 검증만(파일 미생성, CI용)

산출(`fireval/schema/json/`):
  categories.json           객체 카테고리 표(class_id 고정)
  rules.json                규칙 카탈로그(constants 122개에서 도출)
  rules_stats.json          설비/checkability/check_type 요약
  object_label.schema.json  / violation_label.schema.json / drawing_annotation.schema.json
  coco_categories.json      COCO 'categories' 템플릿
"""
from __future__ import annotations

import json
import os
import sys

from . import categories as C
from . import rules as R
from . import labels as L

_OUT = os.path.join(os.path.dirname(__file__), "json")


def _dump(name: str, obj) -> str:
    os.makedirs(_OUT, exist_ok=True)
    path = os.path.join(_OUT, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def run_checks() -> list:
    """세 기둥 + 교차링크 자기검증. 문제 목록(빈 = OK)."""
    problems = []
    problems += [f"[categories] {p}" for p in C.validate()]
    problems += [f"[rules] {p}" for p in R.validate()]
    problems += [f"[labels] {p}" for p in L.validate()]
    return problems


def build() -> dict:
    paths = {}
    paths["categories"] = _dump("categories.json", {
        "groups": dict(C.GROUPS),
        "geometry_types": list(C.GEOMETRY_TYPES),
        "recognize_sources": list(C.RECOGNIZE_SOURCES),
        "categories": [c.to_dict() for c in C.all_categories()],
    })
    paths["rules"] = _dump("rules.json", {
        "facility_meta": {k: {"abbr": v[0], "standard": v[1], "name_ko": v[2]}
                          for k, v in R.FACILITY_META.items()},
        "check_types": list(R.CHECK_TYPES),
        "checkability": list(R.CHECKABILITY),
        "rules": [r.to_dict() for r in R.RULES],
    })
    paths["rules_stats"] = _dump("rules_stats.json", R.stats())
    paths["object_schema"] = _dump("object_label.schema.json", L.object_label_schema())
    paths["violation_schema"] = _dump("violation_label.schema.json", L.violation_label_schema())
    paths["annotation_schema"] = _dump("drawing_annotation.schema.json",
                                       L.drawing_annotation_schema())
    paths["coco_categories"] = _dump("coco_categories.json", L.coco_categories())
    return paths


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    problems = run_checks()
    if problems:
        print("❌ 자기검증 실패:")
        for p in problems:
            print("  -", p)
        return 1
    print("✅ 자기검증 통과")
    st = R.stats()
    print(f"   카테고리 {len(C.all_categories())}종 / 규칙 {st['total']}개")
    print(f"   checkability: {st['by_checkability']}")
    if "--check" in argv:
        return 0
    paths = build()
    print("📦 산출:")
    for k, p in paths.items():
        print(f"   {k:18s} → {os.path.relpath(p)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
