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

from flask import Flask, request, jsonify

from ..schema.rules import RULE_CATALOG

app = Flask(__name__)
# 업로드 크기 상한(전체를 RAM에 읽으므로 OOM/DoS 방지). 대형 실무 DXF 고려해 64MB.
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024


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

# SAM 방찾기(선택적 AI). torch/transformers가 있는 파이썬(오프라인이면 모델 캐시 필요).
# 없으면 AI 방찾기 미제공 → 기존 폴백. 이식성 위해 하드의존 아님.
_SAM_PYTHON = os.environ.get("SAM_PYTHON") or "/A.I_DATA/jbnu/miniconda3/bin/python"
_ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 동시 업로드 시 SAM subprocess가 여러 개 fork되면 각자 358MB 모델 로드 → 저사양(3vCPU/12GB)
# OOM. 전역 락으로 직렬화(느려지지만 안전). 단일 사용자 MVP엔 충분.
import threading as _threading
_SAM_LOCK = _threading.Lock()


def _ai_rooms(dxf_path):
    """miniconda subprocess로 SAM 방찾기 → [{name, area_m2, confidence, polygon(월드mm)}]. 실패=[]."""
    import subprocess
    import tempfile
    import json as _json
    if not os.path.exists(_SAM_PYTHON):
        return []
    outf = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    outf.close()
    try:
        env = dict(os.environ, PYTHONPATH=_ENGINE_ROOT,
                   HF_HOME=os.environ.get("HF_HOME", "/A.I_DATA/jbnu/hf_cache"),
                   HF_HUB_OFFLINE=os.environ.get("HF_HUB_OFFLINE", "1"))
        with _SAM_LOCK:   # 동시 SAM 추론 직렬화(다중 모델 로드 OOM 방지)
            r = subprocess.run([_SAM_PYTHON, "-m", "fireval.ingest.room_sam", dxf_path, outf.name],
                               cwd=_ENGINE_ROOT, env=env, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:   # SAM 크래시/OOM/import실패 → '방 0개'와 구분되게 로그(디버깅)
            import sys
            sys.stderr.write(f"[room_sam] subprocess rc={r.returncode}: {(r.stderr or '')[-800:]}\n")
            return []
        return _json.load(open(outf.name, encoding="utf-8")).get("rooms", [])
    except Exception as e:
        import sys
        sys.stderr.write(f"[room_sam] failed: {repr(e)[:300]}\n")
        return []
    finally:
        try:
            os.unlink(outf.name)
        except OSError:
            pass


def _real_violations_ai(dxf_path, structure, occupancy, mount_height, labels=None):
    """방 면적 = **기하 face-extraction**(room_geom: 벽 평면그래프 닫힌 면 + leak-guard + 안전마진).

    근거(2026-07-06 blind GT): SAM 면적은 실제의 2~7배 과대(부적합)라 폐기 → 면적은 기하가 담당.
    기하는 벽 깨끗한 방서 정확(±3%, 안전마진으로 과대쪽), 아니면 status='needs_boundary'로
    **정직하게 거부**(문틈/병합 → confident-wrong 방지). 거부방은 자동 면적판정 안 하고 '경계 확인 필요'.
    판정경로는 종전대로 권위엔진 check_layout(종별 bounded·2.4.5 면제·구조/층고 미상 강등 자동상속).
    라벨 없으면 배치 판정 안 하고 요구산정만. 좌표: 기하 폴리곤 X*f(mm)→/1000 미터, 인식 위치 *f/1000 미터."""
    from ..ingest.room_geom import geom_faces
    rooms = geom_faces(dxf_path)   # in-process(ezdxf+shapely, torch 불필요) — SAM subprocess 대체
    if not rooms:
        return [], []
    geo = [r for r in rooms if r["status"] == "geometry" and r.get("polygon")]
    nb = [r for r in rooms if r["status"] != "geometry"]

    def _nb_viols():
        # 경계 미확정 방(문틈/병합으로 면 안 닫힘) = 자동 면적판정 불가 → 확인 필요(정직, 자동신뢰 X).
        return [{"ruleId": "FV-DET-need_boundary", "status": "not_applicable", "severity": "",
                 "description": f"{rm['name']}: 벽이 안 닫혀(문틈/병합) 자동 면적 불가 — 경계 확인 필요",
                 "measured": None, "required": None, "unit": "",
                 "roomName": rm["name"], "center": rm.get("center")} for rm in nb]

    try:
        import ezdxf
        from shapely.geometry import Polygon
        from ..engine import checks
        from ..ingest.recognize import recognize_symbols, apply_labels
        from ..ingest.room_extract_raster import _to_mm_factor
        doc = ezdxf.readfile(dxf_path)
        f = _to_mm_factor(doc)
        struct_known = structure in ("fireproof", "noncombustible", "other")
        height_known = mount_height is not None
        # 기하 방 폴리곤(X*f mm) → 미터 _Room (status='geometry'만)
        eng_rooms, keep = [], []
        for rm in geo:
            try:
                poly_m = Polygon([(x / 1000.0, y / 1000.0) for (x, y) in rm["polygon"]])
                if poly_m.is_valid and poly_m.area > 0:
                    eng_rooms.append(checks._Room(rm["name"], poly_m)); keep.append(rm)
            except Exception:
                pass
        if not eng_rooms:
            return _nb_viols(), rooms

        def _attach(viols):
            """_shape_violations dict[]에 roomName·center(클릭이동) 부착 — 설명의 방이름으로 매칭."""
            by_name = {rm["name"]: rm for rm in keep}
            for v in viols:
                nm = (v.get("description", "").split(":")[0]).strip()
                rm = by_name.get(nm)
                v["roomName"] = nm
                v["center"] = rm.get("center") if rm else None
            return viols

        # 라벨 없음 → 감지기 배치를 단정하지 않는다(무엇이 감지기인지 모름). 요구산정만(면적은 기하=정확).
        if not labels:
            out = [{"ruleId": "FV-DET-ai_estimate", "status": "not_applicable", "severity": "",
                    "description": (f"{rm['name']}: 기하 방추출 {rm['area_m2']}㎡ "
                                    f"— 감지기 종류를 라벨하면 배치 판정"),
                    "measured": None, "required": None, "unit": "",
                    "roomName": rm["name"], "center": rm.get("center")} for rm in keep]
            return out + _nb_viols(), rooms

        # 라벨 있음 → 실제 연기/열 감지기(미터)만 check_layout에 투입(스프링클러 등은 이 경로 제외).
        raw = apply_labels(recognize_symbols(doc), labels)
        devices = {fac: [(x * f / 1000.0, y * f / 1000.0) for (x, y) in pts]
                   for fac, pts in raw.items() if fac in ("detector_smoke", "detector_heat")}
        meta = {"structure": structure if struct_known else "fireproof",
                "occupancy": occupancy or "common", "detector_type": "smoke_unknown",
                "mount_height": mount_height if height_known else 3.0}   # HITL은 연기/열만·종별미상→bounded(6축 [7])
        viols_raw = checks.check_layout(eng_rooms, devices, meta)
        return _attach(_shape_violations(viols_raw, struct_known, height_known)) + _nb_viols(), rooms
    except Exception:
        return _nb_viols(), rooms


def _to_dxf(file_storage):
    """업로드 파일 → (dxf_path, cleanup목록, error_dict|None). DWG는 dwg2dxf 변환."""
    import tempfile
    import subprocess
    name = file_storage.filename or "drawing"
    ext = os.path.splitext(name)[1].lower()
    tmp = tempfile.NamedTemporaryFile(suffix=ext or ".dxf", delete=False)
    dxf_path, cleanup = tmp.name, [tmp.name]   # cleanup을 write 前에 확정(예외 시 누수 방지)
    try:
        tmp.write(file_storage.read())
    finally:
        tmp.close()
    if ext == ".dwg":
        if not _DWG2DXF or not os.path.exists(_DWG2DXF):
            return None, cleanup, {"error": "서버에 DWG 변환 도구(dwg2dxf)가 없습니다."}
        dxf_path = tmp.name + ".dxf"
        cleanup.append(dxf_path)
        try:
            r = subprocess.run([_DWG2DXF, "-y", "-o", dxf_path, tmp.name],
                               capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return None, cleanup, {"error": "DWG 변환 시간 초과"}
        except Exception:                      # OSError/PermissionError 등 → 500 대신 안내
            return None, cleanup, {"error": "DWG 변환 실패(변환기 오류)"}
        if r.returncode != 0 or not os.path.exists(dxf_path):
            return None, cleanup, {"error": "DWG→DXF 변환 실패"}
    return dxf_path, cleanup, None


def _parse_drawing(file_storage, structure=None, occupancy="", mount_height=3.0):
    """업로드 DWG/DXF → (사실 dict, 방판정 list, dxf_path, 정리목록).

    임시파일은 **호출측(analyze)이 정리**한다(같은 파일로 실 판정도 돌려야 하므로).
    사실=레이어·소방레이어·실명. 방판정=flood-fill 면적 + NFTC 종류/요구(구조 미상/미신뢰=needs_review).
    occupancy(용도)는 judge_rooms로 전달 — 2.4.2.5 취침류 방의 연기의무 확정에 필요.
    """
    import tempfile
    import subprocess
    from collections import Counter
    name = file_storage.filename or "drawing"
    # 파일→DXF 변환은 _to_dxf 재사용(temp 누수·DWG 비타임아웃 예외 안전 처리 일원화).
    dxf_path, cleanup, err = _to_dxf(file_storage)
    if err or dxf_path is None:
        return {"fileName": name, "error": (err or {}).get("error", "파일 처리 실패")}, [], None, cleanup
    try:
        import ezdxf
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        lc = Counter(e.dxf.layer for e in msp)
        # 소방 레이어 = 명확한 소방 키워드만. 짧고 모호한 'SP'/'PIPE'/'전기'는 비소방 레이어에
        # 부분일치해 오표기하므로 제외(사실 왜곡 방지).
        _fire_ko = ("소방", "감지", "스프링클러", "발신기", "수신기", "경보", "소화", "피난")
        _fire_en = ("FIRE", "FP-", "FP_", "SP_HEAD", "SP_LINE", "SPRINKLER", "SMOKE", "DETECT")

        def _is_fire(ln):
            return any(k in ln for k in _fire_ko) or any(k in ln.upper() for k in _fire_en)
        fire_layers = sorted((ln for ln in lc if _is_fire(ln)), key=lambda ln: -lc[ln])[:15]
        from ..ingest.room_extract_raster import is_room_name, guess_wall_layers, rooms_from_dxf
        from ..engine.detector_type import judge_rooms
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
        judgments = []
        try:
            walls = guess_wall_layers(doc)
            extracted = rooms_from_dxf(doc, walls) if walls else []
            judgments = judge_rooms(extracted, occupancy=occupancy, structure=structure,
                                    mount_height=mount_height or 3.0)
        except Exception as e:
            judgments = [{"room": "", "status": "needs_review", "reason": f"방 판정 생략: {e}"}]
        return facts, judgments, dxf_path, cleanup
    except Exception as e:
        return {"fileName": name, "error": f"DXF 파싱 실패: {e}"}, [], None, cleanup


def _shape_violations(viols, struct_known, height_known):
    """엔진 ViolationLabel[] → 프론트 dict[]. 입력 의존 검사(FV-DET/SPK)를 구조·층고
    미상 시 not_applicable로 강등(가짜 pass/fail 방지). 자동·HITL 경로 공통."""
    out = []
    for v in viols:
        if v.status not in ("violation", "compliant", "not_applicable"):
            continue
        rid = v.rule_id or ""
        status, desc = v.status, (v.description or "")
        # 구조 의존: 감지면적(DET)·스프링클러 반경(SPK)·직통계단 보행한도(EVA, stair_walk_limit).
        needs_struct = rid.startswith(("FV-DET-", "FV-SPK-", "FV-EVA-"))
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


import re as _re

# 제목블록/도면주기에만 나오는 '강한' 토큰(방 이름엔 안 나옴). 방 이름 오탐 방지를 위해
# '설계'(→설계실)·'감리'(→감리실)·'실명'·순수숫자('101'=방번호)는 제외 — 정상 실을 지우면
# 위반 누락(false-pass)이 나므로. 날짜(제목블록)는 별도 정규식으로만 배제.
_META_KW = ("범례", "목록", "표제", "수량표", "일람", "축척", "방위",
            "도면번호", "도면명", "건축주", "비고란", "주기표")


def _looks_like_metadata(name: str) -> bool:
    """방 이름이 제목블록/주기(날짜·범례·목록·수량표 등)로 보이면 True(실이 아님).
    ⚠ 순수숫자('101')·'설계실'/'감리실' 같은 정상 실명은 배제하지 않는다(false-pass 방지)."""
    n = (name or "").strip()
    if not n:
        return True
    if _re.search(r"20\d\d\s*[.\-/]", n):                # 연도 포함 날짜(제목블록)
        return True
    compact = n.replace(" ", "")
    return any(k in compact for k in _META_KW)


# 전용 방 레이어 키워드(dxf_loader._ROOM_KEYS 미러). 짧고 모호한 '실'(→'실선'·'실외기' 등
# 비방 레이어에 부분일치)·'area'·'면적'(→'면적표')은 제외하고, 방 경계에만 쓰이는 명확한 토큰만.
_ROOM_LAYER_KEYS = ("실구획", "실명", "room", "space", "거실", "zone")


def _drawing_has_room_layer(doc) -> bool:
    """도면에 전용 방 레이어의 닫힌 폴리라인이 있나. 없으면 실판정 보류(폴백=신뢰불가)."""
    try:
        msp = doc.modelspace()
        for e in msp.query("LWPOLYLINE"):
            if getattr(e, "closed", False) and any(k in e.dxf.layer.lower() for k in _ROOM_LAYER_KEYS):
                return True
        for e in msp.query("POLYLINE"):
            if getattr(e, "is_closed", False) and any(k in e.dxf.layer.lower() for k in _ROOM_LAYER_KEYS):
                return True
    except Exception:
        return False
    return False


def _trust_rooms(rooms):
    """방 레이어 통과 후 남은 룸에서 메타데이터(제목블록·날짜·범례·순수숫자)·비상식 면적을 배제.
    실명 화이트리스트는 '교육실/자료실' 같은 정상 실을 거부(위반 누락=false-pass)하므로 쓰지 않음."""
    return [r for r in rooms
            if not _looks_like_metadata(getattr(r, "name", "") or "")
            and 1.0 <= getattr(r, "area", 0.0) <= 5000.0]


def _judge(dxf_path, structure, occupancy, mount_height, build_devices):
    """공통 실판정 코어: 도면 → 방(신뢰가드) + devices(주입) → check_layout → shape.

    build_devices(doc, scale, auto_devices) → {facility:[(x,y)_meter]}. 자동경로는 auto_devices
    그대로, HITL경로는 recognize+apply_labels로 대체. 입력 의존 검사(FV-DET/SPK)는
    구조·층고 미상 시 _shape_violations가 강등.
    """
    struct_known = structure in ("fireproof", "noncombustible", "other")
    height_known = mount_height is not None
    try:
        import ezdxf
        from ..ingest import dxf_ir
        from ..engine import checks
        doc = ezdxf.readfile(dxf_path)
        if not _drawing_has_room_layer(doc):           # 전용 방 레이어 없음 → DL.load 폴백(신뢰불가)
            return []                                  #   → 실판정 보류(가짜위반 방지, roomJudgments로 폴백)
        ann = dxf_ir.dxf_to_annotation(
            dxf_path,
            structure=structure if struct_known else "other",   # 미상→보수(기타=더많이 필요, 강등 예정)
            occupancy=occupancy or "common",
            detector_type="smoke_12",
            mount_height=mount_height if height_known else 3.0)
        rooms, auto_devices, meta = checks.from_annotation(ann)
        rooms = _trust_rooms(rooms)                    # ← 남은 메타데이터/비상식 면적 배제
        if not (1 <= len(rooms) <= 80):                # 신뢰 방 없음/폭주 → 실판정 보류
            return []
        scale = ann.units_scale or 1.0
        devices = build_devices(doc, scale, auto_devices)
        if sum(len(v) for v in devices.values()) == 0:  # 설비 없음/미라벨 → 보류
            return []
        viols = checks.check_layout(rooms, devices, meta)
        return _shape_violations(viols, struct_known, height_known)
    except Exception:
        return []      # 엔진 미가용/실패 → 실판정 없음(정직, roomJudgments로 폴백)


def _real_violations(dxf_path, structure, occupancy, mount_height):
    """자동 경로: extract_device_objects가 뽑은 심볼로 실 pass/fail(깨끗 규격 도면)."""
    return _judge(dxf_path, structure, occupancy, mount_height,
                  lambda doc, scale, auto_devices: auto_devices)   # 이미 미터 정합


def _real_violations_hitl(dxf_path, labels, structure, occupancy, mount_height):
    """HITL 경로: 사용자가 라벨한 심볼 클래스(class_id→facility) → 인식 M → 실 pass/fail.
    자동 심볼추출이 실패하는 실무 도면용. 좌표는 raw→units_scale로 미터 정합."""
    from ..ingest.recognize import recognize_symbols, apply_labels

    def build(doc, scale, _auto_devices):
        raw = apply_labels(recognize_symbols(doc), labels or {})   # 자동확정 + 사용자 라벨
        return {k: [(x * scale, y * scale) for (x, y) in v] for k, v in raw.items()}

    return _judge(dxf_path, structure, occupancy, mount_height, build)


@app.route("/api/recognize", methods=["POST", "OPTIONS"])
def recognize():
    """업로드 도면 → 소방 심볼 인식 매니페스트(HITL 명명 UI용).

    클래스별 썸네일·개수·자동추정(있으면)·HITL필요 여부 + 범례종류. 사용자가 각 클래스에
    facility를 라벨하면 /api/analyze 에 labels로 넘겨 실판정을 받는다.
    """
    if request.method == "OPTIONS":
        return ("", 204)
    if "file" not in request.files or not request.files["file"].filename:
        return jsonify({"error": "파일 없음"}), 400
    dxf_path, cleanup, err = _to_dxf(request.files["file"])
    try:
        if err:
            return jsonify(err), 200
        import ezdxf
        from ..ingest.recognize import recognize_symbols, result_manifest
        doc = ezdxf.readfile(dxf_path)
        manifest = result_manifest(doc, recognize_symbols(doc))
        return jsonify(manifest)
    except Exception as e:
        return jsonify({"error": f"인식 실패: {e}", "classes": []}), 200
    finally:
        for p in cleanup:
            try:
                os.unlink(p)
            except OSError:
                pass


@app.route("/api/rooms_ai", methods=["POST", "OPTIONS"])
def rooms_ai():
    """방찾기(기하) — 실무 도면(방 레이어 없어 폴백되던)에서 벽 평면그래프 닫힌 면으로 방 면적 추출
    (room_geom, in-process ~2-3s) + 감지기 배정 + 방별 감지면적 판정. SAM 면적은 2~7배 과대라 폐기.
    ⚠ 사용자 확인(HITL) 전제. 벽 안 닫힌 방은 '경계 확인 필요'(자동 면적판정 안 함)."""
    if request.method == "OPTIONS":
        return ("", 204)
    if "file" not in request.files or not request.files["file"].filename:
        return jsonify({"error": "파일 없음"}), 400
    from ..ingest.room_geom import available as _geom_available
    if not _geom_available():
        return jsonify({"available": False, "aiRooms": [], "violations": [],
                        "note": "기하 방추출(shapely) 미설치 환경 — 방 레이어 있는 도면만 실판정 가능"}), 200
    structure = request.form.get("structure") or None
    occupancy = request.form.get("occupancy") or ""
    mount_height = {"lt4": 3.0, "ge4": 5.0}.get(request.form.get("mount") or "")
    # HITL 라벨(class_id→facility): 있으면 실제 연기/열 감지기 위치로 정확 판정(발신기 등 제외).
    labels = None
    raw_labels = request.form.get("labels")
    if raw_labels:
        try:
            import json
            parsed = json.loads(raw_labels)
            if isinstance(parsed, dict) and parsed:
                labels = {str(k): str(v) for k, v in parsed.items()}
        except (ValueError, TypeError):
            labels = None
    dxf_path, cleanup, err = _to_dxf(request.files["file"])
    try:
        if err:
            return jsonify(err), 200
        viols, rooms = _real_violations_ai(dxf_path, structure, occupancy, mount_height, labels)
        return jsonify({"available": True, "aiRooms": rooms, "violations": viols,
                        "judgmentSource": "geometry",
                        "note": "기하 방추출(벽 닫힌 면 + 안전마진) — 벽이 안 닫힌 방은 '경계 확인 필요'. 확인(HITL) 전제."})
    except Exception as e:
        return jsonify({"available": True, "aiRooms": [], "violations": [], "error": str(e)[:150]}), 200
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

    drawing_info, room_judgments, violations = None, [], []
    used_hitl = False
    if "file" in request.files and request.files["file"].filename:
        structure = request.form.get("structure") or None    # "fireproof"|"other"|미상(None)
        occupancy = request.form.get("occupancy") or ""
        # 부착높이(층고): lt4=3m(<4m) | ge4=5m(≥4m) | 미상=None(실판정은 보류, 요구산정은 3m 가정)
        mount_height = {"lt4": 3.0, "ge4": 5.0}.get(request.form.get("mount") or "")
        # HITL 라벨(class_id→facility): 있으면 인식 M 경로, 없으면 자동추출 경로.
        labels = None
        raw_labels = request.form.get("labels")
        if raw_labels:
            try:
                import json
                parsed = json.loads(raw_labels)
                if isinstance(parsed, dict) and parsed:
                    labels = {str(k): str(v) for k, v in parsed.items()}
            except (ValueError, TypeError):
                labels = None
        drawing_info, room_judgments, dxf_path, cleanup = _parse_drawing(
            request.files["file"], structure, occupancy, mount_height or 3.0)
        try:
            if dxf_path:
                if labels:
                    violations = _real_violations_hitl(
                        dxf_path, labels, structure, occupancy, mount_height)
                    used_hitl = True
                else:
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
        "violations": violations,              # ← 실 pass/fail(자동추출 또는 HITL 인식 M)
        "judgmentSource": "hitl" if used_hitl else "auto",
        "judgmentStatus": "checked" if violations else "pending-recognition",
    })


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8900"))
    print(f"FireVal API → http://{host}:{port}/api/health")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
