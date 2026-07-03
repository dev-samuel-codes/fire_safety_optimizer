# -*- coding: utf-8 -*-
"""
dxf_ir — DXF/DWG → DrawingAnnotation(IR) 변환 (Phase 6).

룸/문은 fireopt.dxf_loader(닫힌 폴리라인→룸, $INSUNITS 단위환산) 재사용.
소방설비는 modelspace 의 INSERT(블록참조)를 카테고리로 매핑(벡터 정확):
  1) 블록명 정확매칭(fireopt.symbols 의 FP_* 블록)
  2) 실패 시 레이어/블록명 키워드 → categories.fire_categories 의 dxf_layers 매칭

건물 메타(structure/occupancy/detector_type)는 도면에 없으므로 **사용자 입력 파라미터**
(가이드: context 규칙은 건물 메타 필요). 기본 fireproof/common/smoke_12.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from fireopt import dxf_loader as DL
from fireopt import symbols as SYM
from ..schema import categories as CAT
from ..schema.labels import ObjectLabel, DrawingAnnotation
from ..engine import checks


# fireopt.symbols 블록명 → fireval 카테고리(정확매칭)
BLOCK_TO_CATEGORY = {
    SYM.SPRINKLER:    "sprinkler_head",
    SYM.SMOKE:        "smoke_detector",
    SYM.HEAT:         "heat_detector",
    SYM.HYDRANT:      "hydrant_box",
    SYM.EXTINGUISHER: "extinguisher",
    SYM.EXIT:         "exit_light",
    SYM.RISER:        "riser_alarm",
}


def _keyword_category(layer: str, block: str) -> str | None:
    """레이어명 키워드 → fire **점(point) 설비** 카테고리(정확 블록매칭 실패 시 폴백).

    실도면 교훈(산업단지 창고 DWG): 짧은 모호 키워드("f"/"sp"/"fd"/"e-")가 hex 블록명·
    무관 레이어(COL/WID/FIN)에 substring 매칭돼 대량 오탐 → 아래로 제한:
      · INSERT 는 점 심볼 → geometry=='point' 카테고리만(evac_route/pipe 등 선 카테고리 배제)
      · ASCII 2글자 이하 키워드 무시(한글 키워드는 2자도 허용: 경종/제연)
      · 블록 hex명(노이즈)이 아닌 **레이어명**으로만 매칭
    """
    lay = (layer or "").lower()
    for c in CAT.fire_categories():
        if c.geometry != "point":
            continue
        for kw in c.dxf_layers:
            k = (kw or "").lower()
            if not k:
                continue
            if len(k) < 3 and not any('가' <= ch <= '힣' for ch in k):
                continue                       # 짧은 모호 ASCII 키워드 제외
            if k in lay:
                return c.key
    return None


def extract_device_objects(doc, scale: float) -> list:
    """modelspace INSERT → 소방설비 ObjectLabel[]. 좌표는 scale 로 미터 환산."""
    msp = doc.modelspace()
    objs, n = [], 0
    for e in msp.query("INSERT"):
        name = getattr(e.dxf, "name", "") or ""
        layer = getattr(e.dxf, "layer", "") or ""
        cat = BLOCK_TO_CATEGORY.get(name) or _keyword_category(layer, name)
        if not cat or cat not in CAT.CATEGORIES:
            continue
        try:
            ins = e.dxf.insert
            x, y = float(ins[0]) * scale, float(ins[1]) * scale
        except Exception:
            continue
        objs.append(ObjectLabel(
            category_id=CAT.by_key(cat).id, category_key=cat,
            world_geometry=[round(x, 3), round(y, 3)],
            dxf_layer=layer, dxf_block=name,
            source="model_pred", object_id=f"dev-{n}"))
        n += 1
    return objs


def dxf_to_annotation(path: str, structure: str = "fireproof", occupancy: str = "common",
                      detector_type: str = "smoke_12", drawing_id: str = None) -> DrawingAnnotation:
    """DXF/DWG → DrawingAnnotation(룸+문+소방설비). check_drawing 에 바로 투입 가능."""
    bm = DL.load(path, structure=structure, occupancy=occupancy)
    doc = DL._open_doc(path)
    scale = bm.scale
    objs = []

    for i, r in enumerate(bm.rooms):
        ring = [[round(x, 3), round(y, 3)] for x, y in r.polygon.exterior.coords]
        objs.append(ObjectLabel(
            category_id=CAT.by_key("room").id, category_key="room",
            world_geometry=ring, dxf_layer="A-실구획",
            attributes={"name": r.name, "area_m2": round(r.area, 1)},
            source="model_pred", object_id=f"room-{i}"))
    for i, d in enumerate(bm.door_points):
        objs.append(ObjectLabel(
            category_id=CAT.by_key("door").id, category_key="door",
            world_geometry=[round(d.x, 3), round(d.y, 3)],
            source="model_pred", object_id=f"door-{i}"))
    objs += extract_device_objects(doc, scale)

    return DrawingAnnotation(
        drawing_id=drawing_id or os.path.splitext(os.path.basename(path))[0],
        source_file=path, units_scale=scale,
        building_meta={"structure": structure, "occupancy": occupancy,
                       "detector_type": detector_type},
        objects=objs, label_provenance="model_pred")


def ir_summary(ann: DrawingAnnotation) -> dict:
    """IR 구성 요약(카테고리별 객체 수)."""
    by_cat = {}
    for o in ann.objects:
        by_cat[o.category_key] = by_cat.get(o.category_key, 0) + 1
    return {"drawing_id": ann.drawing_id, "units_scale": ann.units_scale,
            "n_objects": len(ann.objects), "by_category": by_cat}


def ingest_and_check(path: str, **meta) -> tuple:
    """DXF → IR → 규칙엔진. (annotation, violations[ViolationLabel]) 반환."""
    ann = dxf_to_annotation(path, **meta)
    violations = checks.check_drawing(ann)
    ann.violations = violations
    return ann, violations


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DXF→IR 변환 + 규칙엔진 위반검출(Phase 6)")
    ap.add_argument("--dxf", required=True, help="입력 DXF/DWG 경로")
    ap.add_argument("--structure", default="fireproof", choices=["fireproof", "non_fireproof", "noncombustible"])
    ap.add_argument("--occupancy", default="common")
    ap.add_argument("--detector-type", dest="detector_type", default="smoke_12")
    ap.add_argument("--out", default="", help="주석 JSON 저장 경로(선택)")
    a = ap.parse_args(argv)
    ann, vios = ingest_and_check(a.dxf, structure=a.structure, occupancy=a.occupancy,
                                 detector_type=a.detector_type)
    summ = ir_summary(ann)
    print("=== IR 요약 ===")
    print(f"  도면: {summ['drawing_id']} (scale={summ['units_scale']} → m)")
    print(f"  객체 {summ['n_objects']}개: {summ['by_category']}")
    s = checks.summarize(vios)
    print("=== 규칙엔진 위반검출 ===")
    print(f"  판정 {s['checked']}건 중 위반 {s['violations']}건 · {s['by_severity']}")
    for v in vios:
        if v.status == "violation":
            print(f"   ✗ {v.rule_id} [{v.severity}] {v.description}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(ann.to_json())
        print(f"주석 → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
