# -*- coding: utf-8 -*-
"""
FireOpt — 한국 소방 화재안전기술기준(NFTC) 검증 상수 (Single Source of Truth)
================================================================================

이 모듈은 FireOpt 전체에서 사용하는 **유일한** 소방법 상수 출처입니다.
모든 값은 2026-06 시점 현행 NFTC(화재안전기술기준) / NFPC(화재안전성능기준) 및
건축법 시행령·피난방화규칙을 근거로 하며, 다중 에이전트 조사 후 **적대적 검증**으로
교정된 값을 반영했습니다. (조항·교정 이력은 각 항목 condition에 표기)

  ⚠️ 2022.12 NFSC → NFPC(성능)+NFTC(기술) 개편 후의 현행 번호를 사용합니다.
  ⚠️ 값을 바꿀 때는 tests/test_constants.py 의 회귀 테스트가 함께 깨지는지 확인하세요.

설계 규약(코드 전반 공통):
  - structure : "fireproof"(내화) | "non_fireproof"(비내화/기타) | "noncombustible"(불연 ~ 내화 취급)
  - 거리 metric 은 condition 에 (radial=수평거리), (walking path=보행거리), (vertical=수직거리)로 명시.
    radial 만 단순 원형 버퍼로 처리하고, walking path 는 통로 그래프 BFS 로 계산해야 함.
"""
from __future__ import annotations

from types import MappingProxyType


def _freeze(d: dict) -> MappingProxyType:
    """1-depth 동결: 최상위 dict 를 읽기전용으로 (실수 변경 방지)."""
    return MappingProxyType({k: MappingProxyType(v) if isinstance(v, dict) else v
                             for k, v in d.items()})


# ============================================================================
# 1) 스프링클러설비 — NFTC 103 / NFPC 103
# ============================================================================
SPRINKLER = _freeze({
    # 헤드 수평거리 R (각 부분 → 가장 가까운 헤드). 배치의 핵심 값.
    "R_stage_special":   {"value": 1.7, "unit": "m", "metric_type": "수평거리(radial)",
                          "condition": "무대부 또는 특수가연물 저장·취급 장소(구조 무관). NFTC 103 2.7.3"},
    "R_non_fireproof":   {"value": 2.1, "unit": "m", "metric_type": "수평거리(radial)",
                          "condition": "비내화(기타)구조 특정소방대상물(무대부·랙크식창고·공동주택 제외). NFTC 103 2.7.3"},
    "R_fireproof":       {"value": 2.3, "unit": "m", "metric_type": "수평거리(radial)",
                          "condition": "내화구조 특정소방대상물(무대부·랙크식창고·공동주택 제외). NFTC 103 2.7.3"},
    "R_apartment":       {"value": 3.2, "unit": "m", "metric_type": "수평거리(radial)",
                          "condition": "공동주택(아파트등) 세대 내 거실. NFTC 103 2.7.3.4 / 608"},
    # 배치 산식 계수
    "square_pitch_factor": {"value": 1.41421356, "unit": "-", "metric_type": "배치산식계수",
                            "condition": "정방형 배치 헤드 간격 S = factor × R (= 2R·cos45°). 가지배관 간격 L 동일"},
    "diagonal_factor":     {"value": 2.0, "unit": "-", "metric_type": "배치산식계수",
                            "condition": "장방형 배치: 헤드 보호 대각선 ≤ 2R(살수반경 직경)"},
    # 헤드-부착면/살수 장애 (NFTC 103 2.7.7) — 적대적 검증 교정 반영
    "reflector_to_ceiling_max": {"value": 0.3, "unit": "m", "metric_type": "수직이격(최대)",
                                 "condition": "[CORRECTED 0.6→0.3] 반사판-부착면(천장/반자) 30cm 이하. NFTC 103 2.7.7.2"},
    "spray_clear_radius":  {"value": 0.6, "unit": "m", "metric_type": "살수공간반경(최소)",
                            "condition": "살수 비장애 위해 헤드 반경 60cm 이상 공간 확보. NFTC 103 2.7.7.1"},
    "head_to_wall_min":    {"value": 0.1, "unit": "m", "metric_type": "수평이격(최소)",
                            "condition": "[CORRECTED] 벽-헤드 공간 10cm 이상(최소). NFTC 103 2.7.7.1"},
    "spray_clear_downward": {"value": 0.45, "unit": "m", "metric_type": "수직이격(하방살수)",
                             "condition": "반사판 하방 0.45m 살수공간 확보. NFTC 103 2.7.7.7"},
    "spray_clear_horizontal": {"value": 0.3, "unit": "m", "metric_type": "수평이격(살수)",
                               "condition": "반사판 수평방향 0.3m 살수공간 확보. NFTC 103 2.7.7.7"},
    # 측벽형
    "sidewall_one_side_width_max": {"value": 4.5, "unit": "m", "metric_type": "수평거리(실폭)",
                                    "condition": "폭 4.5m 미만 실: 측벽형 한쪽 일렬 설치. NFTC 103 2.7.7.8"},
    "sidewall_both_side_width_max": {"value": 9.0, "unit": "m", "metric_type": "수평거리(실폭)",
                                     "condition": "폭 4.5~9m 실: 측벽형 양쪽 일렬 설치. NFTC 103 2.7.7.8"},
    "sidewall_long_spacing_max":   {"value": 3.6, "unit": "m", "metric_type": "직선거리(헤드간격)",
                                    "condition": "측벽형 긴변 방향 헤드 간격 3.6m 이내. NFTC 103 2.7.7.8"},
    # 천장-반자 / 개구부 / 설치제외 (NFTC 103 2.7.7, 2.12)
    "ceiling_void_both_install": {"value": 2.0, "unit": "m", "metric_type": "직선거리(천장-반자)",
                                  "condition": "천장-반자 2m 이상이면 양쪽 모두 헤드 설치. NFTC 103 2.7.7.2"},
    "opening_spacing":      {"value": 2.5, "unit": "m", "metric_type": "직선거리(개구부)",
                             "condition": "연소우려 개구부 상하좌우 2.5m 간격 헤드. NFTC 103 2.7.7.6"},
    "opening_inner_offset": {"value": 0.15, "unit": "m", "metric_type": "직선거리(개구부)",
                             "condition": "연소우려 개구부 내측면 0.15m 이내. NFTC 103 2.7.7.6"},
    "exclude_void_unburn_max":      {"value": 1.0, "unit": "m", "metric_type": "설치제외(직선거리)",
                                     "condition": "천장·반자 양쪽 불연+사이 1m 미만 설치제외. NFTC 103 2.12.1"},
    "exclude_void_part_unburn_max": {"value": 0.5, "unit": "m", "metric_type": "설치제외(직선거리)",
                                     "condition": "천장·반자 한쪽 불연 외+사이 0.5m 미만 설치제외. NFTC 103 2.12.1"},
    "exclude_void_narrow_width_max": {"value": 1.2, "unit": "m", "metric_type": "설치제외(직선거리)",
                                      "condition": "불연 벽+천장-반자 2m 이상+폭 1.2m 미만 설치제외. NFTC 103 2.12.1"},
})


