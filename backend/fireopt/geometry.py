# -*- coding: utf-8 -*-
"""
geometry — 배치/클래시/최적화가 공유하는 순수 2D 기하 도구.

도메인 지식 없음(소방법 모름). 손으로 만든 폴리곤만으로 단위테스트 가능.
좌표는 모두 미터. 거리 metric 구분:
  - 수평거리(radial) : coverage_mask / greedy_set_cover (단순 원형)
  - 보행거리(walking): free_space_graph + geodesic_distance (통로 경로, 벽 우회)
"""
from __future__ import annotations

import math
from itertools import count

import numpy as np
import networkx as nx
from scipy.spatial import cKDTree
from shapely.geometry import Polygon, MultiPolygon, Point
from shapely.prepared import prep


# ---------------------------------------------------------------------------
# 그리드 / 커버리지
# ---------------------------------------------------------------------------
def grid_points(polygon: Polygon, step: float, inset: float = 0.0) -> np.ndarray:
    """폴리곤 내부를 step 간격으로 샘플한 점 (N,2). inset>0 이면 경계에서 안쪽으로."""
    region = polygon.buffer(-inset) if inset > 0 else polygon
    if region.is_empty:
        return np.empty((0, 2))
    minx, miny, maxx, maxy = region.bounds
    if step <= 0:
        raise ValueError("step must be > 0")
    xs = np.arange(minx + step / 2, maxx, step)
    ys = np.arange(miny + step / 2, maxy, step)
    if len(xs) == 0:
        xs = np.array([(minx + maxx) / 2])
    if len(ys) == 0:
        ys = np.array([(miny + maxy) / 2])
    pg = prep(region)
    pts = [(x, y) for x in xs for y in ys if pg.contains(Point(x, y))]
    if not pts:  # 너무 작은 방: 중심점 하나
        c = region.centroid
        return np.array([[c.x, c.y]])
    return np.array(pts)


def _exterior_rings(geom, inset: float):
    g = geom.buffer(-inset) if inset > 0 else geom
    if g.is_empty:
        g = geom  # inset 이 방을 없애면 원본 사용
    if isinstance(g, Polygon):
        return [g.exterior]
    if isinstance(g, MultiPolygon):
        return [p.exterior for p in g.geoms]
    return []


def boundary_points(polygon, step: float, inset: float = 0.3) -> np.ndarray:
    """폴리곤(또는 MultiPolygon) 외곽선을 step 간격으로 샘플(안쪽 inset). 코너 피복 보강."""
    out = []
    for ring in _exterior_rings(polygon, inset):
        if ring is None or ring.length == 0:
            continue
        n = max(4, int(math.ceil(ring.length / step)))
        out += [(ring.interpolate(i / n, normalized=True).x,
                 ring.interpolate(i / n, normalized=True).y) for i in range(n)]
    return np.array(out) if out else np.empty((0, 2))


def coverage_demand(polygon, step: float, inset: float = 0.3) -> np.ndarray:
    """완전피복 솔버용 수요점 = 내부 격자 ∪ 경계 샘플(코너 포함)."""
    interior = grid_points(polygon, step)
    bound = boundary_points(polygon, step, inset)
    if len(interior) == 0:
        return bound
    if len(bound) == 0:
        return interior
    return np.vstack([interior, bound])


def coverage_mask(demand: np.ndarray, centers: np.ndarray, radius: float) -> np.ndarray:
    """각 demand 점이 어떤 center 의 radius 안에 드는지 bool (M,)."""
    demand = np.asarray(demand, float).reshape(-1, 2)
    if demand.size == 0:
        return np.zeros(0, dtype=bool)
    if centers is None or len(centers) == 0:
        return np.zeros(len(demand), dtype=bool)
    centers = np.asarray(centers, float).reshape(-1, 2)
    tree = cKDTree(centers)
    d, _ = tree.query(demand, k=1)
    return d <= radius + 1e-9


def all_covered(demand: np.ndarray, centers: np.ndarray, radius: float) -> bool:
    return bool(coverage_mask(demand, centers, radius).all())


