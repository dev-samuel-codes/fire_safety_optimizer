# -*- coding: utf-8 -*-
"""
cli — FireOpt 전체 파이프라인 오케스트레이션 (Stage 0→4).

    python -m fireopt.cli --arch sample_ifc/Duplex_A_20110907.ifc \
        --elec sample_ifc/Duplex_Electrical_20121207.ifc --out out --floors 2

run_pipeline() 는 웹앱에서도 직접 호출 가능하도록 artifacts dict 를 반환한다.
"""
from __future__ import annotations

import os
import json
import argparse
import warnings
import datetime
from dataclasses import dataclass
from shapely.ops import unary_union

from . import ifc_loader as L
from . import placement as P
from . import clash as X
from . import optimize as O
from . import routing as RT
from . import hydraulics as HYD
from . import cost as CST
from . import render as R
from . import export as E
from . import report as REP
from . import overlay as OV


@dataclass
class Config:
    arch: str
    elec: str = ""
    mep_paths: list = None          # 여러 공종(전기/기계/위생) 경로. 있으면 elec 대신 사용
    out: str = "out"
    structure: str = "fireproof"
    occupancy: str = "common"
    floors: int = 2
    elevation: float = None          # None 이면 자동(룸 최다 층) 선택
    storey_tol: float = 1.5
    margin: float = 0.15
    ceiling_z: float = 2.6
    z_band: float = 1.6
    detector_type: str = "smoke_12"


def build_config(args) -> Config:
    return Config(arch=args.arch, elec=args.elec, out=args.out,
                  structure=args.structure, occupancy=args.occupancy,
                  floors=args.floors, elevation=args.elevation,
                  storey_tol=args.storey_tol, margin=args.margin,
                  detector_type=args.detector_type)


def _sprinkler_pipe(layout, region) -> float:
    pts = [p.point for p in layout.placements.get("sprinkler", [])]
    cp = (region.centroid.x, region.centroid.y) if region is not None else None
    return RT.manhattan_mst_length(pts, connect_point=cp)