# ============================================================================
# 2) 자동화재탐지설비 감지기 — NFTC 203 / NFPC 203
#    감지면적 = 감지기 1개가 담당하는 바닥면적(㎡). 개수 = ceil(실면적 / 감지면적).
# ============================================================================
DETECTOR = _freeze({
    # --- 열감지기 스포트형: 부착높이 4m 미만, fire=내화 / other=기타 (NFTC 203 표 2.4.3.5) ---
    "diff_spot_1_lt4_fire":  {"value": 90, "unit": "m2", "metric_type": "감지면적",
                              "condition": "차동식 스포트형 1종, 4m 미만, 내화"},
    "diff_spot_1_lt4_other": {"value": 50, "unit": "m2", "metric_type": "감지면적",
                              "condition": "차동식 스포트형 1종, 4m 미만, 기타"},
    "diff_spot_2_lt4_fire":  {"value": 70, "unit": "m2", "metric_type": "감지면적",
                              "condition": "차동식 스포트형 2종, 4m 미만, 내화"},
    "diff_spot_2_lt4_other": {"value": 40, "unit": "m2", "metric_type": "감지면적",
                              "condition": "차동식 스포트형 2종, 4m 미만, 기타"},
    "fixed_spot_special_lt4_fire":  {"value": 70, "unit": "m2", "metric_type": "감지면적",
                                     "condition": "정온식 스포트형 특종, 4m 미만, 내화"},
    "fixed_spot_special_lt4_other": {"value": 40, "unit": "m2", "metric_type": "감지면적",
                                     "condition": "정온식 스포트형 특종, 4m 미만, 기타"},
    "fixed_spot_1_lt4_fire":  {"value": 60, "unit": "m2", "metric_type": "감지면적",
                              "condition": "정온식 스포트형 1종, 4m 미만, 내화"},
    "fixed_spot_1_lt4_other": {"value": 30, "unit": "m2", "metric_type": "감지면적",
                              "condition": "정온식 스포트형 1종, 4m 미만, 기타"},
    "fixed_spot_2_lt4_fire":  {"value": 20, "unit": "m2", "metric_type": "감지면적",
                              "condition": "정온식 스포트형 2종, 4m 미만, 내화. 4m 이상 설치불가"},
    "fixed_spot_2_lt4_other": {"value": 15, "unit": "m2", "metric_type": "감지면적",
                              "condition": "정온식 스포트형 2종, 4m 미만, 기타"},
    # --- 연기감지기 스포트형 (구조 무관) (NFTC 203 표 2.4.3.10.1) ---
    "smoke_12_lt4":   {"value": 150, "unit": "m2", "metric_type": "감지면적",
                       "condition": "연기 1·2종, 4m 미만(구조 무관)"},
    "smoke_12_4to20": {"value": 75,  "unit": "m2", "metric_type": "감지면적",
                       "condition": "연기 1·2종, 4m 이상 20m 미만(구조 무관)"},
    "smoke_3_lt4":    {"value": 50,  "unit": "m2", "metric_type": "감지면적",
                       "condition": "연기 3종, 4m 미만. 4m 이상 설치불가"},
    # --- 복도·계단 특례 (보행/수직 거리) ---
    "smoke_corridor_walk_12": {"value": 30, "unit": "m", "metric_type": "보행거리(walking path)",
                               "condition": "복도·통로 연기 1·2종, 보행거리 30m마다 1개. NFTC 203 2.4.3.10.2"},
    "smoke_corridor_walk_3":  {"value": 20, "unit": "m", "metric_type": "보행거리(walking path)",
                               "condition": "복도·통로 연기 3종, 보행거리 20m마다 1개"},
    "smoke_stair_vert_12":    {"value": 15, "unit": "m", "metric_type": "수직거리(vertical)",
                               "condition": "계단·경사로 연기 1·2종, 수직 15m마다 1개. NFTC 203 2.4.3.10.3"},
    "smoke_stair_vert_3":     {"value": 10, "unit": "m", "metric_type": "수직거리(vertical)",
                               "condition": "계단·경사로 연기 3종, 수직 10m마다 1개"},
    # --- 설치 기하 제약 ---
    "spot_tilt_max":        {"value": 45, "unit": "deg", "metric_type": "설치각도제한",
                             "condition": "스포트형 45도 이상 경사 금지. NFTC 203 2.4.3.4"},
    "spot_air_inlet_offset": {"value": 1.5, "unit": "m", "metric_type": "이격거리",
                              "condition": "스포트형 공기유입구 1.5m 이상 이격. NFTC 203 2.4.3.1"},
    "heat_to_ceiling_max":  {"value": 0.3, "unit": "m", "metric_type": "부착면이격",
                             "condition": "열감지기 하단 부착면 0.3m 이내. NFTC 203 2.4.3.6"},
    "smoke_to_ceiling_max": {"value": 0.6, "unit": "m", "metric_type": "부착면이격",
                             "condition": "연기감지기 하단 부착면 하부 0.6m 이내. NFTC 203 2.4.3.10.5"},
    "smoke_wall_beam_offset": {"value": 0.6, "unit": "m", "metric_type": "이격거리",
                               "condition": "연기감지기 벽·보 0.6m 이상 이격. NFTC 203 2.4.3.10.4"},
    # --- 차동식 분포형 ---
    "airtube_mutual_spacing_fire":  {"value": 9, "unit": "m", "metric_type": "상호간격(공기관)",
                                     "condition": "[CORRECTED] 분포형 공기관 상호 9m 이하, 내화. NFTC 203 2.4.3.7"},
    "airtube_mutual_spacing_other": {"value": 6, "unit": "m", "metric_type": "상호간격(공기관)",
                                     "condition": "분포형 공기관 상호 6m 이하, 기타"},
    "thermocouple_area_fire":  {"value": 22, "unit": "m2", "metric_type": "감지면적(열전대부)",
                               "condition": "분포형 열전대식 22m2/개, 내화. NFTC 203 2.4.3.8"},
    "thermocouple_area_other": {"value": 18, "unit": "m2", "metric_type": "감지면적(열전대부)",
                               "condition": "분포형 열전대식 18m2/개, 기타"},
    # --- 정온식 감지선형 (수평거리) ---
    "linear_fixed_1_fire":  {"value": 4.5, "unit": "m", "metric_type": "수평거리(radial)",
                             "condition": "정온식 감지선형 1종 내화: 수평 4.5m 이하. NFTC 203 2.4.3.12"},
    "linear_fixed_1_other": {"value": 3,   "unit": "m", "metric_type": "수평거리(radial)",
                             "condition": "정온식 감지선형 1종 기타: 수평 3m 이하"},
    "linear_fixed_2_fire":  {"value": 3,   "unit": "m", "metric_type": "수평거리(radial)",
                             "condition": "정온식 감지선형 2종 내화: 수평 3m 이하"},
    "linear_fixed_2_other": {"value": 1,   "unit": "m", "metric_type": "수평거리(radial)",
                             "condition": "정온식 감지선형 2종 기타: 수평 1m 이하"},
    # --- 경계구역 상위 제약 ---
    "detection_zone_area_max": {"value": 600, "unit": "m2", "metric_type": "경계구역면적(상위제약)",
                                "condition": "하나의 경계구역 600m2 이하(전체 가시 시 1000m2). NFTC 203 2.1.1"},
    "detection_zone_side_max": {"value": 50,  "unit": "m", "metric_type": "직선거리(변길이)",
                                "condition": "경계구역 한 변 50m 이하. NFTC 203 2.1.1"},
})


