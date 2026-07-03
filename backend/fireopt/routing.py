# -*- coding: utf-8 -*-
"""
routing — 가지배관 길이 PROXY (맨해튼 MST). 실제 라우팅 설계가 아닌 비교용 근사치.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.distance import cdist
from scipy.sparse.csgraph import minimum_spanning_tree


def manhattan_mst_length(points, connect_point=None) -> float:
    """점들을 잇는 최소신장트리(맨해튼 거리) 총 길이 [m]. connect_point=입상관 연결점."""
    pts = np.asarray(points, float).reshape(-1, 2)
    if connect_point is not None:
        pts = np.vstack([pts, np.asarray(connect_point, float).reshape(1, 2)])
    if len(pts) < 2:
        return 0.0
    D = cdist(pts, pts, metric="cityblock")
    mst = minimum_spanning_tree(D)
    return float(mst.sum())


def route_orthogonal(head_pts, row_tol=1.0):
    """스프링클러 헤드 → 직교 배관망(가지배관 + 교차배관) + NFTC 구경 산정.

    실무 형태 모사: 같은 y-대(row)의 헤드들을 하나의 '가지배관'(수평선)으로 묶고,
    이를 'TODO 교차배관'(수직선, main_x)에 연결. 입상관(riser)은 교차배관 하단.
    각 구간 관경은 담당(하류) 헤드 수로 NFTC 103 별표 적용.

    반환: (segments, riser, total_len)
      segments: [{p1,p2,dia,heads,kind('branch'|'main'|'riser')}, ...]
    """
    from . import constants as C
    pts = np.asarray(head_pts, float).reshape(-1, 2)
    if len(pts) == 0:
        return [], None, 0.0

    main_x = float(np.median(pts[:, 0]))
    order = np.argsort(pts[:, 1])
    rows = []                      # 각 row = 인덱스 리스트(같은 가지배관)
    cur = [int(order[0])]
    for idx in order[1:]:
        idx = int(idx)
        if pts[idx, 1] - pts[cur[-1], 1] <= row_tol:
            cur.append(idx)
        else:
            rows.append(cur); cur = [idx]
    rows.append(cur)

    row_y = [float(np.mean(pts[r, 1])) for r in rows]   # 오름차순
    riser = (main_x, row_y[0])                          # 교차배관 하단 = 입상관
    segs = []
    total = 0.0

    # 가지배관(수평): 각 row 의 x 범위(+main_x) 를 잇고 헤드수로 구경
    for r, yy in zip(rows, row_y):
        xs = pts[r, 0]
        x0, x1 = float(min(xs.min(), main_x)), float(max(xs.max(), main_x))
        dia = C.pipe_diameter_for_heads(len(r))
        segs.append({"p1": (x0, yy), "p2": (x1, yy), "dia": dia,
                     "heads": len(r), "kind": "branch"})
        total += (x1 - x0)

    # 교차배관(수직, main_x): 인접 row 구간마다 하류(상부) 누적 헤드수로 구경
    cum = [len(r) for r in rows]
    for i in range(len(rows) - 1):
        downstream = sum(cum[i + 1:]) + sum(cum[: i + 1])  # 해당 구간 위로 흐르는 전량 근사
        # 교차배관은 riser(하단)→상단으로 갈수록 헤드 감소: 구간 i~i+1 은 row i+1.. 담당
        downstream = sum(cum[i + 1:])
        dia = C.pipe_diameter_for_heads(max(downstream, 1))
        segs.append({"p1": (main_x, row_y[i]), "p2": (main_x, row_y[i + 1]),
                     "dia": dia, "heads": downstream, "kind": "main"})
        total += abs(row_y[i + 1] - row_y[i])

    # 입상관 구간(전량) — riser 표기용 짧은 세그먼트
    segs.append({"p1": riser, "p2": (main_x, row_y[0]), "dia": C.pipe_diameter_for_heads(len(pts)),
                 "heads": len(pts), "kind": "riser"})
    return segs, riser, total


def tree_edges(points, connect_point=None):
    """MST 엣지 [(i,j), ...] (플롯용). 인덱스는 points(+connect_point) 기준."""
    pts = np.asarray(points, float).reshape(-1, 2)
    if connect_point is not None:
        pts = np.vstack([pts, np.asarray(connect_point, float).reshape(1, 2)])
    if len(pts) < 2:
        return [], pts
    D = cdist(pts, pts, metric="cityblock")
    mst = minimum_spanning_tree(D).tocoo()
    return list(zip(mst.row.tolist(), mst.col.tolist())), pts
