# -*- coding: utf-8 -*-
"""
server — FireVal/FireOpt 엔진을 HTTP API로 노출(프론트엔드 통합점).

fire_safety_optimizer(React) 프론트가 이 엔드포인트를 호출한다. 계약: INTEGRATION.md.

    ./.venv/bin/python -m fireval.api.server        # http://127.0.0.1:8900

판정 원칙(정직):
  · 업로드 도면에서 '확실히 추출되는 사실'(레이어·소방레이어·실명)은 항상 반환(drawingInfo).
  · 방별 요구 판정(flood-fill 면적 + NFTC 종류/요구)은 roomJudgments — 구조 미상/면적 미신뢰는 needs_review.
  · **깨끗 규격 도면**(방 폴리곤 + 소방설비 심볼이 추출되는 경우)만 규칙엔진(check_drawing)을
    돌려 **실 pass/fail(배치 M vs 필요 N)**을 violations로 반환. 아니면 violations=[](가짜 판정 금지).
"""
from __future__ import annotations

import os
import shutil

from flask import Flask, request, jsonify, send_from_directory

from ..schema.rules import RULE_CATALOG

app = Flask(__name__)

# 배포(Docker/HF Spaces)에서만 설정 — 로컬 개발(:5173 + Vite 프록시)은 건드리지 않음.
_STATIC_DIR = os.environ.get("STATIC_DIR")


@app.after_request
def _cors(resp):
    """다른 오리진(React dev :5173)에서 호출 허용."""
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def _resolve_tool(env_name, binary_name):
    configured = os.environ.get(env_name)
    if configured:
        return configured if os.path.exists(configured) else None
    return shutil.which(binary_name)


def _resolve_dwg2dxf():
    return _resolve_tool("DWG2DXF", "dwg2dxf")


def _resolve_dwgread():
    return _resolve_tool("DWGREAD", "dwgread")


_FIRE_LAYER_KO = ("소방", "감지", "스프링클러", "발신기", "수신기", "경보", "소화", "피난")
_FIRE_LAYER_EN = (
    "FIRE", "FP-", "FP_", "SP_HEAD", "SP_LINE", "SPRINKLER", "SMOKE", "DETECT",
    "SO-", "SO_", "HYDRANT",
)


def _is_fire_name(name):
    text = str(name or "")
    upper = text.upper()
    return any(k in text for k in _FIRE_LAYER_KO) or any(k in upper for k in _FIRE_LAYER_EN)


@app.get("/api/health")
def health():
    dwg2dxf_path = _resolve_dwg2dxf()
    dwgread_path = _resolve_dwgread()
    return jsonify({
        "status": "ok",
        "engine": "FireVal+FireOpt",
        "rules": len(RULE_CATALOG),
        "dwg2dxfAvailable": bool(dwg2dxf_path),
        "dwg2dxfPath": dwg2dxf_path,
        "dwgreadAvailable": bool(dwgread_path),
        "dwgreadPath": dwgread_path,
    })


def _handle_key(value):
    if isinstance(value, (list, tuple)) and value:
        return str(value[-1])
    if value is None:
        return ""
    return str(value)