# ============================================================================
# 3) 소화기구 — NFTC 101 / NFPC 101
# ============================================================================
EXTINGUISHER = _freeze({
    "small_walk_max": {"value": 20, "unit": "m", "metric_type": "보행거리(walking path)",
                       "condition": "각 부분→소형소화기 보행거리 20m 이하. NFTC 101 2.1.1.4.2"},
    "large_walk_max": {"value": 30, "unit": "m", "metric_type": "보행거리(walking path)",
                       "condition": "각 부분→대형소화기 보행거리 30m 이하. NFTC 101 2.1.1.4.2"},
    "mount_height_max": {"value": 1.5, "unit": "m", "metric_type": "부착높이",
                         "condition": "소화기구(자동확산 제외) 바닥 1.5m 이하. NFTC 101 2.1.1.6"},
    "room_partition_threshold": {"value": 33, "unit": "m2", "metric_type": "구획추가배치 임계면적",
                                 "condition": "33m2 이상 구획 거실마다 별도 배치(구조 무관). NFTC 101 2.1.1.4.1"},
    # 능력단위 산정 바닥면적/단위 (일반구조 기준; 내화+불연마감이면 2배)
    "unit_area_amusement":       {"value": 30,  "unit": "m2", "metric_type": "능력단위(바닥면적당)",
                                  "condition": "위락시설, 일반구조: 30m2/단위. 표 2.1.1.2"},
    "unit_area_culture_medical": {"value": 50,  "unit": "m2", "metric_type": "능력단위(바닥면적당)",
                                  "condition": "문화집회(전시·동식물원 제외)·의료·장례·문화재, 일반: 50m2/단위"},
    "unit_area_common_100":      {"value": 100, "unit": "m2", "metric_type": "능력단위(바닥면적당)",
                                  "condition": "공동주택·근생·판매·운수·노유자·업무·숙박·공장·창고 등, 일반: 100m2/단위"},
    "unit_area_other":           {"value": 200, "unit": "m2", "metric_type": "능력단위(바닥면적당)",
                                  "condition": "그 밖의 용도, 일반: 200m2/단위. 표 2.1.1.2"},
    "unit_area_fireproof_multiplier": {"value": 2.0, "unit": "-", "metric_type": "능력단위 보정계수",
                                       "condition": "내화 AND 벽·반자 실내면 불연/준불연/난연 마감 → 기준면적 2배. 표 2.1.1.2 비고"},
    "simple_tool_ratio_max": {"value": 0.5, "unit": "-", "metric_type": "능력단위 비율상한",
                              "condition": "간이소화용구 능력단위 합 ≤ 전체 1/2(노유자 제외). NFTC 101 2.1.1.5"},
    "aux_boiler_kitchen_unit": {"value": 25, "unit": "m2", "metric_type": "방호면적(부속추가 능력단위)",
                                "condition": "보일러실·건조실·주방 등 25m2마다 능력단위 1 추가. 표 2.1.1.3"},
    "aux_electrical_count":    {"value": 50, "unit": "m2", "metric_type": "방호면적(부속추가 개수)",
                               "condition": "발전·변전·배전반실 50m2마다 적응 소화기 1개. 표 2.1.1.3"},
    "co2_halon_min_area": {"value": 20, "unit": "m2", "metric_type": "설치제한면적",
                           "condition": "CO2/할로겐: 지하·무창·밀폐 20m2 미만 금지. NFTC 101 2.1.3"},
    "gas_detector_height_max": {"value": 0.3, "unit": "m", "metric_type": "감지부 설치높이",
                                "condition": "주방 가스탐지부: 가벼운가스 천장 30cm/무거운가스 바닥 30cm. NFTC 101 2.1.2.1.4"},
    "small_reduce_ratio": {"value": 0.6667, "unit": "-", "metric_type": "능력단위 감소율",
                           "condition": "옥내/SP/물분무등/옥외 설치 시 소형 2/3 감소(11층↑·근생·위락·아파트 등 제외). NFTC 101 2.2.1"},
    "large_exempt": {"value": 1, "unit": "-", "metric_type": "설치면제(불리언)",
                     "condition": "[CORRECTED 2.2.2→2.2.1] 옥내/SP/물분무등/옥외 유효범위 내 대형소화기 면제. NFTC 101 2.2.1"},
})


