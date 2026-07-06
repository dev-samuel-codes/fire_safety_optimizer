"""방 면적 = 벽선 평면그래프의 닫힌 면(polygonize) + leak-guard + 안전마진.

측정 근거(2026-07-06, blind GT):
  - SAM 면적은 실제의 2~7배 과대(NFTC 판정 부적합) → 면적은 이 기하가 담당.
  - 이 기하는 벽이 깨끗한 방에서 정확(±3%), 아니면 **정직하게 거부**.
정직성 규율:
  - 면이 안 닫히면(no_face=문틈) 또는 다른 방 시드를 품으면(병합) → status='needs_boundary'
    → 자동 면적판정 안 함(HITL 경계확인으로). confident-wrong(건물 전체 병합)을 leak-guard가 막음.
안전방향(과소=위험): 벽 내측 면은 약간 과소 → _SAFETY만큼 바깥 buffer로 과대(안전)쪽 바이어스.

좌표: 폴리곤·center 모두 room_sam과 동일 규약 — polygon=X*f(mm), center=raw DXF(/f).
"""
import math

try:
    import ezdxf
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union, polygonize
    _OK = True
except Exception:
    _OK = False

from .room_extract_raster import _to_mm_factor
from .room_sam import _room_seeds

_WIN = 8000        # 시드 주변 창 (mm)
_MINLEN = 1200     # 벽 최소 길이 (짧은 가구/치수/노트 배제)
_ORTHO = 120       # 축정렬 허용오차 (대각 단면선·지시선 배제 → 방 오분할 방지)
_SAFETY = 120      # 안전마진(mm): 면을 바깥으로 buffer. 과소(위험)를 과대(안전)로 바이어스.


def available():
    return _OK


def _face(segs, sx, sy):
    """시드를 품는 가장 작은 닫힌 면(직교 긴벽만). 없으면 None(정직한 거부)."""
    lines = []
    for ax, ay, bx, by in segs:
        if not ((abs(ax - sx) < _WIN and abs(ay - sy) < _WIN)
                or (abs(bx - sx) < _WIN and abs(by - sy) < _WIN)):
            continue
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        if L < _MINLEN or not (abs(dx) < _ORTHO or abs(dy) < _ORTHO):
            continue
        lines.append(LineString([(ax, ay), (bx, by)]))
    if not lines:
        return None
    faces = list(polygonize(unary_union(lines)))
    seed = Point(sx, sy)
    cont = [g for g in faces if g.contains(seed)]
    return min(cont, key=lambda g: g.area) if cont else None


def geom_faces(dxf_path):
    """각 방 시드에서 기하 면추출. 반환 [{name, center, area_m2|None, polygon|None, status}].
    status='geometry'(깨끗한 단독 면, area/polygon 유효) | 'needs_boundary'(거부: 문틈/병합 → HITL).
    """
    if not _OK:
        return []
    doc = ezdxf.readfile(dxf_path)
    f = _to_mm_factor(doc)
    seeds = _room_seeds(doc, f)
    allpts = [(sx, sy) for _, sx, sy in seeds]
    segs = [(e.dxf.start.x * f, e.dxf.start.y * f, e.dxf.end.x * f, e.dxf.end.y * f)
            for e in doc.modelspace().query('LINE')]
    out = []
    for name, sx, sy in seeds:
        poly = _face(segs, sx, sy)
        status, area, ring = 'needs_boundary', None, None
        if poly is not None:
            # leak-guard: 면이 다른 방 시드를 품으면 = 병합 → 거부(confident-wrong 방지)
            others = sum(1 for (ox, oy) in allpts
                         if abs(ox - sx) + abs(oy - sy) > 1.0 and poly.contains(Point(ox, oy)))
            if others == 0:
                safe = poly.buffer(_SAFETY)          # 안전마진(과대=안전)
                status = 'geometry'
                area = safe.area / 1e6
                try:
                    ring = [[round(x, 1), round(y, 1)] for x, y in safe.exterior.coords]
                except Exception:
                    ring = None
        out.append({
            "name": name,
            "center": [round(sx / f, 2), round(sy / f, 2)],
            "area_m2": round(area, 1) if area is not None else None,
            "polygon": ring,
            "status": status,
        })
    return out
