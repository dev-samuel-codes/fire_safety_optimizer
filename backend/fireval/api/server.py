# -*- coding: utf-8 -*-
"""
server — FireVal/FireOpt 엔진을 HTTP API로 노출(프론트엔드 통합점).

fire_safety_optimizer(React) 프론트가 이 엔드포인트를 호출해 목업 데이터를
실제 엔진 출력으로 교체한다. 계약: INTEGRATION.md.

    ./.venv/bin/python -m fireval.api.server        # http://127.0.0.1:8900

지금 구현:
  · /api/health                    엔진 생존 확인
  · /api/analyze  (POST)           엔진 판정 → 프론트 호환 JSON
      - file 업로드 시: DXF 레이어 요약(실제)
      - 판정(violations): FireVal 규칙엔진 실제 출력(데모 도면 or 향후 인식→방추출 연결)
TODO(계약 유지하며 내부만 확장): 업로드 파일 → 인식(설비)+방추출 → DrawingAnnotation → check_drawing.
"""
from __future__ import annotations

import io
import os

from flask import Flask, request, jsonify
from shapely.geometry import Polygon

from ..engine.checks import _Room, check_layout, summarize
from ..schema.rules import RULE_CATALOG, by_id

app = Flask(__name__)

_SEV_KR = {"critical": "심각", "major": "경고", "minor": "주의", "info": "정보"}
_TONE = {"critical": "danger", "major": "warning", "minor": "warning", "info": "warning"}


@app.after_request
def _cors(resp):
    """다른 오리진(React dev :5173)에서 호출 허용."""
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "engine": "FireVal+FireOpt", "rules": len(RULE_CATALOG)})


def _demo_scene():
    """데모 도면: 교육시설 3층 — 강당(연기 부족)·기계실(열 부족)·강의실(적합).
    실제로는 업로드 도면 → 인식+방추출로 생성될 부분(계약은 동일)."""
    rooms = [_Room("강당", Polygon([(0, 0), (24, 0), (24, 20), (0, 20)])),
             _Room("기계실", Polygon([(26, 0), (42, 0), (42, 15), (26, 15)])),
             _Room("강의실", Polygon([(0, 22), (12, 22), (12, 31), (0, 31)]))]
    devices = {"detector_smoke": [(12, 10), (6, 5), (4, 26)],
               "detector_heat": [(30, 5), (38, 10)]}
    meta = {"structure": "fireproof", "occupancy": "교육연구시설", "floors": 3}
    return rooms, devices, meta


def _layers_from_upload(file_storage):
    """업로드 DXF → 레이어별 엔티티 요약(실제 파싱). DWG는 변환 필요 안내."""
    name = (file_storage.filename or "").lower()
    if name.endswith(".dwg"):
        return [], "DWG는 서버측 DXF 변환 필요(dwg2dxf) — 다음 단계"
    try:
        import ezdxf
        doc = ezdxf.read(io.StringIO(file_storage.read().decode("utf-8", "ignore")))
        from collections import Counter
        c = Counter(e.dxf.layer for e in doc.modelspace())
        return [{"id": ln, "label": ln, "entityCount": n} for ln, n in c.most_common(12)], None
    except Exception as e:
        return [], f"DXF 파싱 실패: {e}"


@app.route("/api/analyze", methods=["POST", "OPTIONS"])
def analyze():
    if request.method == "OPTIONS":
        return ("", 204)

    # (1) 업로드 파일이 있으면 레이어 요약(실제) — 없으면 데모
    layers, note = [], None
    if "file" in request.files and request.files["file"].filename:
        layers, note = _layers_from_upload(request.files["file"])

    # (2) 규칙 판정 — FireVal 엔진 실제 출력
    rooms, devices, meta = _demo_scene()
    viols = check_layout(rooms, devices, meta)

    violations = []
    for v in viols:
        if v.status != "violation":
            continue
        clause = by_id(v.rule_id).clause if v.rule_id in RULE_CATALOG else ""
        violations.append({
            "id": len(violations) + 1,
            "ruleId": v.rule_id, "clause": clause,
            "severity": _SEV_KR.get(v.severity, v.severity),
            "description": v.description,
            "measured": v.measured_value, "required": v.required_value, "unit": v.unit,
            "tone": _TONE.get(v.severity, "warning"),
        })

    # (3) 충돌(clash)·추천 — FireOpt 연결점(현재 구조적 예시, 계약 확정용)
    conflicts = [{"id": 1, "severity": "심각", "title": "스프링클러 ↔ 덕트",
                  "location": "강당", "height": "2450mm", "tone": "danger"}]
    recommendations = [{"id": 1, "title": "대안 1", "summary": "감지기 2개 추가 배치",
                        "saving": "₩0 (설비 추가)", "recommended": True}]

    return jsonify({
        "layers": layers,
        "violations": violations,           # ← FireVal 실제 판정(신규, 목업 대체)
        "conflicts": conflicts,             # ← FireOpt clash 연결 예정
        "recommendations": recommendations,  # ← FireOpt optimize 연결 예정
        "summary": summarize(viols),
        "note": note,
    })


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8900"))
    print(f"FireVal API → http://{host}:{port}/api/health")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