# ============================================================================
# 4) 옥내소화전설비 — NFTC 102 / NFPC 102 (+ 고층 NFTC 604)
# ============================================================================
HYDRANT = _freeze({
    "horizontal_radius": {"value": 25, "unit": "m", "metric_type": "수평거리(radial)",
                          "condition": "각 부분→방수구 수평거리 25m 이하(호스릴 15m). NFTC 102 2.4.2.1"},
    "nozzle_pressure_min": {"value": 0.17, "unit": "MPa", "metric_type": "방수성능(압력)",
                            "condition": "동시사용 시 노즐선단 최소 방수압. NFTC 102 2.2.1.3"},
    "nozzle_pressure_max": {"value": 0.7, "unit": "MPa", "metric_type": "방수성능(압력)",
                            "condition": "노즐선단 0.7MPa 초과 시 감압. NFTC 102 2.2.1.3"},
    "nozzle_flow": {"value": 130, "unit": "L/min", "metric_type": "방수성능(유량)",
                    "condition": "0.17MPa 조건 노즐당 방수량. NFTC 102 2.2.1.3"},
    # 동시개구수 N 캡: 일반 2 / 고층(30층↑) 5
    "sim_open_cap_normal":   {"value": 2, "unit": "개", "metric_type": "산정계수(동시개구수)",
                              "condition": "일반건축물 동시개구수 N 상한 2. NFTC 102 2.2.1.3"},
    "sim_open_cap_highrise": {"value": 5, "unit": "개", "metric_type": "산정계수(동시개구수)",
                              "condition": "[CORRECTED 2→5] 30층 이상 동시개구수 N 상한 5. NFTC 604 2.1.1"},
    # 수원 V = N × factor [m3]
    "water_factor_lt30":   {"value": 2.6, "unit": "m3/개", "metric_type": "수원산정",
                            "condition": "30층 미만 V=N×2.6 (130L/min×20min). NFTC 102 2.1.1"},
    "water_factor_30to49": {"value": 5.2, "unit": "m3/개", "metric_type": "수원산정",
                            "condition": "30~49층 V=N×5.2 (×40min). NFTC 604 2.1.1"},
    "water_factor_ge50":   {"value": 7.8, "unit": "m3/개", "metric_type": "수원산정",
                            "condition": "50층 이상 V=N×7.8 (×60min). NFTC 604 2.1.1"},
    "rooftop_reserve_ratio": {"value": 0.3333, "unit": "-", "metric_type": "수원산정(옥상수조)",
                              "condition": "산정수원 1/3 이상 옥상 별도(자연낙차 등 예외). NFTC 102 2.1.2"},
    "pump_flow_per_head": {"value": 130, "unit": "L/min", "metric_type": "펌프 토출량",
                           "condition": "펌프 토출량 Q=N×130. 일반 N≤2→260, 고층 N≤5→650. NFTC 102 2.2.1.4"},
    "pump_churn_max":    {"value": 140, "unit": "%", "metric_type": "펌프 성능곡선",
                         "condition": "체절운전 시 정격토출압 140% 초과금지. NFTC 102 2.2.1.7"},
    "pump_overload_min": {"value": 65, "unit": "%", "metric_type": "펌프 성능곡선",
                          "condition": "150% 유량 시 정격토출압 65% 이상. NFTC 102 2.2.1.7"},
    "head_pressure_equiv": {"value": 17, "unit": "m", "metric_type": "펌프 양정산정",
                            "condition": "양정 H=실양정+마찰손실+17m(0.17MPa 환산). NFTC 102"},
    "hose_diameter":        {"value": 40, "unit": "mm", "metric_type": "배관·호스규격",
                             "condition": "호스 구경 40mm(호스릴 25mm). NFTC 102 2.4.2.3"},
    "branch_pipe_diameter": {"value": 40, "unit": "mm", "metric_type": "배관규격",
                             "condition": "가지배관 최소 40mm(호스릴 25mm). NFTC 102 2.3.5"},
    "riser_pipe_diameter":  {"value": 50, "unit": "mm", "metric_type": "배관규격",
                             "condition": "주배관 수직배관 최소 50mm(호스릴 32mm). NFTC 102 2.3.5"},
    "valve_height_max": {"value": 1.5, "unit": "m", "metric_type": "함·방수구 설치높이",
                         "condition": "방수구 바닥 1.5m 이하. NFTC 102 2.4.2.2"},
    "connector_diameter": {"value": 65, "unit": "mm", "metric_type": "송수구 규격",
                           "condition": "송수구 구경 65mm 쌍구/단구형. NFTC 102 2.3.12.4"},
    "emergency_power_trigger_floor":    {"value": 7, "unit": "층", "metric_type": "설치대상 임계",
                                        "condition": "지상 7층↑ AND 연면적 2000m2↑ 시 비상전원. NFTC 102 2.5.2.1"},
    "emergency_power_trigger_area":     {"value": 2000, "unit": "m2", "metric_type": "설치대상 임계",
                                        "condition": "비상전원 연면적 임계(7층과 AND). NFTC 102 2.5.2.1"},
    "emergency_power_trigger_basement": {"value": 3000, "unit": "m2", "metric_type": "설치대상 임계",
                                        "condition": "지하층 합계 3000m2↑ 시 비상전원. NFTC 102 2.5.2.2"},
    "emergency_power_duration_lt30":   {"value": 20, "unit": "min", "metric_type": "비상전원 용량",
                                       "condition": "30층 미만 20분 이상. NFTC 102 2.5.3.2"},
    "emergency_power_duration_30to49": {"value": 40, "unit": "min", "metric_type": "비상전원 용량",
                                       "condition": "30~49층 40분 이상. NFTC 604 2.1.7"},
    "emergency_power_duration_ge50":   {"value": 60, "unit": "min", "metric_type": "비상전원 용량",
                                       "condition": "50층 이상 60분 이상. NFTC 604 2.1.7"},
})


