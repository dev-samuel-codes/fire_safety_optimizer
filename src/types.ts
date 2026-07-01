export type LayerId =
  | "architecture"
  | "electrical"
  | "mechanical"
  | "fire"
  | "collision";

export type Severity = "심각" | "경고";

export interface DrawingFile {
  name: string;
  time: string;
  active?: boolean;
}

export interface Layer {
  id: LayerId;
  label: string;
  color: string;
  visible: boolean;
}

export interface Conflict {
  id: number;
  severity: Severity;
  title: string;
  location: string;
  height: string;
  tone: "danger" | "warning";
}

export interface Recommendation {
  id: number;
  title: string;
  summary: string;
  saving: string;
  recommended?: boolean;
}

export interface WorkflowItem {
  title: string;
  status: "implemented" | "ui-only";
  description: string;
}
