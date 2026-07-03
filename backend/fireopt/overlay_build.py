# -*- coding: utf-8 -*-
"""
overlay_build — 다공종 오버레이 장면(out/scene.json) 생성 스크립트.

    ./.venv/bin/python -m fireopt.overlay_build
사용 가능한 공종 IFC(전기/기계/위생)를 모두 메싱해 공종별 도형+충돌을 캐시한다.
(메싱이 무거워 ~2분 소요; 한 번만 만들어두면 뷰어는 즉시 로드)
"""
import os
from . import overlay

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 소방 설계 자동화/
S = os.path.join(BASE, "sample_ifc")
OUT = os.path.join(BASE, "out")


def main():
    os.makedirs(OUT, exist_ok=True)
    candidates = {
        "electrical":   "Duplex_Electrical_20121207.ifc",
        "mechanical":   "Duplex_MEP_20110907.ifc",
        "mechanical_m": "Duplex_M_20111024_ROOMS_AND_SPACES.ifc",
        "plumbing":     "Duplex_Plumbing_20121113.ifc",
    }
    disc_paths = {k: os.path.join(S, f) for k, f in candidates.items()
                  if os.path.exists(os.path.join(S, f))}
    print("공종 메싱 시작(무거움):", list(disc_paths))
    sc = overlay.build_and_save(
        os.path.join(OUT, "scene.json"),
        arch_path=os.path.join(S, "Duplex_A_20110907.ifc"),
        disc_list=overlay.disc_list_from_paths(disc_paths), floors=2, elevation=0.0)
    print("저장: out/scene.json")
    print("rooms:", len(sc["rooms"]), "pipes:", len(sc["pipes"]))
    for k, d in sc["disciplines"].items():
        print(f"  {k:11} 도형 {d['geom_count']:4}개  충돌 {d['clash_count']:3}건")


if __name__ == "__main__":
    main()
