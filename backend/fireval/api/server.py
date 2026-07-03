# -*- coding: utf-8 -*-
"""
server — FireVal/FireOpt 엔진을 HTTP API로 노출(프론트엔드 통합점).

fire_safety_optimizer(React) 프론트가 이 엔드포인트를 호출한다. 계약: INTEGRATION.md.

    ./.venv/bin/python -m fireval.api.server        # http://127.0.0.1:8900

정직성 원칙:
  업로드 도면의 NFTC 적정성 판정(방별 필요 감지기 수)은 설비 심볼 인식 + 방 면적
  추출(R³의 Recognize 단계)이 필요하다. 이건 아직 신뢰 가능한 수준으로 연결되지
  않았다(실제 도면엔 방 경계 폴리라인/면적표기가 없고, FIRE 레이어 심볼 카운트는
  노이즈가 커서 그대로 세면 과다 집계된다). 따라서 **가짜 판정을 내보내지 않는다.**
  업로드 도면에서 '확실히 추출되는 사실'(레이어·엔티티·소방 레이어·실명)만 반환한다.

지금 구현:
  · /api/health                    엔진 생존 확인
  · /api/analyze  (POST)           업로드 도면의 실제 사실(drawingInfo). 판정은 미연결(정직).
"""
from __future__ import annotations

import os

from flask import Flask, request, jsonify

from ..schema.rules import RULE_CATALOG

app = Flask(__name__)


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


_DWG2DXF = "/A.I_DATA/jbnu/miniconda3/envs/dwgtools/bin/dwg2dxf"


def _parse_drawing(file_storage):
    """업로드 DWG/DXF → 실제 도면 정보(레이어·엔티티·소방레이어·실명). 확실히 추출되는 사실만."""
    import os as _os
    import tempfile
    import subprocess
    from collections import Counter
    name = file_storage.filename or "drawing"
    ext = _os.path.splitext(name)[1].lower()
    tmp = tempfile.NamedTemporaryFile(suffix=ext or ".dxf", delete=False)
    tmp.write(file_storage.read())
    tmp.close()
    dxf_path = tmp.name
    if ext == ".dwg":
        if not _os.path.exists(_DWG2DXF):
            return {"fileName": name, "error": "서버에 DWG 변환 도구가 없습니다."}
        dxf_path = tmp.name + ".dxf"
        r = subprocess.run([_DWG2DXF, "-y", "-o", dxf_path, tmp.name],
                           capture_output=True, text=True)
        if r.returncode != 0 or not _os.path.exists(dxf_path):
            return {"fileName": name, "error": "DWG→DXF 변환 실패"}
    try:
        import ezdxf
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        lc = Counter(e.dxf.layer for e in msp)
        fire_kw = ("소방", "FIRE", "fire", "감지", "스프", "SP", "PIPE", "전기", "설비", "DEVICE")
        fire_layers = sorted((ln for ln in lc if any(k in ln for k in fire_kw)),
                             key=lambda ln: -lc[ln])[:15]
        room_kw = ("보육", "유희", "놀이", "조리", "교사", "사무", "원장", "화장", "복도",
                   "계단", "현관", "회의", "강의", "다목적", "세탁", "기계", "창고", "샤워", "주방")
        rooms = []
        for e in msp:
            if e.dxftype() in ("TEXT", "MTEXT"):
                try:
                    t = (e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text).strip()
                except Exception:
                    continue
                if 2 <= len(t) <= 12 and any('가' <= c <= '힣' for c in t) and "평면도" not in t \
                        and (t.endswith(("실", "장")) or any(k in t for k in room_kw)):
                    rooms.append(t)
        return {"fileName": name, "layerCount": len(doc.layers), "entityCount": len(msp),
                "fireLayers": list(fire_layers), "roomNames": list(dict.fromkeys(rooms))[:20]}
    except Exception as e:
        return {"fileName": name, "error": f"DXF 파싱 실패: {e}"}


@app.route("/api/analyze", methods=["POST", "OPTIONS"])
def analyze():
    if request.method == "OPTIONS":
        return ("", 204)

    drawing_info = None
    if "file" in request.files and request.files["file"].filename:
        drawing_info = _parse_drawing(request.files["file"])

    # 판정은 미연결(정직) — 가짜 데모 판정을 내보내지 않는다. 사실(drawingInfo)만.
    return jsonify({
        "drawingInfo": drawing_info,           # ← 업로드 도면의 실제 사실
        "violations": [],                      # ← NFTC 적정성 판정: 인식 파이프라인 연결 후
        "recommendations": [],
        "judgmentStatus": "pending-recognition",
    })


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8900"))
    print(f"FireVal API → http://{host}:{port}/api/health")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
