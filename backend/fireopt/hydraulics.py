# -*- coding: utf-8 -*-
"""
hydraulics — 스프링클러 수리계산(1차 설계검토). NFTC 103 + Hazen-Williams.

소방기술사가 검토 시작점으로 쓸 수 있는 '수리계산서' 수준의 실제 공학 계산:
  · 방수량   q = K√(10P)  (K=80 표준형, P[MPa]) → NFTC 최소 0.1MPa·80L/min
  · 기준개수 NFTC 103 표 2.1.1.1 (수원·설계 동시방사 헤드수)
  · 마찰손실 Hazen-Williams (SI):  Δp[bar/m] = 6.05e5 · Q^1.85 / (C^1.85 · d^4.87)
                                   Q[L/min], d[mm 내경], C=강관 120
  · 최원거리 헤드 → 입상관 경로를 따라 압력손실 누적 → 펌프 필요양정 산정
  · 유속 검토(가지 ≤6, 주배관 ≤10 m/s 권장)

⚠️ 1차 보수적 산정(가지배관은 만관유량 가정). 정밀 면적법·실측 표고·검수는 면허 기술인 몫.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

# ── 상수 ────────────────────────────────────────────────────────────────────
K_FACTOR = 80.0          # 표준형 헤드 K (L/min/√bar). q=K√P (P bar)
MIN_HEAD_PRESSURE_MPA = 0.10   # NFTC 103 2.2.1.10 헤드 최소 방수압
MIN_HEAD_FLOW_LPM = 80.0       # NFTC 103 최소 방수량
HW_C_STEEL = 120         # Hazen-Williams 조도계수(백관/흑관)
BAR_TO_M = 10.197        # 1 bar = 10.197 m 수두
VEL_LIMIT_BRANCH = 6.0   # 가지배관 권장 유속 상한[m/s]
VEL_LIMIT_MAIN = 10.0    # 주배관 권장 유속 상한[m/s]

# 호칭경(mm) → 내경(mm) 근사 (KS D 3507 백관 / SCH40 수준)
INSIDE_DIA = {25: 27.6, 32: 36.2, 40: 42.1, 50: 53.2, 65: 69.0,
              80: 81.0, 90: 90.0, 100: 105.3, 125: 130.0, 150: 155.0}


def head_flow(pressure_mpa: float) -> float:
    """헤드 방수량 q = K√(10P) [L/min]. (P[MPa]→bar=10P)"""
    return K_FACTOR * math.sqrt(max(pressure_mpa, 0) * 10.0)


def hazen_williams_loss(q_lpm: float, dia_mm: float, length_m: float,
                        C: float = HW_C_STEEL) -> float:
    """Hazen-Williams 마찰손실 [bar]. SI: Δp[bar/m]=6.05e5·Q^1.85/(C^1.85·d^4.87)."""
    if q_lpm <= 0 or dia_mm <= 0 or length_m <= 0:
        return 0.0
    unit = 6.05e5 * (q_lpm ** 1.85) / ((C ** 1.85) * (dia_mm ** 4.87))   # bar/m
    return unit * length_m


def velocity(q_lpm: float, dia_mm: float) -> float:
    """관내 평균유속 [m/s]."""
    if dia_mm <= 0:
        return 0.0
    area = math.pi / 4 * (dia_mm / 1000.0) ** 2          # m²
    return (q_lpm / 60000.0) / area                       # (m³/s)/m²


def design_head_count(occupancy: str = "common", num_floors: int = 1,
                      installed_heads: int = 0) -> int:
    """NFTC 103 표 2.1.1.1 헤드 기준개수(수원·동시방사). 설치수보다 크면 설치수로."""
    if num_floors >= 11:
        base = 30                       # 11층 이상·지하가·지하역사
    elif occupancy in ("sales", "complex", "amusement", "factory"):
        base = 30                       # 판매·복합·위락·공장(특수가연물)
    elif occupancy == "apartment":
        base = 10                       # 공동주택(아파트등)
    else:
        base = 20                       # 그 밖(8~10층 이하 일반)
    return max(1, min(base, installed_heads) if installed_heads else base)


# ── 경로(최원거리 헤드 → 입상관) 추출 ────────────────────────────────────────
def _node(p, q=2):
    return (round(p[0], q), round(p[1], q))


def _on_seg(a, b, p, tol=0.05):
    """점 p 가 선분 a-b 위(끝점 제외)에 있는가(공선 + 범위)."""
    cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    seglen = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
    if abs(cross) / seglen > tol:
        return False
    dot = (p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])
    return tol < dot / (seglen ** 2) < 1 - tol / seglen


def _split_at_junctions(segments):
    """T분기(주배관 중간에 가지가 닿는 점)에서 세그먼트를 쪼개 노드를 일치시킨다."""
    pts = set()
    for s in segments:
        pts.add(_node(s["p1"]))
        pts.add(_node(s["p2"]))
    out = []
    for s in segments:
        a, b = _node(s["p1"]), _node(s["p2"])
        on = [p for p in pts if p not in (a, b) and _on_seg(a, b, p)]
        on.sort(key=lambda p: math.hypot(p[0] - a[0], p[1] - a[1]))
        chain = [a] + on + [b]
        for i in range(len(chain) - 1):
            s2 = dict(s)
            s2["p1"], s2["p2"] = chain[i], chain[i + 1]
            out.append(s2)
    return out


def _remote_path(segments, riser):
    """입상관에서 가장 먼 헤드(가지 끝)까지의 세그먼트 경로 [(seg, length), ...]."""
    if not segments or riser is None:
        return []
    segments = _split_at_junctions(segments)
    # 인접 그래프(무방향): 노드→[(이웃노드, seg)]
    adj = {}
    for s in segments:
        a, b = _node(s["p1"]), _node(s["p2"])
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        adj.setdefault(a, []).append((b, s, L))
        adj.setdefault(b, []).append((a, s, L))
    start = _node(riser)
    if start not in adj:                              # riser 노드가 세그먼트와 불일치 시 최근접
        start = min(adj, key=lambda n: math.hypot(n[0] - riser[0], n[1] - riser[1]))
    # Dijkstra(거리 최대 노드 탐색 위해 전체 최단거리 계산)
    import heapq
    dist = {start: 0.0}
    prev = {}
    pq = [(0.0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, 1e18):
            continue
        for v, s, L in adj[u]:
            nd = d + L
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                prev[v] = (u, s, L)
                heapq.heappush(pq, (nd, v))
    far = max(dist, key=dist.get)
    # far → start 역추적
    path = []
    cur = far
    while cur in prev:
        u, s, L = prev[cur]
        path.append((s, L))
        cur = u
    path.reverse()                                    # 입상관쪽→원거리쪽 순
    return path


# ── 시스템 수리계산 ──────────────────────────────────────────────────────────
@dataclass
class HydraulicResult:
    design_head_count: int
    design_flow_lpm: float
    water_source_m3: float
    required_pressure_mpa: float
    pump_head_m: float
    pump_flow_lpm: float
    remote_path_len_m: float
    rows: list = field(default_factory=list)         # 경로 세그먼트별 계산표
    checks: dict = field(default_factory=dict)
    notes: str = ""

    def to_dict(self):
        return asdict(self)


def calc_system(segments, riser, occupancy="common", num_floors=1,
                installed_heads=0, ceiling_h=2.6, pump_height=0.0,
                duration_min=20) -> HydraulicResult:
    """스프링클러 1차 수리계산. 최원거리 경로 압력손실 → 펌프 필요양정."""
    n_design = design_head_count(occupancy, num_floors, installed_heads)
    q_head = MIN_HEAD_FLOW_LPM
    design_flow = n_design * q_head                                   # L/min
    water_m3 = design_flow * duration_min / 1000.0                    # 수원 V

    path = _remote_path(segments, riser)             # [(seg, L)] 입상관쪽→원거리
    # 원거리 헤드에서 시작: 최소압 0.1MPa(=1bar), 거기서 입상관으로 누적
    p_bar = MIN_HEAD_PRESSURE_MPA * 10.0
    rows = []
    vmax_branch = vmax_main = 0.0
    # 원거리→입상관 순서로 누적(경로를 뒤집어 순회)
    for s, L in reversed(path):
        heads = max(int(s.get("heads", 1)), 1)
        flow = min(heads, n_design) * q_head                          # 보수적: 만관유량
        dia_in = INSIDE_DIA.get(int(s["dia"]), float(s["dia"]) * 0.9)
        loss = hazen_williams_loss(flow, dia_in, L)                   # bar
        v = velocity(flow, dia_in)
        if s.get("kind") == "branch":
            vmax_branch = max(vmax_branch, v)
        else:
            vmax_main = max(vmax_main, v)
        p_bar += loss
        rows.append({
            "kind": s.get("kind", ""), "nominal_mm": int(s["dia"]),
            "inside_mm": round(dia_in, 1), "length_m": round(L, 2),
            "flow_lpm": round(flow, 1), "velocity_ms": round(v, 2),
            "unit_loss_bar_m": round(loss / L, 4) if L else 0.0,
            "seg_loss_bar": round(loss, 4),
            "cum_pressure_mpa": round(p_bar / 10.0, 4),
        })
    # 표고 손실(단층: 입상관 높이만큼) + 입상관 정수두
    elev_m = ceiling_h + pump_height
    p_bar += elev_m / BAR_TO_M
    required_mpa = p_bar / 10.0
    pump_head = required_mpa * 10.0 * BAR_TO_M                        # MPa→m (≈102·MPa)
    path_len = sum(L for _, L in path)

    checks = {
        "head_min_pressure_ok": True,                 # 시작점을 0.1MPa로 가정
        "branch_velocity_ok": vmax_branch <= VEL_LIMIT_BRANCH,
        "main_velocity_ok": vmax_main <= VEL_LIMIT_MAIN,
        "max_branch_velocity_ms": round(vmax_branch, 2),
        "max_main_velocity_ms": round(vmax_main, 2),
    }
    notes = ("Hazen-Williams(C=120)·K=80 표준형 헤드·최소 0.1MPa/80L/min. "
             "가지배관 만관유량 가정(보수적 1차). 정밀 면적법·실측표고·검수는 기술인 몫.")
    return HydraulicResult(
        design_head_count=n_design, design_flow_lpm=round(design_flow, 1),
        water_source_m3=round(water_m3, 2), required_pressure_mpa=round(required_mpa, 4),
        pump_head_m=round(pump_head, 1), pump_flow_lpm=round(design_flow, 1),
        remote_path_len_m=round(path_len, 2), rows=rows, checks=checks, notes=notes)


def write_calc_sheet(hr: HydraulicResult, path_csv: str):
    """수리계산서 CSV(경로 세그먼트별 표)."""
    import csv
    with open(path_csv, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["구간", "호칭경(mm)", "내경(mm)", "길이(m)", "유량(L/min)",
                    "유속(m/s)", "단위손실(bar/m)", "구간손실(bar)", "누적압력(MPa)"])
        for r in hr.rows:
            w.writerow([r["kind"], r["nominal_mm"], r["inside_mm"], r["length_m"],
                        r["flow_lpm"], r["velocity_ms"], r["unit_loss_bar_m"],
                        r["seg_loss_bar"], r["cum_pressure_mpa"]])
        w.writerow([])
        w.writerow(["기준개수", hr.design_head_count, "설계유량(L/min)", hr.design_flow_lpm])
        w.writerow(["수원(㎥)", hr.water_source_m3, "필요압력(MPa)", hr.required_pressure_mpa])
        w.writerow(["펌프양정(m)", hr.pump_head_m, "펌프토출(L/min)", hr.pump_flow_lpm])
    return path_csv