def greedy_set_cover(demand: np.ndarray, candidates: np.ndarray, radius: float,
                     max_picks: int | None = None) -> list:
    """demand 를 radius 로 모두 덮는 candidate 인덱스를 greedy 로 선택.

    매 단계 '아직 안 덮인 demand 를 가장 많이 덮는 후보' 선택. 완전피복 보장(후보가 충분하면).
    """
    demand = np.asarray(demand, float).reshape(-1, 2)
    candidates = np.asarray(candidates, float).reshape(-1, 2)
    if len(demand) == 0 or len(candidates) == 0:
        return []
    dtree = cKDTree(demand)
    # 후보별로 덮는 demand 인덱스 집합 사전계산
    covers = [set(dtree.query_ball_point(c, radius + 1e-9)) for c in candidates]
    uncovered = set(range(len(demand)))
    chosen: list[int] = []
    limit = max_picks if max_picks is not None else len(candidates)
    while uncovered and len(chosen) < limit:
        best, best_gain = -1, 0
        for i, cov in enumerate(covers):
            if i in chosen:
                continue
            gain = len(cov & uncovered)
            if gain > best_gain:
                best, best_gain = i, gain
        if best < 0 or best_gain == 0:
            break  # 더 못 덮음(후보 부족)
        chosen.append(best)
        uncovered -= covers[best]
    return chosen


def spiral_offsets(step: float, rmax: float):
    """원점 주변 나선형 오프셋 (dx,dy) 생성기 — nudge 탐색용. (0,0) 먼저."""
    yield (0.0, 0.0)
    r = step
    while r <= rmax + 1e-9:
        n = max(8, int(2 * math.pi * r / step))
        for k in range(n):
            a = 2 * math.pi * k / n
            yield (r * math.cos(a), r * math.sin(a))
        r += step


# ---------------------------------------------------------------------------
# 보행거리(geodesic) — 통로 그래프
# ---------------------------------------------------------------------------
def free_space_graph(region, step: float):
    """이동가능영역(region: Polygon/MultiPolygon) 위 격자 그래프.

    노드 = region 내부 격자점, 엣지 = 인접(8방향) 격자점 쌍(둘 다 내부),
    가중치 = 유클리드 거리. 반환: (G, coords(N,2), kdtree).
    보행거리는 이 그래프의 최단경로 길이 ≈ 벽을 우회하는 실제 동선.
    """
    if isinstance(region, (Polygon, MultiPolygon)):
        polys = [region] if isinstance(region, Polygon) else list(region.geoms)
    else:
        raise TypeError("region must be Polygon/MultiPolygon")
    minx = min(p.bounds[0] for p in polys); miny = min(p.bounds[1] for p in polys)
    maxx = max(p.bounds[2] for p in polys); maxy = max(p.bounds[3] for p in polys)
    pg = prep(region)
    xs = np.arange(minx + step / 2, maxx, step)
    ys = np.arange(miny + step / 2, maxy, step)
    idx = {}
    coords = []
    for ix, x in enumerate(xs):
        for iy, y in enumerate(ys):
            if pg.contains(Point(x, y)):
                idx[(ix, iy)] = len(coords)
                coords.append((x, y))
    G = nx.Graph()
    G.add_nodes_from(range(len(coords)))
    diag = step * math.sqrt(2)
    for (ix, iy), n in idx.items():
        for dx, dy, w in ((1, 0, step), (0, 1, step), (1, 1, diag), (1, -1, diag)):
            m = idx.get((ix + dx, iy + dy))
            if m is not None:
                G.add_edge(n, m, weight=w)
    coords = np.array(coords) if coords else np.empty((0, 2))
    tree = cKDTree(coords) if len(coords) else None
    return G, coords, tree


def _nearest(tree, coords, p) -> int:
    _, i = tree.query(np.asarray(p, float))
    return int(i)


def geodesic_distance(graph, coords, tree, p, q) -> float:
    """그래프 상 보행거리(p→q). 노드 없거나 비연결이면 inf."""
    if tree is None or len(coords) == 0:
        return math.inf
    a, b = _nearest(tree, coords, p), _nearest(tree, coords, q)
    try:
        return float(nx.shortest_path_length(graph, a, b, weight="weight"))
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return math.inf


def max_walk_to_sources(graph, coords, tree, demand: np.ndarray,
                        sources: np.ndarray) -> float:
    """모든 demand 점에서 가장 가까운 source 까지 보행거리의 최댓값.

    멀티소스 Dijkstra 1회로 계산(각 demand 노드의 최단 source 거리). 소화기/피난 검증용.
    """
    demand = np.asarray(demand, float).reshape(-1, 2)
    sources = np.asarray(sources, float).reshape(-1, 2)
    if tree is None or len(coords) == 0 or len(demand) == 0 or len(sources) == 0:
        return math.inf
    src_nodes = {_nearest(tree, coords, s) for s in sources}
    dist = nx.multi_source_dijkstra_path_length(graph, src_nodes, weight="weight")
    worst = 0.0
    for d in demand:
        n = _nearest(tree, coords, d)
        worst = max(worst, dist.get(n, math.inf))
    return worst
