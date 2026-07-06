"""방 면적 = 벽선 평면그래프의 닫힌 면(polygonize) + leak-guard + 안전마진.

측정 근거(2026-07-06, blind GT):
  - SAM 면적은 실제의 2~7배 과대(NFTC 판정 부적합) → 면적은 이 기하가 담당.
  - 이 기하는 벽이 깨끗한 방에서 정확(±3%), 아니면 **정직하게 거부**.
정직성 규율:
  - 면이 안 닫히면(no_face=문틈) 또는 다른 방 시드를 품으면(병합) → status='needs_boundary'
    → 자동 면적판정 안 함(HITL 경계확인으로). confident-wrong(건물 전체 병합)을 leak-guard가 막음.
  - 면적 상한/하한(_MAX_ROOM/_MIN_ROOM) 밖이면 needs_boundary — 무명공간 병합(과대)·하위면 undercut
    (과소) 의심 방을 자동판정 안 함(6축리뷰 [1][3] 부분방어). 안전그물=HITL 확인(자동 최종 없음).
좌표: 폴리곤·center 모두 room_sam과 동일 규약 — polygon=X*f(mm), center=raw DXF(/f).
버퍼 미사용(6축리뷰 [4][11]): centerline 면이 측정상 ±3% 정확하고, 바깥 buffer는 임계면적
  false-violation + 감지기 point-in-polygon 과대포함(false-pass)을 유발해 제거함.
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
_MAX_ROOM = 400.0  # 단일 방 면적 상한(㎡). 초과 = 무명공간 병합 의심 → needs_boundary(과대 신뢰 방지).
_MIN_ROOM = 2.0    # 하한(㎡). 미만 = 하위면 undercut 의심 → needs_boundary(과소 위반은폐 방지).


def available():
    return _OK


_MAX_GAP = 1000    # 문 개구부(문틈) 최대폭(mm). 이보다 큰 틈은 안 메움(방 병합 방지).


def _bridge(segs, max_gap=_MAX_GAP):
    """벽 조각 끝점 중 **동일직선·마주보는** 쌍을 max_gap 이내면 다리 선분으로 연결(문틈 메움, Jaén식).
    직선 벽의 틈(문 개구부)만 잇고 임의 근접 끝점은 안 이어 방 병합을 막는다."""
    ends = []
    for (ax, ay, bx, by) in segs:
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        if L < 1:
            continue
        ux, uy = dx / L, dy / L
        ends.append((ax, ay, -ux, -uy))   # A끝: 바깥방향 = -벽축
        ends.append((bx, by, ux, uy))       # B끝: 바깥방향 = +벽축
    bridges = []
    n = len(ends)
    for a in range(n):
        xa, ya, uxa, uya = ends[a]
        for b in range(a + 1, n):
            xb, yb, uxb, uyb = ends[b]
            gx, gy = xb - xa, yb - ya
            d = math.hypot(gx, gy)
            if d < 50 or d > max_gap:
                continue
            gxu, gyu = gx / d, gy / d
            if abs(uxa * uxb + uya * uyb) < 0.95:    # 두 벽 평행 아님 → 문 아님
                continue
            if abs(gxu * uxa + gyu * uya) < 0.9:      # 틈이 벽축을 안 따름 → 문 아님
                continue
            if (uxa * gxu + uya * gyu) < 0:           # 서로 안 마주봄
                continue
            bridges.append(LineString([(xa, ya), (xb, yb)]))
    return bridges


def _face(segs, sx, sy, bridge=False):
    """시드를 품는 가장 작은 닫힌 면(직교 긴벽만). bridge=True면 문틈 메움. 없으면 None(정직한 거부)."""
    lines, kept = [], []
    for ax, ay, bx, by in segs:
        if not ((abs(ax - sx) < _WIN and abs(ay - sy) < _WIN)
                or (abs(bx - sx) < _WIN and abs(by - sy) < _WIN)):
            continue
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        if L < _MINLEN or not (abs(dx) < _ORTHO or abs(dy) < _ORTHO):
            continue
        lines.append(LineString([(ax, ay), (bx, by)]))
        kept.append((ax, ay, bx, by))
    if not lines:
        return None
    if bridge:
        lines = lines + _bridge(kept)
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
    # 예외 격리(6축 [9]): 한 LINE의 무효/NaN 좌표가 전체 방추출을 전멸시키지 않게 엔티티별 스킵.
    segs = []
    for e in doc.modelspace().query('LINE'):
        try:
            ax, ay = e.dxf.start.x * f, e.dxf.start.y * f
            bx, by = e.dxf.end.x * f, e.dxf.end.y * f
            if all(map(math.isfinite, (ax, ay, bx, by))):
                segs.append((ax, ay, bx, by))
        except Exception:
            continue

    def _clean(poly, sx, sy):
        # leak-guard: 면이 다른 방 시드를 품으면 = 병합 → 거짓(confident-wrong 방지).
        if poly is None:
            return False
        return sum(1 for (ox, oy) in allpts
                   if abs(ox - sx) + abs(oy - sy) > 1.0 and poly.contains(Point(ox, oy))) == 0

    out = []
    for name, sx, sy in seeds:
        # 2-pass: 브리징 없이 먼저(정확도 보존). 닫힘 실패(no_face)일 때만 문틈 메워 재시도.
        # 병합(다른 시드 포함)이면 브리징이 더 병합시키므로 재시도 안 함(needs_boundary).
        poly, bridged = None, False
        try:
            poly = _face(segs, sx, sy, bridge=False)
            if not _clean(poly, sx, sy):
                p2 = _face(segs, sx, sy, bridge=True) if poly is None else None
                if _clean(p2, sx, sy):
                    poly, bridged = p2, True
                else:
                    poly = None
        except Exception:
            poly = None          # 이 방만 needs_boundary, 나머지는 계속(6축 [9])
        status, area, ring = 'needs_boundary', None, None
        if poly is not None:
            a = poly.area / 1e6
            # 상한/하한 가드(6축 [1][3]): 무명공간 병합(과대)·하위면 undercut(과소) 의심 → 자동판정 거부.
            if _MIN_ROOM <= a <= _MAX_ROOM:
                status = 'geometry'
                area = a                          # 버퍼 미사용 — centerline 원면(6축 [4][11])
                try:
                    ring = [[round(x, 1), round(y, 1)] for x, y in poly.exterior.coords]
                except Exception:
                    status, area = 'needs_boundary', None
        out.append({
            "name": name,
            "center": [round(sx / f, 2), round(sy / f, 2)],
            "area_m2": round(area, 1) if area is not None else None,
            "polygon": ring,
            "status": status,
            "bridged": bridged,   # 문틈 보정으로 닫힌 방(약간 덜 확실 — 참고)
        })
    return out
