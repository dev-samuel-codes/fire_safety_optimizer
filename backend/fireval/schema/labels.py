# -*- coding: utf-8 -*-
"""
labels — 표준 라벨 포맷 (실행가이드 Phase 0 ②).

두 종류의 라벨을 분리한다(가이드 원칙):
  ① ObjectLabel    : "도면 어디에 무엇이 있나" — 인식 모델 학습용(객체검출/세그멘테이션).
                     COCO bbox/segmentation 과 호환 + 벡터 도면용 dxf_entity 핸들 보존.
  ② ViolationLabel : "어느 규칙을 어겼나" — 규칙 엔진(Phase 7) 산출/전문가 판정.
                     가이드의 위반 테이블 스키마({drawing_id, rule_id, status, evidence ...}).

한 도면의 전체 주석은 DrawingAnnotation 에 묶는다(객체 + 위반 + 메타).
좌표 규약: pixel_bbox 는 래스터(px), geometry(world) 는 미터(평면 실측). 둘 다 선택적.

직렬화는 표준 라이브러리만 사용(json). 외부 검증기 없이도 JSON Schema(draft-07)를
함께 제공해 어떤 도구(Label Studio/CVAT/스크립트)로도 검증 가능하게 한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

from . import categories as C


# 라벨 출처 — 합성 GT(완벽) / 모델 예측 / 전문가 / 규칙엔진
LABEL_SOURCES = ("synthetic_gt", "model_pred", "expert", "rule_engine")
# 위반 판정 상태
VIOLATION_STATUS = ("violation", "compliant", "not_applicable", "needs_review")


@dataclass
class ObjectLabel:
    """도면 속 객체 1개의 검출 라벨."""
    category_id: int                       # categories.Category.id
    category_key: str                      # categories.Category.key (가독·교차검증용 이중기재)
    # 래스터 좌표(px) — COCO 호환 [x, y, w, h]. 벡터-only 라벨이면 None.
    pixel_bbox: list = None
    segmentation: list = field(default_factory=list)   # COCO polygon [[x1,y1,x2,y2,...]]
    # 월드 좌표(m) — 벡터 도면 기하. point=[x,y] / polyline·polygon=[[x,y],...]
    world_geometry: list = field(default_factory=list)
    # 벡터 도면 추적성 — DXF 엔티티 핸들/레이어/블록(가이드: 벡터는 entity ID 기준 라벨)
    dxf_handle: str = ""
    dxf_layer: str = ""
    dxf_block: str = ""
    attributes: dict = field(default_factory=dict)     # 종별 등(예 detector_kind="smoke_12")
    source: str = "synthetic_gt"
    object_id: str = ""                                 # 도면 내 유일 id(위반 evidence 참조용)

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, "", [], {})}


@dataclass
class ViolationLabel:
    """규칙 1건에 대한 판정 라벨(가이드 위반 테이블 스키마)."""
    rule_id: str                           # rules.Rule.rule_id
    status: str                            # VIOLATION_STATUS
    description: str = ""                  # 사람이 읽는 위반/적합 사유
    # 근거 기하(가이드 evidence_geometry) — 위반을 보여주는 점·선·영역(m)
    evidence_geometry: list = field(default_factory=list)
    evidence_object_ids: list = field(default_factory=list)   # 관련 ObjectLabel.object_id
    measured_value: object = None          # 측정값(예 감지기 간 2.8m)
    required_value: object = None          # 기준값(rules.Rule.value)
    unit: str = ""
    severity: str = ""                     # rules.Rule.severity 복사(필터 편의)
    source: str = "rule_engine"            # LABEL_SOURCES

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, "", [], {})}


@dataclass
class DrawingAnnotation:
    """도면 1장의 전체 주석(객체 + 위반 + 메타)."""
    drawing_id: str
    source_file: str = ""                  # 원본 .dxf/.png 경로(상대)
    units_scale: float = 1.0               # 모델단위→m 환산(dxf_loader.scale 과 동일 의미)
    image_size: list = field(default_factory=list)   # 래스터면 [w, h] px
    building_meta: dict = field(default_factory=dict)  # 구조/용도/층수 등(context 규칙용)
    objects: list = field(default_factory=list)        # ObjectLabel[]
    violations: list = field(default_factory=list)     # ViolationLabel[]
    label_provenance: str = "synthetic_gt"             # 이 도면 라벨의 주 출처

    def to_dict(self) -> dict:
        return {
            "drawing_id": self.drawing_id,
            "source_file": self.source_file,
            "units_scale": self.units_scale,
            "image_size": self.image_size,
            "building_meta": self.building_meta,
            "label_provenance": self.label_provenance,
            "objects": [o.to_dict() for o in self.objects],
            "violations": [v.to_dict() for v in self.violations],
        }

    def to_json(self, **kw) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, **kw)


# ── COCO 호환 내보내기 ──────────────────────────────────────────────────────
def coco_categories() -> list:
    """categories 표 → COCO 'categories' 배열(supercategory=group)."""
    return [{"id": c.id, "name": c.key, "supercategory": c.group}
            for c in C.all_categories()]


def to_coco(annotations: list) -> dict:
    """DrawingAnnotation[] → COCO detection dict(인식 모델 학습용).

    위반 라벨은 COCO 에 담지 않는다(별도 violations 테이블 — 가이드 원칙).
    """
    images, coco_ann = [], []
    ann_id = 1
    for img_id, ann in enumerate(annotations, 1):
        w, h = (ann.image_size + [0, 0])[:2]
        images.append({"id": img_id, "file_name": ann.source_file,
                       "width": w, "height": h, "drawing_id": ann.drawing_id})
        for o in ann.objects:
            if not o.pixel_bbox:
                continue            # 래스터 bbox 없는 벡터-only 라벨은 COCO 제외
            x, y, bw, bh = o.pixel_bbox
            coco_ann.append({
                "id": ann_id, "image_id": img_id, "category_id": o.category_id,
                "bbox": [x, y, bw, bh], "area": float(bw) * float(bh),
                "iscrowd": 0, "segmentation": o.segmentation or [],
                "object_id": o.object_id, "attributes": o.attributes,
            })
            ann_id += 1
    return {"images": images, "annotations": coco_ann, "categories": coco_categories()}


# ── JSON Schema(draft-07) — 외부 도구 검증용 ────────────────────────────────
def object_label_schema() -> dict:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "ObjectLabel",
        "type": "object",
        "required": ["category_id", "category_key"],
        "properties": {
            "category_id": {"type": "integer"},
            "category_key": {"type": "string"},
            "pixel_bbox": {"type": "array", "items": {"type": "number"},
                           "minItems": 4, "maxItems": 4,
                           "description": "COCO [x,y,w,h] px"},
            "segmentation": {"type": "array"},
            "world_geometry": {"type": "array", "description": "월드좌표(m) 점/선/면"},
            "dxf_handle": {"type": "string"},
            "dxf_layer": {"type": "string"},
            "dxf_block": {"type": "string"},
            "attributes": {"type": "object"},
            "source": {"enum": list(LABEL_SOURCES)},
            "object_id": {"type": "string"},
        },
        "additionalProperties": False,
    }


def violation_label_schema() -> dict:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "ViolationLabel",
        "type": "object",
        "required": ["rule_id", "status"],
        "properties": {
            "rule_id": {"type": "string", "pattern": "^FV-(SPK|DET|EXT|HYD|EVA)-.+$"},
            "status": {"enum": list(VIOLATION_STATUS)},
            "description": {"type": "string"},
            "evidence_geometry": {"type": "array"},
            "evidence_object_ids": {"type": "array", "items": {"type": "string"}},
            "measured_value": {},
            "required_value": {},
            "unit": {"type": "string"},
            "severity": {"enum": ["critical", "major", "minor", "info", ""]},
            "source": {"enum": list(LABEL_SOURCES)},
        },
        "additionalProperties": False,
    }


def drawing_annotation_schema() -> dict:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "DrawingAnnotation",
        "type": "object",
        "required": ["drawing_id"],
        "properties": {
            "drawing_id": {"type": "string"},
            "source_file": {"type": "string"},
            "units_scale": {"type": "number"},
            "image_size": {"type": "array", "items": {"type": "number"}},
            "building_meta": {"type": "object"},
            "label_provenance": {"enum": list(LABEL_SOURCES)},
            "objects": {"type": "array", "items": object_label_schema()},
            "violations": {"type": "array", "items": violation_label_schema()},
        },
        "additionalProperties": False,
    }


def validate() -> list:
    """자기검증: 더미 주석 1건을 만들어 round-trip + COCO 변환이 깨지지 않는지."""
    problems: list = []
    cat = C.by_key("smoke_detector")
    obj = ObjectLabel(category_id=cat.id, category_key=cat.key,
                      pixel_bbox=[10, 10, 8, 8], world_geometry=[3.0, 4.0],
                      dxf_block=cat.dxf_block, object_id="o1",
                      attributes={"detector_kind": "smoke_12"})
    vio = ViolationLabel(rule_id="FV-DET-smoke_12_lt4", status="violation",
                         evidence_object_ids=["o1"], measured_value=180,
                         required_value=150, unit="m2", severity="critical")
    ann = DrawingAnnotation(drawing_id="d1", source_file="d1.png",
                            image_size=[256, 256], objects=[obj], violations=[vio])
    try:
        s = ann.to_json()
        back = json.loads(s)
        if back["objects"][0]["category_key"] != "smoke_detector":
            problems.append("round-trip: category_key 손상")
        coco = to_coco([ann])
        if len(coco["annotations"]) != 1:
            problems.append("to_coco: annotation 개수 불일치")
        if not any(c["id"] == cat.id for c in coco["categories"]):
            problems.append("to_coco: categories 누락")
    except Exception as e:
        problems.append(f"직렬화 예외: {e!r}")
    return problems