def run_pipeline(cfg: Config) -> dict:
    os.makedirs(cfg.out, exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        # Stage 0 — 건축 도면 → 룸. IFC(.ifc) 또는 실무 CAD(.dxf/.dwg) 자동 분기.
        if os.path.splitext(cfg.arch)[1].lower() in (".dxf", ".dwg"):
            from . import dxf_loader as DL
            arch = DL.load(cfg.arch, structure=cfg.structure, occupancy=cfg.occupancy)
            elev, rooms = 0.0, arch.rooms          # CAD 평면도는 단층
        else:
            arch = L.load(cfg.arch, structure=cfg.structure, occupancy=cfg.occupancy)
            elev = cfg.elevation if cfg.elevation is not None else L.auto_storey_elevation(arch.rooms)
            rooms = L.rooms_on_storey(arch.rooms, elev, cfg.storey_tol) or arch.rooms
        region = unary_union([r.polygon for r in rooms]) if rooms else None

        # Stage 1 — 자동 배치 (BEFORE)
        pcfg = P.PlaceConfig(structure=cfg.structure, occupancy=cfg.occupancy,
                             num_floors=cfg.floors, detector_type=cfg.detector_type)
        before = P.build_layout(rooms, pcfg, doors=arch.door_points)

        # Stage 2 — 타 공종(전기/기계) IFC·DXF → MEP, 클래시 감지 (BEFORE). 여러 공종 합산.
        ceiling_abs = elev + cfg.ceiling_z
        mep_paths = cfg.mep_paths if cfg.mep_paths else ([cfg.elec] if cfg.elec else [])
        meps = []
        for p in mep_paths:
            if not (p and os.path.exists(p)):
                continue
            if p.lower().endswith(".ifc"):
                m, _ = L.open_model(p)
                meps += X.to_mep_geoms(m)
            elif p.lower().endswith((".dxf", ".dwg")):
                meps += X.to_mep_geoms_dxf(p, ceiling_z=ceiling_abs)
            else:
                warnings.warn(f"지원하지 않는 MEP 형식 — 건너뜀: {p}", RuntimeWarning)
        before_clashes = X.detect(
            X.fire_geoms(before, cfg.margin, ceiling_abs, floor_z=elev),
            meps, clearance=cfg.margin)

        # Stage 3 — 재최적화 (AFTER)
        after, after_clashes, disp = O.resolve(before, meps, rooms, margin=cfg.margin,
                                               ceiling_z=ceiling_abs, floor_z=elev, cfg=pcfg)

        # 배관길이 프록시
        before_pipe = _sprinkler_pipe(before, region)
        after_pipe = _sprinkler_pipe(after, region)

        # Stage 4 — 비용/리포트/시각화/DXF
        creport = CST.compare(before, after, before_pipe, after_pipe,
                              before_clashes, after_clashes)

        # 배관망(직교 라우팅 + NFTC 구경) · 입상관 · 도면 메타 — DXF/PNG 공용
        sp_pts = [p.point for p in after.placements.get("sprinkler", [])]
        cp = (region.centroid.x, region.centroid.y) if region is not None else None
        pipe_edges, pipe_pts = RT.tree_edges(sp_pts, connect_point=cp)
        segs, riser, _plen = RT.route_orthogonal(sp_pts)
        # 스프링클러 수리계산(NFTC 103 + Hazen-Williams) — 1차 설계검토
        hyd = HYD.calc_system(segs, riser, occupancy=cfg.occupancy,
                              num_floors=cfg.floors, installed_heads=len(sp_pts),
                              ceiling_h=cfg.ceiling_z)
        meta = {"project": os.path.splitext(os.path.basename(cfg.arch))[0],
                "title": "소방시설 평면 배치도", "scale": "N.T.S (참고)",
                "date": datetime.date.today().isoformat(), "dwg_no": "FP-101",
                "code": "NFTC/NFPC · 건축법 피난·방화규칙"}

        art = {}
        art["clashes_csv"] = os.path.join(cfg.out, "clashes_before.csv")
        X.export_csv(before_clashes, art["clashes_csv"])
        art["clashes_after_csv"] = os.path.join(cfg.out, "clashes_after.csv")
        X.export_csv(after_clashes, art["clashes_after_csv"])
        art["before_after_png"] = R.plot_before_after(
            rooms, meps, before, before_clashes, after, after_clashes,
            os.path.join(cfg.out, "before_after.png"))
        art["dxf"] = E.to_dxf(rooms, after, meps, after_clashes,
                              os.path.join(cfg.out, "fireopt_after.dxf"),
                              walls=arch.wall_polys, doors=arch.door_points,
                              pipe_segments=segs, riser=riser,
                              checks=after.checks, meta=meta, hydraulics=hyd.to_dict())
        art["cost_csv"] = REP.write_cost_csv(creport, os.path.join(cfg.out, "cost.csv"))
        art["summary_md"] = REP.write_summary(creport, after.checks,
                                              os.path.join(cfg.out, "summary.md"))
        art["cost_json"] = os.path.join(cfg.out, "cost.json")
        with open(art["cost_json"], "w", encoding="utf-8") as fp:
            json.dump(creport.to_dict(), fp, ensure_ascii=False, indent=1)
        art["calc_sheet_csv"] = HYD.write_calc_sheet(
            hyd, os.path.join(cfg.out, "hydraulic_calc.csv"))
        # 레이어 오버레이 뷰어 장면(IFC·DXF 공통) — 재최적화 전 간섭검토 상태
        try:
            OV.scene_from_pipeline(
                os.path.join(cfg.out, "scene.json"), rooms, arch.wall_polys,
                arch.door_points, before, meps, region, margin=cfg.margin,
                ceiling_z=ceiling_abs, floor_z=elev, state="before")
            art["scene_json"] = os.path.join(cfg.out, "scene.json")
        except Exception as e:
            warnings.warn(f"오버레이 장면 생성 실패: {e}", RuntimeWarning)

        result = {
            "config": cfg.__dict__,
            "schema": arch.schema,
            "storeys": arch.storeys,
            "elevation_used": round(elev, 3),
            "rooms": len(rooms),
            "total_area_m2": round(sum(r.area for r in rooms), 1),
            "mep_count": len(meps),
            "before": {"clashes": X.summarize(before_clashes),
                       "counts": CST.facility_counts(before),
                       "pipe_m": round(before_pipe, 1)},
            "after": {"clashes": X.summarize(after_clashes),
                      "counts": CST.facility_counts(after),
                      "pipe_m": round(after_pipe, 1),
                      "moved": after.checks.get("reopt", {})},
            "cost": creport.to_dict(),
            "checks": after.checks,
            "hydraulics": hyd.to_dict(),
            "artifacts": art,
        }
        # 소방시설 평면 배치도(시공도면 스타일: 벽체·표준기호·배관·범례·표제란)
        art["drawing_png"] = R.plot_drawing(
            rooms, after, os.path.join(cfg.out, "drawing_plan.png"),
            meta=meta, pipe_edges=pipe_edges, pipe_pts=pipe_pts)

        # CAD 스타일 배관 평면도(직교 라우팅 + NFTC 구경 산정 SP.nn) — 어두운/밝은 2종
        pmeta = {**meta, "dwg_no": "FP-201"}
        art["piping_dark_png"] = R.plot_piping_cad(
            rooms, after, segs, riser, os.path.join(cfg.out, "piping_plan_dark.png"),
            meta=pmeta, theme="dark")
        art["piping_light_png"] = R.plot_piping_cad(
            rooms, after, segs, riser, os.path.join(cfg.out, "piping_plan_light.png"),
            meta=pmeta, theme="light")

        # 단일 대시보드 PNG (서버 없이 VSCode 등에서 바로 열람)
        art["dashboard_png"] = R.plot_dashboard(
            result, art["before_after_png"], os.path.join(cfg.out, "dashboard.png"))

        with open(os.path.join(cfg.out, "result.json"), "w", encoding="utf-8") as fp:
            json.dump(result, fp, ensure_ascii=False, indent=1, default=str)
        return result


def main(argv=None):
    ap = argparse.ArgumentParser("fireopt", description="FireOpt 소방 설계 자동 최적화")
    ap.add_argument("--arch", required=True, help="건축 IFC 경로")
    ap.add_argument("--elec", default="", help="전기/기계 IFC 경로(클래시 감지용)")
    ap.add_argument("--out", default="out")
    ap.add_argument("--structure", default="fireproof", choices=["fireproof", "non_fireproof", "noncombustible"])
    ap.add_argument("--occupancy", default="common")
    ap.add_argument("--floors", type=int, default=2)
    ap.add_argument("--elevation", type=float, default=0.0, help="대상 층 표고(m)")
    ap.add_argument("--storey-tol", dest="storey_tol", type=float, default=1.5)
    ap.add_argument("--margin", type=float, default=0.15, help="이격거리(m)")
    ap.add_argument("--detector-type", dest="detector_type", default="smoke_12")
    args = ap.parse_args(argv)

    cfg = build_config(args)
    res = run_pipeline(cfg)
    b, a = res["before"]["clashes"], res["after"]["clashes"]
    print(f"[FireOpt] rooms={res['rooms']} area={res['total_area_m2']}m2 MEP={res['mep_count']}")
    print(f"  클래시 hard: {b['hard']} → {a['hard']}  (해소 {b['hard']-a['hard']}건)")
    print(f"  재배치: {res['after']['moved']}")
    print(f"  총비용: {res['cost']['before']['total_cost']:,} → {res['cost']['after']['total_cost']:,}원")
    print(f"  산출물: {res['out'] if 'out' in res else cfg.out}/ (before_after.png, fireopt_after.dxf, cost.csv, summary.md)")
    return res


if __name__ == "__main__":
    main()