# ============================================================================
# 5) 피난 — 건축법 시행령 제34조 / 피난·방화규칙 / 피난기구 NFTC 301
# ============================================================================
EVACUATION = _freeze({
    # 거실 각 부분 → 직통계단 보행거리 (건축법 시행령 제34조①)
    "stair_walk_default":       {"value": 30, "unit": "m", "metric_type": "보행거리(walking path)",
                                 "condition": "일반 건축물. 시행령 제34조①"},
    "stair_walk_fireproof":     {"value": 50, "unit": "m", "metric_type": "보행거리(walking path)",
                                 "condition": "주요구조부 내화/불연. 시행령 제34조①"},
    "stair_walk_apartment_16f": {"value": 40, "unit": "m", "metric_type": "보행거리(walking path)",
                                 "condition": "내화/불연 + 16층 이상 공동주택의 16층↑ 층"},
    "stair_walk_auto_factory":  {"value": 75, "unit": "m", "metric_type": "보행거리(walking path)",
                                 "condition": "내화/불연 + 자동식소화설비 자동화생산 공장"},
    "stair_walk_unmanned_factory": {"value": 100, "unit": "m", "metric_type": "보행거리(walking path)",
                                    "condition": "위 자동화공장 + 무인화 공장"},
    # 직통계단 2개소 요구 바닥면적 임계 (양방향 피난 트리거) — 제34조②
    "two_stairs_threshold_assembly":    {"value": 200, "unit": "m2", "metric_type": "바닥면적 임계(양방향)",
                                         "condition": "문화집회·종교·위락주점·장례 ≥200m2 → 2개소. 제34조②1호"},
    "two_stairs_threshold_performance": {"value": 300, "unit": "m2", "metric_type": "바닥면적 임계(양방향)",
                                         "condition": "제2종 근생 공연장·종교집회장 ≥300m2 → 2개소. 제34조②1호"},
    "two_stairs_threshold_medical":     {"value": 200, "unit": "m2", "metric_type": "바닥면적 임계(양방향)",
                                         "condition": "[CORRECTED 제2호] 다중·다가구·의료·학원·판매·노유자·숙박 ≥200m2(3층↑). 제34조②2호"},
    "two_stairs_threshold_apartment":   {"value": 300, "unit": "m2", "metric_type": "바닥면적 임계(양방향)",
                                         "condition": "[ADDED 제3호] 공동주택(4세대 초과)·오피스텔 ≥300m2 → 2개소. 제34조②3호"},
    "two_stairs_threshold_basement":    {"value": 200, "unit": "m2", "metric_type": "바닥면적 임계(양방향)",
                                         "condition": "지하층 거실 ≥200m2 → 2개소. 제34조②5호"},
    "two_stairs_threshold_other":       {"value": 400, "unit": "m2", "metric_type": "바닥면적 임계(양방향)",
                                         "condition": "그 밖 3층↑ 거실 ≥400m2. 제34조②4호"},
    # 복도 유효너비 (피난·방화규칙 제15조의2)
    "corridor_school_double":      {"value": 2.4, "unit": "m", "metric_type": "복도 유효너비",
                                    "condition": "유치원·초중고 중복도(양옆거실)"},
    "corridor_school_single":      {"value": 1.8, "unit": "m", "metric_type": "복도 유효너비",
                                    "condition": "유치원·초중고 편복도"},
    "corridor_residential_double": {"value": 1.8, "unit": "m", "metric_type": "복도 유효너비",
                                    "condition": "공동주택·오피스텔 중복도"},
    "corridor_residential_single": {"value": 1.2, "unit": "m", "metric_type": "복도 유효너비",
                                    "condition": "공동주택·오피스텔 편복도"},
    "corridor_general_double":     {"value": 1.5, "unit": "m", "metric_type": "복도 유효너비",
                                    "condition": "기타 거실합계 ≥200m2(지하 ≥100) 중복도(의료는 1.8)"},
    "corridor_medical_double":     {"value": 1.8, "unit": "m", "metric_type": "복도 유효너비",
                                    "condition": "의료시설 거실합계 ≥200m2(지하 ≥100) 중복도"},
    "corridor_general_single":     {"value": 1.2, "unit": "m", "metric_type": "복도 유효너비",
                                    "condition": "기타 거실합계 ≥200m2(지하 ≥100) 편복도"},
    "corridor_assembly_lt500":     {"value": 1.5, "unit": "m", "metric_type": "복도 유효너비",
                                    "condition": "관람·집회실 복도 <500m2. 제15조의2②"},
    "corridor_assembly_500to1000": {"value": 1.8, "unit": "m", "metric_type": "복도 유효너비",
                                    "condition": "관람·집회실 복도 500~1000m2"},
    "corridor_assembly_ge1000":    {"value": 2.4, "unit": "m", "metric_type": "복도 유효너비",
                                    "condition": "관람·집회실 복도 ≥1000m2"},
    # 피난기구 개수 산정 분모 (NFTC 301 2.1.2.1): 개수 = ceil(층 바닥면적 / 분모)
    "escape_denom_lodging":   {"value": 500,  "unit": "m2", "metric_type": "설치개수 산정분모",
                               "condition": "숙박·노유자·의료 층 500m2마다 1개. NFTC 301 2.1.2.1 가"},
    "escape_denom_amusement": {"value": 800,  "unit": "m2", "metric_type": "설치개수 산정분모",
                               "condition": "위락·문화집회·운동·판매·복합 층 800m2마다 1개. 2.1.2.1 나"},
    "escape_denom_other":     {"value": 1000, "unit": "m2", "metric_type": "설치개수 산정분모",
                               "condition": "그 밖의 용도 층 1000m2마다 1개. 2.1.2.1 라"},
    "escape_reduce_ratio": {"value": 0.5, "unit": "-", "metric_type": "설치개수 감소율",
                            "condition": "내화 + 피난/특별피난계단 2개소↑ → 피난기구 1/2(올림). NFTC 301 2.3.1"},
    "descent_opening_width_min":  {"value": 0.5, "unit": "m", "metric_type": "개구부 치수",
                                   "condition": "완강기 개구부 가로 0.5m 이상. NFTC 301 2.1.3.1"},
    "descent_opening_height_min": {"value": 1.0, "unit": "m", "metric_type": "개구부 치수",
                                   "condition": "완강기 개구부 세로 1m 이상. NFTC 301 2.1.3.1"},
    "descent_floor_range_low":  {"value": 3,  "unit": "층", "metric_type": "적응 층 범위",
                                 "condition": "완강기 적응 최저 3층(1·2층 부적응). NFTC 301 표 2.1.1"},
    "descent_floor_range_high": {"value": 10, "unit": "층", "metric_type": "적응 층 범위",
                                 "condition": "완강기 적응 최고 10층(11층↑ 부적응). NFTC 301 표 2.1.1"},
})


