# -*- coding: utf-8 -*-
"""
fireval.engine — 규칙 엔진(가이드 Phase 7)의 기하 평가 핵심.

checks.py 는 plan-checkable 규칙(감지면적·수평거리·보행거리·경계구역)을 **순수
기하 + fireopt.constants** 로 판정해 ViolationLabel 을 낸다. 두 곳에서 공유한다:
  · 합성 생성기(Phase 3): 만든 도면이 정말 위반/적합인지 확인해 GT 라벨을 emit
  · 규칙 엔진(Phase 7):   임의 도면의 IR 객체로부터 위반을 검출

→ "생성기가 안다"와 "엔진이 검출한다"가 같은 코드라 GT 와 검출이 정의상 일치.
"""
from . import checks   # noqa: F401

__all__ = ["checks"]
