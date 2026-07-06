# -*- coding: utf-8 -*-
"""room_sam — SAM 기반 방 세그멘테이션(선택적 AI 방찾기).

flood-fill(고전)이 실패하는 실무 도면(가구·클러터)에서 SAM(Segment Anything)으로 방 경계를
찾는다. **측정으로 확정한 3기법**(project_fireopt_room_ai):
  ① 한 평면 격리(도면에 평면 여러개면 방시드 x-gap으로 분리, 최다방 평면 선택)
  ② 직교벽만 렌더(길이≥700mm+직교, 대각선 단면선 제거) + 가벼운 봉합(문틈)
  ③ box prompt(방시드 ±4.5m, point보다 압도 — bleed↓·전체방↑)

산출: 방 [{name, area_m2, confidence(SAM IoU), polygon(월드 DXF 좌표)}]. polygon은 감지기
(월드 좌표)와 point-in-polygon으로 배정 가능. **완벽친 않음 → HITL 확인 전제**(사용자가 조정).

의존: torch+transformers(SAM)+cv2+PIL+scipy+ezdxf. 없으면 available()=False → 백엔드는 폴백.
"""
from __future__ import annotations

import math

_MODEL = None
_PROC = None


def available() -> bool:
    """SAM 방찾기 가능 여부(선택적 기능 — 무거운 의존성)."""
    try:
        import torch  # noqa
        import transformers  # noqa
        import cv2  # noqa
        return True
    except Exception:
        return False


def _load_sam():
    """SAM vit-base 로드(캐시). 오프라인이면 HF_HUB_OFFLINE=1 + 캐시 필요."""
    global _MODEL, _PROC
    if _MODEL is None:
        from transformers import SamModel, SamProcessor
        _PROC = SamProcessor.from_pretrained("facebook/sam-vit-base")
        _MODEL = SamModel.from_pretrained("facebook/sam-vit-base").eval()
    return _MODEL, _PROC


import re as _re
# 방 코드 라벨 패턴(cross-office 일반화): 설계사마다 방을 '이름'(사무실)이 아니라 '코드'로 라벨.
#   가106·다B01(블록/동 접두+번호)·101호·B103. is_room_name(키워드)이 못 잡던 것.
# SAM이 코드 위치에서 실제 방을 찾으면 신뢰 높고, 제목블록 코드면 신뢰 낮아 걸러진다(공간 검증).
_ROOM_CODE = _re.compile(
    r"^[가-힣]{1,2}[A-Za-z]?\d{2,4}[가-힣]?$"   # 가106, 다B01, 나201호
    r"|^\d{1,4}호$"                            # 101호
    r"|^[A-Za-z]{1,2}-?\d{2,4}$")              # B103, A-201


def _is_room_code(t):
    """방 코드 라벨인지. 제목블록/도면번호류는 _NON_ROOM으로 배제."""
    from .room_extract_raster import _NON_ROOM
    t = (t or "").strip()
    if not (2 <= len(t) <= 8) or any(b in t for b in _NON_ROOM):
        return False
    return bool(_ROOM_CODE.match(t))


# 설비/표지 라벨 = 방 아님. "계단통로 유도등"처럼 방 키워드(통로)를 포함해도 걸러야 정직.
_FIXTURE_WORDS = ("유도등", "표지", "감지기", "발신기", "소화기", "소화전", "스프링클러",
                  "경종", "수신기", "사이렌", "펌프", "밸브", "댐퍼", "함")
# 심볼/주기 레이어 힌트: 코드-패턴 라벨(오탐 위험)이 여기 있으면 방 아닌 주기(맞변35 등).
# 주의: "TEXT"는 넣지 않음 — 방 코드가 정상 text 레이어에 오는 도면(용산) 오배제 방지.
_ANNOT_LAYER_HINT = ("SYM", "SYMBOL", "NOTE", "DIM", "LEADER", "HATCH", "LEGEND",
                     "치수", "주기", "인출", "범례", "표제")


def _is_annot_layer(layer):
    L = (layer or "").upper()
    return any(k in L for k in _ANNOT_LAYER_HINT)


def _room_seeds(doc, f):
    """방 라벨 텍스트(이름 또는 코드) → [(name, wx, wy)] 월드좌표.
    이름=is_room_name(서술명), 코드=_is_room_code(가106 등) — 설계사별 라벨 관행 차이 흡수.
    설비/표지 라벨(_FIXTURE_WORDS)은 방 키워드를 포함해도 배제(유도등·감지기 등 ≠ 방).
    코드-경로 라벨이 심볼/주기 레이어에 있으면 배제(맞변35처럼 주기가 코드패턴에 걸리는 것 방지) —
    측정: 진짜 방=ARCH/text 레이어, 주기 노이즈=SYM. name-경로·정상 레이어 코드는 불변."""
    from .room_extract_raster import is_room_name
    out = []
    for e in doc.modelspace().query("TEXT MTEXT"):
        try:
            t = (e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text).strip()
        except Exception:
            continue
        if any(w in t for w in _FIXTURE_WORDS):
            continue
        isname = is_room_name(t)
        iscode = _is_room_code(t)
        if not (isname or iscode):
            continue
        if iscode and not isname and _is_annot_layer(e.dxf.layer):
            continue   # 코드패턴이지만 심볼/주기 레이어 → 방 아닌 주기(맞변35)
        out.append((t, e.dxf.insert.x * f, e.dxf.insert.y * f))
    return out