# ============================================================================
# 6) 비용 단가 (⚠️ DEMO ESTIMATE — 검증된 시장 단가 아님. 상대 비교용으로만 사용)
# ============================================================================
# 6-1) 충돌 1건당 재시공비 산정 근거 (항목 분해 — 왜 이 값인지 투명하게)
#   ⚠️ 모든 단가는 조정 가능한 가정값. FireOpt 가 '확정 계산'하는 것은 충돌 '건수'이며,
#      건당 금액은 아래 항목 합으로 추정한다. 절감액 = 해소 건수 × 건당 재시공비.
REWORK_BASIS = _freeze({
    "pipe_redo":      {"value": 300000, "unit": "KRW/건",
                       "condition": "가지배관 부분 철거·재설치 + 헤드/부속 재시공 자재 (가정)"},
    "fire_labor":     {"value": 250000, "unit": "KRW/건",
                       "condition": "소방설비공 2인 × 0.5일 × 25만원/일 노임 (가정, 표준품셈 노임 수준)"},
    "mep_relocate":   {"value": 200000, "unit": "KRW/건",
                       "condition": "간섭 전기/기계 배관·기구 이설·재결선 (가정)"},
    "finish_restore": {"value": 400000, "unit": "KRW/건",
                       "condition": "천장·마감 부분 철거 후 복구(타 공종 영향) (가정)"},
    "delay_indirect": {"value": 350000, "unit": "KRW/건",
                       "condition": "검측 재실시·공정 지연 등 간접비 (가정)"},
})

