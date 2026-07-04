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
import shutil

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


# DWG→DXF 변환기: 환경변수 > PATH > 개발 머신 기본경로(배포 시 DWG2DXF env 또는 PATH로 지정).
_DWG2DXF = (os.environ.get("DWG2DXF") or shutil.which("dwg2dxf")
            or "/A.I_DATA/jbnu/miniconda3/envs/dwgtools/bin/dwg2dxf")


def _parse_drawing(file_storage, structure=None):
    """업로드 DWG/DXF → (사실 dict, 방판정 list). 임시파일은 반드시 정리.

    사실=레이어·엔티티·소방레이어·실명. 방판정=flood-fill 면적 + NFTC 종류/요구(①).
    structure 미상(None)이면 판정은 needs_review(열 과소계산 위험방향 차단).
    """
    import tempfile
    import subprocess
    from collections import Counter
    name = file_storage.filename or "drawing"
    ext = os.path.splitext(name)[1].lower()
    tmp = tempfile.NamedTemporaryFile(suffix=ext or ".dxf", delete=False)
    tmp.write(file_storage.read())
    tmp.close()
    dxf_path = tmp.name
    cleanup = [tmp.name]
    try:
        if ext == ".dwg":
            if not _DWG2DXF or not os.path.exists(_DWG2DXF):
                return {"fileName": name, "error": "서버에 DWG 변환 도구(dwg2dxf)가 없습니다."}, []
            dxf_path = tmp.name + ".dxf"
            cleanup.append(dxf_path)
            try:
                r = subprocess.run([_DWG2DXF, "-y", "-o", dxf_path, tmp.name],
                                   capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                return {"fileName": name, "error": "DWG 변환 시간 초과"}, []
            if r.returncode != 0 or not os.path.exists(dxf_path):
                return {"fileName": name, "error": "DWG→DXF 변환 실패"}, []
        import ezdxf
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        lc = Counter(e.dxf.layer for e in msp)
        fire_kw = ("소방", "FIRE", "fire", "감지", "스프", "SP", "PIPE", "전기", "설비", "DEVICE")
        fire_layers = sorted((ln for ln in lc if any(k in ln for k in fire_kw)),
                             key=lambda ln: -lc[ln])[:15]
        from ..ingest.room_extract_raster import is_room_name
        rooms = []
        for e in msp:
            if e.dxftype() in ("TEXT", "MTEXT"):
                try:
                    t = (e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text).strip()
                except Exception:
                    continue
                if is_room_name(t):        # 가구/집기/도면주기 배제(수납장·진열장·강의대 등)
                    rooms.append(t)
        facts = {"fileName": name, "layerCount": len(doc.layers), "entityCount": len(msp),
                 "fireLayers": list(fire_layers), "roomNames": list(dict.fromkeys(rooms))[:20]}
        # ① 방 판정 — flood-fill 면적 + NFTC 종류/요구(구조 미상/미신뢰=needs_review). 실패해도 사실은 반환.
        judgments = []
        try:
            from ..ingest.room_extract_raster import guess_wall_layers, rooms_from_dxf
            from ..engine.detector_type import judge_rooms
            walls = guess_wall_layers(doc)
            extracted = rooms_from_dxf(doc, walls) if walls else []
            judgments = judge_rooms(extracted, occupancy="", structure=structure)
        except Exception as e:
            judgments = [{"room": "", "status": "needs_review", "reason": f"방 판정 생략: {e}"}]
        return facts, judgments
    except Exception as e:
        return {"fileName": name, "error": f"DXF 파싱 실패: {e}"}, []
    finally:
        for p in cleanup:
            try:
                os.unlink(p)
            except OSError:
                pass


@app.route("/api/analyze", methods=["POST", "OPTIONS"])
def analyze():
    if request.method == "OPTIONS":
        return ("", 204)

    drawing_info, room_judgments = None, []
    if "file" in request.files and request.files["file"].filename:
        structure = request.form.get("structure") or None    # "fireproof"|"other"|미상(None)
        drawing_info, room_judgments = _parse_drawing(request.files["file"], structure)

    # 방별 요구 판정(①): 면적(flood-fill)+NFTC 종류/요구. 구조 미상/면적 미신뢰=needs_review(정직).
    # 확정 pass/fail(배치 vs 필요)은 감지기 인식(B) 연결 후.
    return jsonify({
        "drawingInfo": drawing_info,           # ← 업로드 도면의 실제 사실
        "roomJudgments": room_judgments,       # ← 방별 NFTC 요구/미확정
        "violations": [],
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
