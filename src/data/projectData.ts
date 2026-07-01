import type { Conflict, Layer, Recommendation, WorkflowItem } from "../types";

export const initialLayers: Layer[] = [
  { id: "architecture", label: "건축 (Architecture)", color: "#8794a8", visible: true },
  { id: "electrical", label: "전기 (Electrical)", color: "#24b9d2", visible: true },
  { id: "mechanical", label: "기계 (Mechanical)", color: "#d5802c", visible: true },
  { id: "fire", label: "소방 시설 (Fire Protection)", color: "#ef4d4d", visible: true },
  { id: "collision", label: "충돌 영역", color: "#914dff", visible: true },
];

export const conflicts: Conflict[] = [
  {
    id: 1,
    severity: "심각",
    title: "스프링클러 ↔ 덕트",
    location: "사무실 101",
    height: "2,450mm",
    tone: "danger",
  },
  {
    id: 2,
    severity: "경고",
    title: "소화배관 ↔ 전기 케이블 트레이",
    location: "복도",
    height: "2,300mm",
    tone: "warning",
  },
  {
    id: 3,
    severity: "경고",
    title: "감지기 ↔ 조명 기구",
    location: "회의실 203",
    height: "2,800mm",
    tone: "warning",
  },
  {
    id: 4,
    severity: "경고",
    title: "스프링클러 ↔ 덕트",
    location: "사무실 104",
    height: "2,400mm",
    tone: "warning",
  },
];

export const recommendations: Recommendation[] = [
  {
    id: 1,
    title: "대안 1",
    summary: "3개 설비 재배치",
    saving: "₩8,420,000",
    recommended: true,
  },
  {
    id: 2,
    title: "대안 2",
    summary: "덕트 우회 + 감지기 간격 재계산",
    saving: "₩5,110,000",
  },
];

export const implementedItems: WorkflowItem[] = [
  {
    title: "도면 파일 선택",
    status: "implemented",
    description: "업로드된 IFC 샘플 목록에서 활성 도면을 선택하는 UI 상태를 제공합니다.",
  },
  {
    title: "레이어 표시/숨김",
    status: "implemented",
    description: "건축, 전기, 기계, 소방, 충돌 레이어를 토글하면 중앙 도면 요소가 즉시 반영됩니다.",
  },
  {
    title: "충돌 목록 필터",
    status: "implemented",
    description: "전체, 심각, 경고 기준으로 우측 충돌 목록을 필터링합니다.",
  },
  {
    title: "최적화 대안 적용",
    status: "implemented",
    description: "권장 대안을 적용하면 해결 가능 수치와 상태 메시지가 갱신됩니다.",
  },
];

export const uiOnlyItems: WorkflowItem[] = [
  {
    title: "IFC/BIM 실제 파일 파싱",
    status: "ui-only",
    description: "공간 객체 추출, 방/벽/문/복도/면적 계산은 아직 실제 엔진과 연결하지 않았습니다.",
  },
  {
    title: "NFSC 법규 자동 검토",
    status: "ui-only",
    description: "NFSC 규칙 트리와 LLM 법규 해석은 화면 표기만 제공하며 실제 판정은 수행하지 않습니다.",
  },
  {
    title: "충돌 감지 알고리즘",
    status: "ui-only",
    description: "충돌 목록은 샘플 데이터이며 전기/기계/소방 객체 간 실제 기하 충돌 계산은 없습니다.",
  },
  {
    title: "AI 재최적화 및 비용 예측",
    status: "ui-only",
    description: "추천 대안과 비용 절감액은 프로토타입용 정적 데이터입니다.",
  },
  {
    title: "DXF/보고서 내보내기",
    status: "ui-only",
    description: "상단 버튼과 상태 토스트만 제공하며 파일 생성은 아직 구현하지 않았습니다.",
  },
];