def _isolate_plan(seeds):
    """도면에 평면이 여러 개면 x-gap으로 클러스터링 → 방이 가장 많은 평면의 시드만.
    단일 평면이면 전부 반환. 반환: (chosen_seeds, x_lo, x_hi)."""
    if len(seeds) <= 2:
        xs = [s[1] for s in seeds]
        return seeds, (min(xs) if xs else 0), (max(xs) if xs else 0)
    xs = sorted(s[1] for s in seeds)
    span = xs[-1] - xs[0] or 1.0
    # 최대 x-gap이 전체의 25% 넘으면 그 지점에서 분할
    gaps = [(xs[i + 1] - xs[i], (xs[i] + xs[i + 1]) / 2) for i in range(len(xs) - 1)]
    gap, cut = max(gaps, key=lambda g: g[0]) if gaps else (0, 0)
    if gap < 0.25 * span:
        return seeds, xs[0], xs[-1]                      # 단일 평면
    left = [s for s in seeds if s[1] < cut]
    right = [s for s in seeds if s[1] >= cut]
    chosen = left if len(left) >= len(right) else right  # 방 많은 쪽
    cx = [s[1] for s in chosen]
    return chosen, min(cx), max(cx)


def _wall_layer(doc, f):
    """벽/건축 레이어 추정 = 긴 직교 LINE이 가장 많은 레이어. ARCH 등 이름 무관."""
    from collections import Counter
    cnt = Counter()
    for e in doc.modelspace().query("LINE"):
        a, b = e.dxf.start, e.dxf.end
        dx, dy = (b.x - a.x) * f, (b.y - a.y) * f
        if math.hypot(dx, dy) >= 700 and min(abs(dx), abs(dy)) <= 0.12 * max(abs(dx), abs(dy), 1):
            cnt[e.dxf.layer] += 1
    return cnt.most_common(1)[0][0] if cnt else None


