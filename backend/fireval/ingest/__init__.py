# -*- coding: utf-8 -*-
"""
fireval.ingest — Phase 6: 도면(DXF) → 중간표현(IR=DrawingAnnotation) 브리지.

가이드 Phase 6 의 핵심: dwg→dxf→IR. 실무 DXF 를 읽어 룸·문·**소방설비 객체**를
fireval 스키마(ObjectLabel)로 변환 → 그대로 규칙엔진(engine.checks.check_drawing)에
태운다. 이로써 "합성 도면"뿐 아니라 **임의의 실 DXF** 도 위반 검출 대상이 된다.

  DXF ──(fireopt.dxf_loader: 룸/벽/문)──┐
       └─(INSERT 블록→소방설비 카테고리)─┴─▶ DrawingAnnotation ─▶ check_drawing ─▶ 위반

DWG 는 fireopt.dxf_loader 가 ODA odafc 로 자동변환(설치 시). 설비 인식은 현재
블록명/레이어 규칙 기반(벡터 정확). 래스터/스캔 도면은 향후 검출모델(Phase 1) 연결점.

사용: `from fireval.ingest import dxf_ir` (지연 임포트 — `-m` 실행 시 runpy 경고 회피).
"""
__all__ = ["dxf_ir"]