# 충돌 1건당 재시공비 = 위 항목 합
REWORK_PER_CLASH = sum(v["value"] for v in REWORK_BASIS.values())   # = 1,500,000

# 산정 규모 근거(앵커): 기획안 인용 — 소방 설계오류 재시공 판례 5천만원+ 사례는
# '다수 충돌·대형 구간이 누적된 심각 사례'. 본 모델의 건당 ₩150만은 '소규모 단일
# 충돌 1지점'의 보수적 직접비 추정으로, 충돌 다수 누적 시 판례 규모로 수렴.
REWORK_ANCHOR = ("소방 설계오류 재시공 판례 5천만원+ 사례(다수·대형 충돌 누적)를 규모 앵커로, "
                 "본 모델은 보수적으로 충돌 1건(소규모 단일 간섭)당 직접 재시공비를 항목 합으로 추정")


def rework_per_clash() -> int:
    """충돌 1건당 재시공비(원) = REWORK_BASIS 항목 합."""
    return REWORK_PER_CLASH


COST = _freeze({
    "sprinkler_head": {"value": 35000,  "unit": "KRW/EA", "condition": "DEMO 추정 단가(헤드+부속)"},
    "detector":       {"value": 45000,  "unit": "KRW/EA", "condition": "DEMO 추정 단가(감지기)"},
    "extinguisher":   {"value": 40000,  "unit": "KRW/EA", "condition": "DEMO 추정 단가(소화기)"},
    "hydrant_box":    {"value": 450000, "unit": "KRW/EA", "condition": "DEMO 추정 단가(소화전함)"},
    "evac_device":    {"value": 350000, "unit": "KRW/EA", "condition": "DEMO 추정 단가(피난기구)"},
    "branch_pipe":    {"value": 28000,  "unit": "KRW/m",  "condition": "DEMO 추정 단가(가지배관 1m)"},
    "rework_penalty": {"value": REWORK_PER_CLASH, "unit": "KRW/건",
                       "condition": "충돌 1건당 재시공비 = REWORK_BASIS 항목 합(근거 분해)"},
})


# ============================================================================
# 순수 선택자(selector) — 배치/최적화 모듈이 호출
# ============================================================================
def sprinkler_radius(structure: str = "fireproof", occupancy: str | None = None) -> float:
    """스프링클러 헤드 수평거리 R [m].

    occupancy: "stage_special"(무대부/특수가연물) 또는 "apartment"(공동주택 세대)면 구조보다 우선.
    structure: "fireproof"(내화) 그 외는 비내화로 처리.
    """
    if occupancy == "stage_special":
        return SPRINKLER["R_stage_special"]["value"]
    if occupancy == "apartment":
        return SPRINKLER["R_apartment"]["value"]
    if structure in ("fireproof", "noncombustible"):
        return SPRINKLER["R_fireproof"]["value"]
    return SPRINKLER["R_non_fireproof"]["value"]


def square_pitch(R: float) -> float:
    """정방형 배치 헤드 간격 S = R × √2 (= 2R·cos45°)."""
    return R * SPRINKLER["square_pitch_factor"]["value"]


def sprinkler_protection_area(structure: str = "fireproof", occupancy: str | None = None) -> float:
    """정방형 배치 시 헤드 1개당 방호면적 [m²] = S² = (√2·R)² = 2R²."""
    R = sprinkler_radius(structure, occupancy)
    return square_pitch(R) ** 2


def detector_area(dtype: str = "smoke_12", mount_height: float = 3.0,
                  structure: str = "fireproof") -> float:
    """감지기 1개당 감지면적 [m²].

    dtype: "smoke_12" | "smoke_3" | "diff_spot_1" | "diff_spot_2"
           | "fixed_spot_special" | "fixed_spot_1" | "fixed_spot_2"
    """
    if dtype.startswith("smoke"):
        if dtype == "smoke_3":
            if mount_height >= 4:
                raise ValueError("연기 3종은 부착높이 4m 이상 설치불가")
            return DETECTOR["smoke_3_lt4"]["value"]
        # smoke_12 (1·2종)
        if mount_height >= 20:
            raise ValueError("연기 1·2종은 부착높이 20m 이상 설치불가")
        key = "smoke_12_4to20" if mount_height >= 4 else "smoke_12_lt4"
        return DETECTOR[key]["value"]
    # 열감지기 스포트형 (스펙은 4m 미만 구간 정의)
    if mount_height >= 4:
        raise ValueError(f"{dtype}: 부착높이 4m 이상 감지면적은 본 스펙 범위 외")
    suffix = "fire" if structure in ("fireproof", "noncombustible") else "other"
    key = f"{dtype}_lt4_{suffix}"
    if key not in DETECTOR:
        raise KeyError(f"알 수 없는 감지기 종별: {dtype} (key={key})")
    return DETECTOR[key]["value"]