def _render(doc, f, seeds, xlo, xhi, res=1024, pad_mm=6000):
    """직교벽만 굵게 렌더 → (PIL RGB, transform). transform=(x0,y1,scale)."""
    from PIL import Image, ImageDraw
    sy = [s[2] for s in seeds]
    ylo, yhi = min(sy) - pad_mm, max(sy) + pad_mm
    x0, x1 = xlo - pad_mm, xhi + pad_mm
    scale = res / max(x1 - x0, yhi - ylo)
    IW, IH = int((x1 - x0) * scale), int((yhi - ylo) * scale)
    layer = _wall_layer(doc, f)

    def T(wx, wy):
        return ((wx - x0) * scale, (yhi - wy) * scale)

    def inreg(wx, wy):
        return x0 - 1500 <= wx <= x1 + 1500 and ylo - 1500 <= wy <= yhi + 1500

    def ortho(dx, dy):
        return min(abs(dx), abs(dy)) <= 0.12 * max(abs(dx), abs(dy), 1)

    img = Image.new("RGB", (IW, IH), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    for e in doc.modelspace().query("LINE LWPOLYLINE"):
        if layer and e.dxf.layer != layer:
            continue
        try:
            if e.dxftype() == "LINE":
                a, b = e.dxf.start, e.dxf.end
                ax, ay, bx, by = a.x * f, a.y * f, b.x * f, b.y * f
                dx, dy = bx - ax, by - ay
                if math.hypot(dx, dy) < 700 or not ortho(dx, dy):
                    continue
                if inreg(ax, ay) or inreg(bx, by):
                    dr.line([T(ax, ay), T(bx, by)], fill=(0, 0, 0), width=4)
            else:
                pts = [(p[0] * f, p[1] * f) for p in e.get_points()]
                if not any(inreg(x, y) for x, y in pts):
                    continue
                for i in range(len(pts) - 1):
                    (x1p, y1p), (x2p, y2p) = pts[i], pts[i + 1]
                    dx, dy = x2p - x1p, y2p - y1p
                    if math.hypot(dx, dy) >= 700 and ortho(dx, dy):
                        dr.line([T(x1p, y1p), T(x2p, y2p)], fill=(0, 0, 0), width=4)
        except Exception:
            pass
    return img, (x0, yhi, scale)


def _mask_to_polygon(mask, transform, seed_px):
    """마스크 → 월드좌표 폴리곤. 시드가 속한 연결성분만(bleed 제거) → 최대 윤곽 단순화."""
    import numpy as np
    import cv2
    from scipy import ndimage
    lab, n = ndimage.label(mask)
    sx, sy = seed_px
    if 0 <= sy < lab.shape[0] and 0 <= sx < lab.shape[1] and lab[sy, sx] > 0:
        mask = (lab == lab[sy, sx])
    m = (mask.astype("uint8")) * 255
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None, 0
    c = max(cnts, key=cv2.contourArea)
    eps = 0.01 * cv2.arcLength(c, True)
    c = cv2.approxPolyDP(c, eps, True)
    x0, yhi, scale = transform
    world = [[round(x0 + px / scale, 1), round(yhi - py / scale, 1)] for [[px, py]] in c]
    area_px = int(mask.sum())
    return world, area_px


def find_rooms(dxf_path: str, box_m: float = 4.5) -> dict:
    """DXF → {plan, rooms:[{name, area_m2, confidence, polygon(월드 DXF 좌표)}]}.

    SAM box-prompt로 방을 찾는다. 실패/미가용이면 rooms=[](백엔드 폴백)."""
    try:
        import numpy as np
        import cv2
        import torch
        import ezdxf
        from PIL import Image
        from .room_extract_raster import _to_mm_factor
    except Exception:
        return {"plan": None, "rooms": [], "error": "deps unavailable"}
    try:
        doc = ezdxf.readfile(dxf_path)
        f = _to_mm_factor(doc)
        seeds = _room_seeds(doc, f)
        if len(seeds) < 1:
            return {"plan": None, "rooms": []}
        chosen, xlo, xhi = _isolate_plan(seeds)
        img, transform = _render(doc, f, chosen, xlo, xhi)
        x0, yhi, scale = transform
        px_area = (1.0 / scale) ** 2 / 1e6      # px → m²

        # 가벼운 봉합(문틈) — SAM 입력
        arr = np.array(img.convert("L"))
        wall = (arr < 128).astype("uint8")
        sealed = cv2.morphologyEx(wall, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
        sam_img = Image.fromarray(np.where(sealed > 0, 0, 255).astype("uint8")).convert("RGB")

        model, proc = _load_sam()
        emb = proc(sam_img, return_tensors="pt")
        with torch.no_grad():
            ie = model.get_image_embeddings(emb["pixel_values"])
        bw = int(box_m * 1000 * scale)
        rooms = []
        for (name, wx, wy) in chosen:
            px, py = int((wx - x0) * scale), int((yhi - wy) * scale)
            box = [[max(0, px - bw), max(0, py - bw), px + bw, py + bw]]
            pin = proc(sam_img, input_boxes=[box], input_points=[[[float(px), float(py)]]],
                       input_labels=[[1]], return_tensors="pt")
            with torch.no_grad():
                out = model(input_boxes=pin["input_boxes"], input_points=pin["input_points"],
                            input_labels=pin["input_labels"], image_embeddings=ie, multimask_output=True)
            masks = proc.image_processor.post_process_masks(
                out.pred_masks.cpu(), pin["original_sizes"], pin["reshaped_input_sizes"])[0][0]
            sc = out.iou_scores[0, 0].cpu().numpy()
            tot = masks.shape[-1] * masks.shape[-2]
            best, bj = -9, 0
            for j in range(masks.shape[0]):
                fr = int(masks[j].sum()) / tot
                v = sc[j] - (0 if 0.003 < fr < 0.30 else 1.0)
                if v > best:
                    best, bj = v, j
            m = masks[bj].numpy().astype(bool)
            poly, area_px = _mask_to_polygon(m, transform, (px, py))
            if poly is None or len(poly) < 3:
                continue
            # 방 중심 — 폴리곤은 f-스케일(X*f) 좌표. 뷰어(원시 DXF)용으로 /f 하여 raw 좌표 제공.
            cx = sum(p[0] for p in poly) / len(poly) / f
            cy = sum(p[1] for p in poly) / len(poly) / f
            rooms.append({"name": name, "area_m2": round(float(area_px * px_area), 1),
                          "confidence": round(float(sc[bj]), 2), "polygon": poly,
                          "center": [round(float(cx), 2), round(float(cy), 2)]})
        return {"plan": "isolated", "rooms": rooms}
    except Exception as e:
        return {"plan": None, "rooms": [], "error": str(e)[:200]}


def main():
    import sys
    import json
    if len(sys.argv) < 2:
        print("usage: python -m fireval.ingest.room_sam <dxf> [out.json]")
        return
    res = find_rooms(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else None
    txt = json.dumps(res, ensure_ascii=False)
    if out:
        open(out, "w").write(txt)
        print(f"{len(res.get('rooms', []))} rooms → {out}")
    else:
        print(txt)


if __name__ == "__main__":
    main()