def _run_command(args, timeout=60):
    import subprocess

    try:
        return subprocess.run(args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def _command_error(result, default_message):
    if result is None:
        return "시간 초과"
    raw = (result.stderr or b"")[-4000:]
    text = raw.decode("utf-8", errors="replace").strip()
    return text or default_message


def _dxf_facts(doc, name, *, analysis_status="ok", analysis_source="dwg2dxf", warnings=None):
    from collections import Counter

    msp = doc.modelspace()
    lc = Counter((getattr(e.dxf, "layer", "") or "") for e in msp)
    fire_layers = sorted((ln for ln in lc if _is_fire_name(ln)), key=lambda ln: -lc[ln])[:15]
    from ..ingest.room_extract_raster import is_room_name

    rooms = []
    for e in msp:
        if e.dxftype() in ("TEXT", "MTEXT"):
            try:
                t = (e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text).strip()
            except Exception:
                continue
            if is_room_name(t):
                rooms.append(t)
    return {
        "fileName": name,
        "layerCount": len(doc.layers),
        "entityCount": len(msp),
        "fireLayers": list(fire_layers),
        "roomNames": list(dict.fromkeys(rooms))[:20],
        "analysisStatus": analysis_status,
        "analysisSource": analysis_source,
        "analysisWarnings": list(warnings or []),
    }


def _json_facts(json_path, name, warnings):
    import json
    from collections import Counter

    from ..ingest.room_extract_raster import is_room_name

    with open(json_path, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)

    objects = data.get("OBJECTS") or []
    layer_names_by_handle = {}
    layers = []
    block_names_by_handle = {}
    for obj in objects:
        if obj.get("object") == "LAYER":
            layer_name = str(obj.get("name") or "")
            if layer_name:
                layers.append(layer_name)
                layer_names_by_handle[_handle_key(obj.get("handle"))] = layer_name
        if obj.get("entity") == "BLOCK":
            block_name = str(obj.get("name") or "")
            if block_name:
                block_names_by_handle[_handle_key(obj.get("handle"))] = block_name

    entity_count = 0
    layer_counts = Counter()
    fire_layer_counts = Counter()
    rooms = []
    for obj in objects:
        entity_type = obj.get("entity")
        if not entity_type:
            continue
        if entity_type not in ("BLOCK", "ENDBLK", "SEQEND", "VERTEX_2D", "VERTEX_PFACE", "VERTEX_PFACE_FACE"):
            entity_count += 1
        layer_name = layer_names_by_handle.get(_handle_key(obj.get("layer")), "")
        if layer_name:
            layer_counts[layer_name] += 1
        block_name = block_names_by_handle.get(_handle_key(obj.get("block_header")), "")
        if layer_name and (_is_fire_name(layer_name) or _is_fire_name(block_name)):
            fire_layer_counts[layer_name] += 1
        if entity_type in ("TEXT", "MTEXT"):
            text = str(obj.get("text_value") or obj.get("text") or "").strip()
            if is_room_name(text):
                rooms.append(text)

    fire_layers = sorted(fire_layer_counts, key=lambda ln: -fire_layer_counts[ln])[:15]
    if not fire_layers:
        fire_layers = sorted((ln for ln in layer_counts if _is_fire_name(ln)), key=lambda ln: -layer_counts[ln])[:15]

    return {
        "fileName": name,
        "layerCount": len(layers),
        "entityCount": entity_count,
        "fireLayers": list(fire_layers),
        "roomNames": list(dict.fromkeys(rooms))[:20],
        "layerNames": layers[:30],
        "analysisStatus": "recovered",
        "analysisSource": "dwgread-json",
        "analysisWarnings": list(warnings),
    }


def _parse_dxf_file(dxf_path, name, structure=None, occupancy="", mount_height=3.0,
                    *, analysis_status="ok", analysis_source="dwg2dxf", warnings=None):
    import ezdxf

    doc = ezdxf.readfile(dxf_path)
    facts = _dxf_facts(
        doc,
        name,
        analysis_status=analysis_status,
        analysis_source=analysis_source,
        warnings=warnings,
    )
    judgments = []
    try:
        from ..ingest.room_extract_raster import guess_wall_layers, rooms_from_dxf
        from ..engine.detector_type import judge_rooms

        walls = guess_wall_layers(doc)
        extracted = rooms_from_dxf(doc, walls) if walls else []
        judgments = judge_rooms(extracted, occupancy=occupancy, structure=structure,
                                mount_height=mount_height or 3.0)
    except Exception as e:
        judgments = [{"room": "", "status": "needs_review", "reason": f"방 판정 생략: {e}"}]
    return facts, judgments


def _parse_drawing(file_storage, structure=None, occupancy="", mount_height=3.0):
    """업로드 DWG/DXF → (사실 dict, 방판정 list, dxf_path, 정리목록).

    임시파일은 **호출측(analyze)이 정리**한다(같은 파일로 실 판정도 돌려야 하므로).
    사실=레이어·소방레이어·실명. 방판정=flood-fill 면적 + NFTC 종류/요구(구조 미상/미신뢰=needs_review).
    occupancy(용도)는 judge_rooms로 전달 — 2.4.2.5 취침류 방의 연기의무 확정에 필요.
    """
    import tempfile
    name = file_storage.filename or "drawing"
    ext = os.path.splitext(name)[1].lower()
    tmp = tempfile.NamedTemporaryFile(suffix=ext or ".dxf", delete=False)
    tmp.write(file_storage.read())
    tmp.close()
    dxf_path = tmp.name
    cleanup = [tmp.name]
    if ext == ".dwg":
        dwg2dxf = _resolve_dwg2dxf()
        if not dwg2dxf:
            return {
                "fileName": name,
                "error": "서버에 DWG 변환 도구(dwg2dxf)가 없습니다.",
                "errorCode": "dwg2dxf_missing",
            }, [], None, cleanup
        dxf_path = tmp.name + ".dxf"
        cleanup.append(dxf_path)
        r = _run_command([dwg2dxf, "-y", "-o", dxf_path, tmp.name])
        if r is None:
            return {"fileName": name, "error": "DWG 변환 시간 초과", "errorCode": "dwg2dxf_timeout"}, [], None, cleanup
        if r.returncode != 0 or not os.path.exists(dxf_path):
            return {"fileName": name, "error": "DWG→DXF 변환 실패", "errorCode": "dwg2dxf_failed"}, [], None, cleanup

    try:
        facts, judgments = _parse_dxf_file(dxf_path, name, structure, occupancy, mount_height)
        return facts, judgments, dxf_path, cleanup
    except Exception as e:
        parse_error = f"DXF 파싱 실패: {e}"
        if ext != ".dwg":
            return {"fileName": name, "error": parse_error, "errorCode": "dxf_parse_failed",
                    "analysisStatus": "failed"}, [], None, cleanup

        warnings = [
            f"기본 DWG→DXF 변환 결과를 ezdxf가 읽지 못했습니다: {e}",
            "복구 분석 결과는 레이어·텍스트 중심의 부분 분석입니다.",
        ]
        minimal_dxf_path = tmp.name + ".minimal.dxf"
        cleanup.append(minimal_dxf_path)
        minimal_ready = False
        minimal_result = _run_command([dwg2dxf, "-y", "-m", "--as", "r2010", "-o", minimal_dxf_path, tmp.name])
        if minimal_result is not None and minimal_result.returncode == 0 and os.path.exists(minimal_dxf_path):
            try:
                minimal_facts, minimal_judgments = _parse_dxf_file(
                    minimal_dxf_path,
                    name,
                    structure,
                    occupancy,
                    mount_height,
                    analysis_status="recovered",
                    analysis_source="dwg2dxf-minimal",
                    warnings=warnings,
                )
                minimal_ready = True
            except Exception as minimal_error:
                warnings.append(f"minimal DXF 복구 파싱 실패: {minimal_error}")
        elif minimal_result is None:
            warnings.append("minimal DXF 복구 변환 시간 초과")
        else:
            warnings.append(f"minimal DXF 복구 변환 실패: {_command_error(minimal_result, '변환 실패')}")

        dwgread = _resolve_dwgread()
        if dwgread:
            json_path = tmp.name + ".json"
            cleanup.append(json_path)
            json_result = _run_command([dwgread, "-O", "JSON", "-o", json_path, tmp.name], timeout=90)
            if json_result is not None and json_result.returncode == 0 and os.path.exists(json_path):
                try:
                    return _json_facts(json_path, name, warnings), [], (minimal_dxf_path if minimal_ready else None), cleanup
                except Exception as json_error:
                    warnings.append(f"JSON 복구 분석 실패: {json_error}")
            elif json_result is None:
                warnings.append("JSON 복구 분석 시간 초과")
            else:
                warnings.append(f"JSON 복구 분석 실패: {_command_error(json_result, '분석 실패')}")
        else:
            warnings.append("dwgread가 없어 JSON 복구 분석을 건너뜀")

        if minimal_ready:
            return minimal_facts, minimal_judgments, minimal_dxf_path, cleanup
        return {"fileName": name, "error": parse_error, "errorCode": "dxf_parse_failed",
                "analysisStatus": "failed", "analysisWarnings": warnings}, [], None, cleanup


def _real_violations(dxf_path, structure, occupancy, mount_height):
    """깨끗 규격(방 폴리곤 + 소방설비 심볼 추출되는) 도면이면 규칙엔진으로 **실 pass/fail**.

    입력 의존별 게이트(검사 통째 억제 금지 — 커버리지 회귀 방지):
      · 감지기 감지면적(FV-DET-*)은 구조+층고 필요 → 미상이면 그 검사만 not_applicable(확인필요).
      · 스프링클러(FV-SPK-*)는 구조 필요 → 구조 미상이면 그 검사만 not_applicable.
      · 소화기(FV-EXT-*)·소화전(FV-HYD-*)·직통계단(FV-EVA-*)은 구조/층고 무관 → 그대로 판정.
    구조/층고 미상 시엔 엔진은 보수 기본(other/3m)로 돌리되, 위 규칙으로 의존 검사만 강등한다.
    """
    struct_known = structure in ("fireproof", "noncombustible", "other")
    height_known = mount_height is not None
    try:
        from ..ingest.dxf_ir import ingest_and_check, ir_summary
        ann, viols = ingest_and_check(
            dxf_path,
            structure=structure if struct_known else "other",   # 미상→보수(기타=더많이 필요, 강등 예정)
            occupancy=occupancy or "common",
            detector_type="smoke_12",
            mount_height=mount_height if height_known else 3.0)
        by = ir_summary(ann).get("by_category", {})
        n_rooms = by.get("room", 0)
        n_dev = sum(v for k, v in by.items() if k not in ("room", "door"))
        if not (1 <= n_rooms <= 80 and n_dev > 0):     # 방 폴백폭주(쓰레기)·설비없음 → 실판정 보류
            return []
        out = []
        for v in viols:
            if v.status not in ("violation", "compliant", "not_applicable"):
                continue
            rid = v.rule_id or ""
            status, desc = v.status, (v.description or "")
            needs_struct = rid.startswith(("FV-DET-", "FV-SPK-"))
            needs_height = rid.startswith("FV-DET-")
            if (needs_struct and not struct_known) or (needs_height and not height_known):
                miss = "·".join(x for x, ok in (("구조", struct_known), ("층고", height_known)) if not ok)
                room = desc.split(":")[0] if ":" in desc else desc
                status = "not_applicable"
                desc = f"{room}: 적정성 확인 필요 ({miss} 미상 — 감지면적/반경 기준 확정 불가)"
            out.append({
                "ruleId": rid, "status": status,
                "severity": getattr(v, "severity", "") or "",
                "description": desc,
                "measured": v.measured_value, "required": v.required_value,
                "unit": v.unit or ""})
        return out
    except Exception:
        return []      # 규칙엔진 미가용/실패 → 실판정 없음(정직, roomJudgments로 폴백)


@app.route("/api/analyze", methods=["POST", "OPTIONS"])
def analyze():
    if request.method == "OPTIONS":
        return ("", 204)

    drawing_info, room_judgments, violations = None, [], []
    if "file" in request.files and request.files["file"].filename:
        structure = request.form.get("structure") or None    # "fireproof"|"other"|미상(None)
        occupancy = request.form.get("occupancy") or ""
        # 부착높이(층고): lt4=3m(<4m) | ge4=5m(≥4m) | 미상=None(실판정은 보류, 요구산정은 3m 가정)
        mount_height = {"lt4": 3.0, "ge4": 5.0}.get(request.form.get("mount") or "")
        drawing_info, room_judgments, dxf_path, cleanup = _parse_drawing(
            request.files["file"], structure, occupancy, mount_height or 3.0)
        try:
            if dxf_path:
                violations = _real_violations(dxf_path, structure, occupancy, mount_height)
        finally:
            for p in cleanup:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    return jsonify({
        "drawingInfo": drawing_info,           # ← 업로드 도면의 실제 사실
        "roomJudgments": room_judgments,       # ← 방별 NFTC 요구/미확정(flood-fill 경로)
        "violations": violations,              # ← 깨끗 규격이면 실 pass/fail(배치 M vs 필요 N)
        "judgmentStatus": "checked" if violations else "pending-recognition",
    })


if _STATIC_DIR:
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def serve_frontend(path):
        """빌드된 React 정적 파일 서빙(SPA) — Docker/HF Spaces 배포용.

        /api/*는 위쪽 라우트가 먼저 매치되므로 이 catch-all과 겹치지 않음.
        """
        target = os.path.join(_STATIC_DIR, path) if path else ""
        if path and os.path.isfile(target):
            return send_from_directory(_STATIC_DIR, path)
        return send_from_directory(_STATIC_DIR, "index.html")


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8900"))
    print(f"FireVal API → http://{host}:{port}/api/health")
    if _STATIC_DIR:
        print(f"정적 프론트 서빙 → {_STATIC_DIR}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
