# -*- coding: utf-8 -*-
"""recognize — 벡터 DXF 소방 심볼 인식(프로토타입, R³의 Recognize 단계).

핵심 설계(선행연구 + 안전규율)
------------------------------
그룹핑(구분)과 명명(종류)은 **별개 단계**다.
  1) 블록정의/기하로 심볼을 K개 '서로 다른 클래스'로 그룹핑(구분만).   ← DXF block prior(우리 강점)
  2) 각 클래스에 종류를 붙인다: 레이어힌트 → 범례매칭 → HITL 순.       ← Hain 2025 legend self-cal

안전편향(precision-first): 감지기 종별(연기/열)은 감지면적 기준을 가르는 **안전임계값**이라,
레이어/범례로 확정하지 못하면 auto로 추정하지 않고 **HITL로 넘긴다**(가짜 종별 = 위반 은폐 위험).

산출 = 엔진(engine.checks.check_layout)이 그대로 먹는 devices dict
  {detector_smoke/detector_heat/sprinkler/hydrant/extinguisher/evacuation: [(x,y),...]}.

regime 의존성(실측): 블록 prior는 도면마다 있다/없다.
  · 어린이집: 구분 블록 O, 범례 X → 그룹핑 자동 + HITL 명명(K 라벨).
  · 전기소방: 블록 X(병합 지오메트리), 범례 O → 그룹핑 거침 + 범례명명.
두 regime을 **따로** 다룬다(가짜 자동화 회피).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# HITL/자동 라벨 → 엔진 facility 키(check_layout이 소비하는 키). 이 집합이 계약.
FACILITIES = ("detector_smoke", "detector_heat", "sprinkler",
              "hydrant", "extinguisher", "evacuation")
# 종류 미상(감지기인데 연기/열 모름)·인식했으나 layout 판정 대상 아님(발신기 등)·무시.
DETECTOR_UNKNOWN = "detector_unknown"   # 감지기 확정, 종별 미상 → HITL 필요
IGNORE = "ignore"

# 소방 심볼이 놓이는 레이어 후보 키워드(사실 왜곡 방지 위해 보수적으로).
_FIRE_LAYER_KO = ("소방", "감지", "스프링클러", "발신기", "수신기", "경보", "소화", "피난")
_FIRE_LAYER_EN = ("FIRE", "FP-", "FP_", "SP_HEAD", "SP_LINE", "SPRINKLER",
                  "SMOKE", "DETECT", "HYDRANT", "EXTING", "DEVICE", "EQUIP")
# 주기·치수·범례·해칭 레이어는 소방 심볼이 아님(노트 원·인출선·치수선). 이게 소방레이어 키워드에
# 부분일치(예: 'FIRE-NOTE'⊃'FIRE')해 가짜 감지기로 잡히므로 먼저 배제. (실측: 어린이집 노트원 22개)
_ANNOT_LAYER_EN = ("NOTE", "DIM", "TEXT", "LEADER", "HATCH", "LEGEND", "TITLE", "GRID", "AXIS")
_ANNOT_LAYER_KO = ("주기", "치수", "범례", "인출", "문자", "표제", "일람")


def _is_fire_layer(layer: str) -> bool:
    up = layer.upper()
    if any(k in up for k in _ANNOT_LAYER_EN) or any(k in layer for k in _ANNOT_LAYER_KO):
        return False   # 주기/치수/범례/해칭 레이어 → 심볼 아님(가짜 감지기 방지)
    return any(k in layer for k in _FIRE_LAYER_KO) or any(k in up for k in _FIRE_LAYER_EN)


def _block_name_hint(name: str) -> tuple[str | None, float, str]:
    """블록 '이름'이 의미있을 때(FP_SMOKE·HEAT_DET 등)의 유추. 익명/hex면 (None,0,'').

    안전근거: 블록명 smoke/heat는 설계자가 명시한 의도 → 안전하게 자동확정 가능.
    (위험한 건 *기하*로 smoke/heat 추정 — 연기/정온 글리프가 안 갈림. 명시 블록명은 별개.)"""
    up = (name or "").upper()
    # 익명/자동생성 블록명(A$C…, *U…, hex)은 의미없음 → 스킵
    if not up or up.startswith(("A$", "*", "_")) or all(c in "0123456789ABCDEF$" for c in up):
        return None, 0.0, ""
    if "SMOKE" in up or "연기" in name:
        return "detector_smoke", 0.85, f"블록명={name}(연기)"
    if "HEAT" in up or "열감" in name or "정온" in name or "차동" in name:
        return "detector_heat", 0.85, f"블록명={name}(열)"
    if "SPRINK" in up or "SP_HEAD" in up:
        return "sprinkler", 0.85, f"블록명={name}(스프링클러)"
    if "HYDRANT" in up or "소화전" in name:
        return "hydrant", 0.8, f"블록명={name}(소화전)"
    if "EXTING" in up or "소화기" in name:
        return "extinguisher", 0.8, f"블록명={name}(소화기)"
    if up.startswith(("FP_DET", "DETECT")) or "감지기" in name:
        return DETECTOR_UNKNOWN, 0.0, f"블록명={name}(감지기·종별미상→HITL)"
    return None, 0.0, ""


def _layer_hint(layer: str) -> tuple[str | None, float, str]:
    """레이어명만으로 유추 가능한 (facility, confidence, 근거). 확정 못 하면 (None,0,'').

    안전편향: 감지기 레이어는 '감지기'까지만 확정하고 종별(연기/열)은 미상으로 둔다
    (레이어가 종별을 알려주는 일은 드묾 → 자동 종별 추정 금지)."""
    up = layer.upper()
    if "SP_HEAD" in up or "SPRINK" in up or "스프링클러" in layer:
        return "sprinkler", 0.9, "레이어=스프링클러"
    if "HYDRANT" in up or "소화전" in layer:
        return "hydrant", 0.85, "레이어=소화전"
    if "EXTING" in up or "소화기" in layer:
        return "extinguisher", 0.85, "레이어=소화기"
    if "피난" in layer or "EVAC" in up or "EXIT" in up:
        return "evacuation", 0.8, "레이어=피난"
    if "발신기" in layer:
        return IGNORE, 0.7, "레이어=발신기(면적판정 대상 아님)"
    if "FIRE" in up or "감지" in layer or "SMOKE" in up or "DETECT" in up:
        # 감지기는 맞지만 종별 미상 → HITL 필요(안전편향)
        return DETECTOR_UNKNOWN, 0.0, "레이어=감지기(종별 미상→HITL)"
    return None, 0.0, ""


@dataclass
class SymbolClass:
    class_id: str
    layer: str
    key: tuple                     # 그룹 정체성(블록명 or ('geo',dxftype,size))
    positions: list = field(default_factory=list)   # [(x,y),...] 월드좌표
    guess: str | None = None       # facility | DETECTOR_UNKNOWN | IGNORE | None
    confidence: float = 0.0
    source: str = ""               # 'layer' | 'legend' | 'block_name'
    reason: str = ""

    @property
    def count(self) -> int:
        return len(self.positions)

    @property
    def needs_hitl(self) -> bool:
        # 종별 미상 감지기이거나, 아무 근거도 못 붙였으면 사람 확인 필요.
        return self.guess in (None, DETECTOR_UNKNOWN)


@dataclass
class RecognitionResult:
    classes: list                  # [SymbolClass,...]
    legend_types: list = field(default_factory=list)   # 범례에서 읽은 종류명(있으면)

    def hitl_manifest(self) -> list:
        """사람이 라벨해야 할 클래스만(개수 많은 순). UI가 이걸 띄운다."""
        return sorted([c for c in self.classes if c.needs_hitl],
                      key=lambda c: -c.count)

    def auto_summary(self) -> dict:
        auto = [c for c in self.classes if not c.needs_hitl and c.guess != IGNORE]
        return {"auto_classes": len(auto),
                "auto_instances": sum(c.count for c in auto),
                "hitl_classes": len(self.hitl_manifest()),
                "hitl_instances": sum(c.count for c in self.hitl_manifest())}


def _block_key(doc, insert) -> tuple:
    """INSERT의 그룹 정체성 = 블록명(같은 심볼은 같은 블록정의를 참조 → 자연 그룹)."""
    return ("blk", insert.dxf.name)


def _geo_key(e) -> tuple:
    """블록 아닌 raw 기하 심볼의 정체성 = (타입, 크기버킷). 원/타원 크기로 종류 구분."""
    t = e.dxftype()
    size = 0.0
    try:
        if t == "CIRCLE":
            size = float(e.dxf.radius)
        elif t == "ELLIPSE":
            size = float(abs(e.dxf.major_axis.x) + abs(e.dxf.major_axis.y))
    except Exception:
        pass
    return ("geo", t, round(size / 50.0))   # 50mm 버킷


def _pos(e):
    try:
        if e.dxftype() == "INSERT":
            return (float(e.dxf.insert.x), float(e.dxf.insert.y))
        if e.dxftype() == "CIRCLE":
            return (float(e.dxf.center.x), float(e.dxf.center.y))
        if e.dxftype() == "ELLIPSE":
            return (float(e.dxf.center.x), float(e.dxf.center.y))
    except Exception:
        pass
    return None


def _parse_legend_types(msp) -> list:
    """범례/주기 텍스트에서 감지기 종류명을 surface(best-effort).

    NOTE: 프로토타입은 '어떤 종류가 범례에 있나'만 뽑는다. 심볼 글리프↔종류명 기하 매칭
    (Hain 2025식)은 다음 증분. 지금은 범례 유무·종류목록을 HITL 보조로 제공."""
    kw = {"차동식": "detector_heat", "정온식": "detector_heat", "보상식": "detector_heat",
          "연기식": "detector_smoke", "연기감지": "detector_smoke", "광전식": "detector_smoke",
          "이온화": "detector_smoke", "아날로그": "detector_smoke", "열감지": "detector_heat"}
    found = []
    for e in msp:
        if e.dxftype() not in ("TEXT", "MTEXT"):
            continue
        try:
            t = (e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text).strip()
        except Exception:
            continue
        if 2 <= len(t) <= 24:
            for k, fac in kw.items():
                if k in t:
                    found.append((t, fac))
                    break
    # 중복 텍스트 제거
    seen, out = set(), []
    for t, fac in found:
        if t not in seen:
            seen.add(t); out.append((t, fac))
    return out


def recognize_symbols(doc, fire_layers: list | None = None) -> RecognitionResult:
    """DXF 문서 → 소방 심볼 클래스들(구분) + 레이어힌트 부분명명 + 범례종류.

    종별 확정(연기 vs 열)은 여기서 하지 않는다 — HITL/범례매칭이 붙일 몫."""
    msp = doc.modelspace()
    allow = set(fire_layers) if fire_layers else None
    groups: dict[tuple, SymbolClass] = {}
    # 도면 유형 판별: '감지기' 텍스트가 하나도 없으면 이 도면은 감지기 도면이 아니다(예: 소방'기계'
    # 도면=스프링클러). 그런 도면에서 FIRE 레이어를 '감지기'라 단정하면 오라벨 → 종류 미상으로.
    has_detector_ctx = False
    for e in msp.query("TEXT MTEXT"):
        try:
            tx = (e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text)
        except Exception:
            continue
        if "감지" in (tx or ""):
            has_detector_ctx = True
            break
    for e in msp:
        t = e.dxftype()
        if t not in ("INSERT", "CIRCLE", "ELLIPSE"):
            continue
        layer = e.dxf.layer
        if allow is not None:
            if layer not in allow:
                continue
        elif not _is_fire_layer(layer):
            continue
        p = _pos(e)
        if p is None:
            continue
        key = _block_key(doc, e) if t == "INSERT" else _geo_key(e)
        gid = (layer, key)
        sc = groups.get(gid)
        if sc is None:
            # 블록명 힌트(의미있으면) 우선 → 없으면 레이어 힌트. 기하 심볼은 레이어만.
            fac = conf = reason = None; src = ""
            if t == "INSERT":
                fac, conf, reason = _block_name_hint(e.dxf.name)
                if fac is not None:
                    src = "block_name"
            if fac is None:
                fac, conf, reason = _layer_hint(layer)
                src = "layer" if fac else ""
            # 감지기 도면이 아닌데 레이어힌트가 '감지기'라 했으면 → 종류 미상으로 강등(오라벨 방지).
            if fac == DETECTOR_UNKNOWN and not has_detector_ctx:
                fac, src, reason = None, "", "종류 미상(감지기 도면 아님 — 소방기계 등)"
            sc = SymbolClass(class_id=f"C{len(groups)}", layer=layer, key=key,
                             guess=fac, confidence=conf or 0.0,
                             source=src, reason=reason or "")
            groups[gid] = sc
        sc.positions.append(p)
    return RecognitionResult(classes=list(groups.values()),
                             legend_types=_parse_legend_types(msp))


import math as _math


def _entity_polylines(e) -> list:
    """ezdxf 엔티티 → SVG용 폴리라인 점열 리스트(곡선은 샘플링). 블록로컬 좌표."""
    d = e.dxftype()
    try:
        if d == "LINE":
            return [[(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]]
        if d == "CIRCLE":
            cx, cy, r = e.dxf.center.x, e.dxf.center.y, e.dxf.radius
            return [[(cx + r * _math.cos(t), cy + r * _math.sin(t))
                     for t in [i * _math.pi / 18 for i in range(37)]]]
        if d == "ARC":
            cx, cy, r = e.dxf.center.x, e.dxf.center.y, e.dxf.radius
            a0 = _math.radians(e.dxf.start_angle); a1 = _math.radians(e.dxf.end_angle)
            if a1 < a0:
                a1 += 2 * _math.pi
            n = max(4, int((a1 - a0) / (_math.pi / 18)))
            return [[(cx + r * _math.cos(a0 + (a1 - a0) * i / n),
                      cy + r * _math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]]
        if d == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points()]
            if getattr(e, "closed", False) and pts:
                pts = pts + [pts[0]]
            return [pts] if len(pts) >= 2 else []
        if d == "ELLIPSE":
            cx, cy = e.dxf.center.x, e.dxf.center.y
            mx, my = e.dxf.major_axis.x, e.dxf.major_axis.y
            a = _math.hypot(mx, my); b = a * e.dxf.ratio
            rot = _math.atan2(my, mx)
            out = []
            for i in range(37):
                t = i * _math.pi / 18
                x, y = a * _math.cos(t), b * _math.sin(t)
                out.append((cx + x * _math.cos(rot) - y * _math.sin(rot),
                            cy + x * _math.sin(rot) + y * _math.cos(rot)))
            return [out]
    except Exception:
        return []
    return []


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _approx_pos(e):
    """엔티티 대표 위치(반경 필터용). 못 구하면 None."""
    d = e.dxftype()
    try:
        if d == "INSERT":
            return (e.dxf.insert.x, e.dxf.insert.y)
        if d in ("CIRCLE", "ARC", "ELLIPSE"):
            return (e.dxf.center.x, e.dxf.center.y)
        if d == "LINE":
            return ((e.dxf.start.x + e.dxf.end.x) / 2, (e.dxf.start.y + e.dxf.end.y) / 2)
        if d in ("TEXT", "MTEXT", "ATTDEF"):
            return (e.dxf.insert.x, e.dxf.insert.y)
        if d == "LWPOLYLINE":
            pts = list(e.get_points())
            return (pts[0][0], pts[0][1]) if pts else None
    except Exception:
        return None
    return None


def _entity_geom(e, ox: float = 0.0, oy: float = 0.0):
    """엔티티 → (폴리라인들, 텍스트들). 텍스트=[(x,y,height,string)]. (ox,oy)만큼 평행이동.
    감지기 심볼 안의 글자(S/H 등)를 <text>로 살리기 위해 TEXT/MTEXT/ATTDEF도 처리."""
    d = e.dxftype()
    try:
        if d in ("TEXT", "MTEXT", "ATTDEF"):
            s = (e.plain_text() if d == "MTEXT" else (e.dxf.text or "")).strip()
            if not s:
                return [], []
            p = e.dxf.insert
            h = float(getattr(e.dxf, "height", 0) or getattr(e.dxf, "char_height", 0) or 2.5)
            return [], [(p.x - ox, p.y - oy, h, s[:6])]
    except Exception:
        return [], []
    pls = _entity_polylines(e)
    if ox or oy:
        pls = [[(x - ox, y - oy) for (x, y) in pl] for pl in pls]
    return pls, []


def _svg_render(polys: list, texts: list, size: int = 72, max_pts: int = 1200) -> str:
    """폴리라인 + 텍스트 → 정규화·중앙정렬 SVG(글자는 <text>). y 반전·비유한 좌표 제거·점 상한."""
    clean, total = [], 0
    for pl in polys:
        seg = [(x, y) for (x, y) in pl if _math.isfinite(x) and _math.isfinite(y)]
        if len(seg) >= 2:
            clean.append(seg); total += len(seg)
        if total >= max_pts:
            break
    texts = [t for t in texts if _math.isfinite(t[0]) and _math.isfinite(t[1])][:8]
    allpts = [p for pl in clean for p in pl] + [(t[0], t[1]) for t in texts]
    if not allpts:
        return ""
    xs = [p[0] for p in allpts]; ys = [p[1] for p in allpts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    w = max(maxx - minx, 1e-6); h = max(maxy - miny, 1e-6)
    s = (size - 10) / max(w, h)
    ox = (size - w * s) / 2; oy = (size - h * s) / 2
    def tx(x): return ox + (x - minx) * s
    def ty(y): return size - (oy + (y - miny) * s)   # y 반전
    body = []
    for pl in clean:
        d = " ".join(f"{tx(x):.1f},{ty(y):.1f}" for x, y in pl)
        body.append(f'<polyline points="{d}" fill="none" stroke="currentColor" '
                    f'stroke-width="1.4" stroke-linejoin="round"/>')
    for (x, y, th, st) in texts:
        fs = min(max(th * s, 8), size * 0.6)
        body.append(f'<text x="{tx(x):.1f}" y="{ty(y):.1f}" font-size="{fs:.1f}" '
                    f'fill="currentColor" text-anchor="middle" dominant-baseline="central" '
                    f'font-family="sans-serif">{_esc(st)}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
            f'width="{size}" height="{size}">{"".join(body)}</svg>')


def _class_thumbnail(doc, sc: SymbolClass, size: int = 72) -> str:
    """클래스 대표 글리프 SVG. INSERT=블록정의 기하+글자 / geo=대표 인스턴스 주변 실제 기하+글자."""
    polys, texts = [], []
    if sc.key[0] == "blk":
        try:
            for i, e in enumerate(doc.blocks[sc.key[1]]):
                if i >= 500:            # 초대형 블록 방어
                    break
                p, t = _entity_geom(e)
                polys += p; texts += t
        except Exception:
            pass
    elif sc.positions:
        # geo 클래스: 대표 인스턴스 주변(반경) 실제 기하+글자를 모아 '진짜 글리프' 렌더
        # (예전엔 크기버킷 원으로 근사 → 서로 똑같아 보였음). 반경은 심볼 크기에 비례.
        cx, cy = sc.positions[0]
        bucket = sc.key[2] if len(sc.key) > 2 else 1
        rad = max(bucket * 50 * 2.5, 300.0)
        try:
            for e in doc.modelspace():
                if e.dxftype() not in ("LINE", "ARC", "CIRCLE", "ELLIPSE", "LWPOLYLINE", "TEXT", "MTEXT"):
                    continue
                ep = _approx_pos(e)
                if ep is None or abs(ep[0] - cx) > rad or abs(ep[1] - cy) > rad:
                    continue
                p, t = _entity_geom(e, cx, cy)
                polys += p; texts += t
                if len(polys) > 120:
                    break
        except Exception:
            pass
    return _svg_render(polys, texts, size)


def result_manifest(doc, result: RecognitionResult) -> dict:
    """프론트 HITL UI용 직렬화 매니페스트. HITL 필요 클래스를 개수순으로 앞에."""
    classes = []
    for c in result.classes:
        guess = c.guess if c.guess in FACILITIES else None
        classes.append({
            "classId": c.class_id, "layer": c.layer, "count": c.count,
            "guess": guess,                       # 자동확정 facility(있으면 UI 프리필)
            "needsHitl": c.needs_hitl,
            "isDetector": c.guess == DETECTOR_UNKNOWN,   # 감지기 확정·종별미상 → UI 강조
            "source": c.source, "reason": c.reason,
            "thumbnail": _class_thumbnail(doc, c),
        })
    classes.sort(key=lambda x: (not x["needsHitl"], -x["count"]))
    return {"classes": classes,
            "legendTypes": [t for t, _ in result.legend_types],
            "facilityOptions": list(FACILITIES) + [IGNORE]}


def apply_labels(result: RecognitionResult, labels: dict | None = None) -> dict:
    """클래스 자동추정 + HITL 라벨(class_id→facility)을 합쳐 엔진 devices dict 생성.

    labels: {class_id: facility|IGNORE}. 자동확정 클래스는 라벨 없어도 반영.
    종별 미상 감지기(DETECTOR_UNKNOWN)에 라벨이 없으면 **버린다**(가짜 종별 주입 금지)."""
    labels = labels or {}
    devices: dict[str, list] = {}
    for c in result.classes:
        fac = labels.get(c.class_id, c.guess)
        if fac in (None, DETECTOR_UNKNOWN, IGNORE):
            continue    # 미확정/무시 → M에 넣지 않음(정직: 모르면 배제, 추정 안 함)
        if fac not in FACILITIES:
            continue
        devices.setdefault(fac, []).extend(c.positions)
    return devices