_EXT_UNIT_KEY = {
    "amusement": "unit_area_amusement",
    "culture_medical": "unit_area_culture_medical",
    "common": "unit_area_common_100",
    "other": "unit_area_other",
}


def extinguisher_unit_area(occupancy: str = "common",
                           fireproof_and_noncombustible: bool = False) -> float:
    """소화기 능력단위 1단위당 기준 바닥면적 [m²].

    내화구조 AND 벽·반자 실내면이 불연/준불연/난연 마감이면 기준면적 2배.
    """
    key = _EXT_UNIT_KEY.get(occupancy, "unit_area_common_100")
    area = EXTINGUISHER[key]["value"]
    if fireproof_and_noncombustible:
        area *= EXTINGUISHER["unit_area_fireproof_multiplier"]["value"]
    return area


def hydrant_sim_open_cap(num_floors: int) -> int:
    """옥내소화전 동시개구수 N 상한 (일반 2 / 30층 이상 5)."""
    return (HYDRANT["sim_open_cap_highrise"]["value"] if num_floors >= 30
            else HYDRANT["sim_open_cap_normal"]["value"])


def hydrant_water_factor(num_floors: int) -> float:
    """옥내소화전 수원 산정계수 [m³/개]."""
    if num_floors >= 50:
        return HYDRANT["water_factor_ge50"]["value"]
    if num_floors >= 30:
        return HYDRANT["water_factor_30to49"]["value"]
    return HYDRANT["water_factor_lt30"]["value"]


def hydrant_water_source(num_floors: int, installed_heads: int) -> float:
    """옥내소화전 수원 용량 V = min(N_설치, N_상한) × factor [m³]."""
    n = min(installed_heads, hydrant_sim_open_cap(num_floors))
    return n * hydrant_water_factor(num_floors)


def hydrant_pump_flow(num_floors: int, installed_heads: int) -> float:
    """옥내소화전 펌프 토출량 Q = min(N_설치, N_상한) × 130 [L/min]."""
    n = min(installed_heads, hydrant_sim_open_cap(num_floors))
    return n * HYDRANT["pump_flow_per_head"]["value"]


_ESCAPE_DENOM_KEY = {
    "lodging": "escape_denom_lodging",
    "amusement": "escape_denom_amusement",
    "other": "escape_denom_other",
}


def escape_device_denominator(occupancy: str = "other") -> float:
    """피난기구 설치개수 산정 분모 [m²/개]."""
    return EVACUATION[_ESCAPE_DENOM_KEY.get(occupancy, "escape_denom_other")]["value"]


def stair_walk_limit(structure: str = "fireproof", floor_no: int = 1,
                     is_apartment: bool = False, auto_factory: bool = False,
                     unmanned: bool = False) -> float:
    """거실 각 부분 → 직통계단 허용 보행거리 [m]."""
    fireproof = structure in ("fireproof", "noncombustible")
    if not fireproof:
        return EVACUATION["stair_walk_default"]["value"]          # 30
    if unmanned:
        return EVACUATION["stair_walk_unmanned_factory"]["value"]  # 100
    if auto_factory:
        return EVACUATION["stair_walk_auto_factory"]["value"]      # 75
    if is_apartment and floor_no >= 16:
        return EVACUATION["stair_walk_apartment_16f"]["value"]     # 40
    return EVACUATION["stair_walk_fireproof"]["value"]             # 50


# ============================================================================
# 스프링클러 배관 구경 — NFTC 103 별표(표 2.5.3.3) 폐쇄형 표준형헤드 기준
#   해당 배관이 담당(하류)하는 헤드 개수 → 최소 관경(mm). 1차 구경 산정용.
# ============================================================================
# (최대 헤드수, 관경mm) 오름차순
SPRINKLER_PIPE_TABLE = (
    (2, 25), (3, 32), (5, 40), (10, 50), (30, 65),
    (60, 80), (80, 90), (100, 100), (160, 125),
)
SPRINKLER_PIPE_MAX = 150   # 161개 이상


def pipe_diameter_for_heads(n_heads: int) -> int:
    """담당 헤드 수 → 스프링클러 배관 최소 관경 [mm] (NFTC 103 별표)."""
    for max_n, dia in SPRINKLER_PIPE_TABLE:
        if n_heads <= max_n:
            return dia
    return SPRINKLER_PIPE_MAX


# 외부에서 facility 이름으로 표를 찾을 때 사용
ALL_FACILITIES = _freeze({
    "sprinkler": SPRINKLER,
    "detector": DETECTOR,
    "extinguisher": EXTINGUISHER,
    "hydrant": HYDRANT,
    "evacuation": EVACUATION,
})
