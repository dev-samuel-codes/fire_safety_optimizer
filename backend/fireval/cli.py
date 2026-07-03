# -*- coding: utf-8 -*-
"""
cli — FireVal 데모/빌드: 합성 데이터셋 생성 → 규칙엔진 검증 → 리포트·COCO 출력.

    ./.venv/bin/python -m fireval.cli --out out_fireval

산출물(out_fireval/):
  · annotations/<id>.json   각 도면의 DrawingAnnotation(객체+위반 GT)
  · reports/<id>.md, .csv   소방 법규 검토의견서(조항 인용)
  · coco.json               인식모델 학습용 COCO(객체검출 라벨)
  · dataset_summary.json    시나리오별 위반 요약 + 엔진↔GT 일치
"""
from __future__ import annotations

import os
import json
import argparse
import warnings

from .generate import generate, SCENARIOS
from .generate.synth import SCENARIO_STRUCTURE
from .generate.render import render_and_label
from .engine import checks as CHK
from .schema import labels as LB
from . import report as REP


def build(out_dir="out_fireval", structures=("fireproof",)):
    for sub in ("annotations", "reports", "images"):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)

    anns, summary = [], []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for sc in SCENARIOS:
            st = SCENARIO_STRUCTURE.get(sc, structures[0])
            ann = generate(drawing_id=sc, scenario=sc, structure=st)
            render_and_label(ann, os.path.join(out_dir, "images", f"{sc}.png"))  # pixel_bbox 부여
            engine_v = CHK.check_drawing(ann)                 # 독립 검출(rule_engine)
            viol = [v for v in engine_v if v.status == "violation"]

            # 산출물
            with open(os.path.join(out_dir, "annotations", f"{sc}.json"), "w",
                      encoding="utf-8") as fp:
                fp.write(ann.to_json())
            REP.write_report_md(ann, engine_v, os.path.join(out_dir, "reports", f"{sc}.md"))
            REP.write_violations_csv(ann, engine_v, os.path.join(out_dir, "reports", f"{sc}.csv"))

            gt_ids = sorted(v.rule_id for v in ann.violations)
            eng_ids = sorted(v.rule_id for v in viol)
            anns.append(ann)
            summary.append({
                "drawing_id": sc, "structure": st, "scenario_desc": SCENARIOS[sc],
                "objects": len(ann.objects),
                "gt_violations": gt_ids, "engine_violations": eng_ids,
                "engine_matches_gt": gt_ids == eng_ids,
                "severities": CHK.summarize(engine_v)["by_severity"],
            })

    # COCO(객체검출 학습 라벨) — 합성 GT 객체
    with open(os.path.join(out_dir, "coco.json"), "w", encoding="utf-8") as fp:
        json.dump(LB.to_coco(anns), fp, ensure_ascii=False, indent=1)

    summ = {"n_drawings": len(anns), "scenarios": summary,
            "all_engine_matches_gt": all(s["engine_matches_gt"] for s in summary)}
    with open(os.path.join(out_dir, "dataset_summary.json"), "w", encoding="utf-8") as fp:
        json.dump(summ, fp, ensure_ascii=False, indent=1)
    return summ


def main(argv=None):
    ap = argparse.ArgumentParser("fireval", description="FireVal 합성 데이터셋·규칙검증 빌드")
    ap.add_argument("--out", default="out_fireval")
    args = ap.parse_args(argv)
    s = build(args.out)
    print(f"[FireVal] 도면 {s['n_drawings']}개 생성 · 엔진↔GT 전부일치={s['all_engine_matches_gt']}")
    for row in s["scenarios"]:
        v = row["engine_violations"]
        print(f"  {row['drawing_id']:20s} 위반 {len(v)}건 {v if v else ''}")
    print(f"  산출물: {args.out}/ (annotations·reports·coco.json·dataset_summary.json)")
    return s


if __name__ == "__main__":
    main()
