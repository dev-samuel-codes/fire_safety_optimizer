import { useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { CadFileViewer, type CadFileViewerHandle } from "./components/CadFileViewer";
import type { LayerId } from "./types";

// CAD 뷰어는 도면 자체의 레이어 가시성으로 렌더 — 앱 레이어 필터는 미연결(PART B 예정)
const NO_VISIBLE_LAYERS = new Set<LayerId>();

type DialogType = "export" | null;
type PanOffset = { x: number; y: number };
type DragState = PanOffset & { pointerId: number; startX: number; startY: number };
type PanelWidths = { left: number; right: number };
type ColumnResizeSide = "left" | "right";
type ColumnResizeState = {
  pointerId: number;
  side: ColumnResizeSide;
  startX: number;
  leftWidth: number;
  rightWidth: number;
};
type DrawingInfo = {
  fileName?: string;
  layerCount?: number;
  entityCount?: number;
  fireLayers?: string[];
  roomNames?: string[];
  layerNames?: string[];
  error?: string;
  errorCode?: string;
  analysisStatus?: "ok" | "recovered" | "failed";
  analysisSource?: string;
  analysisWarnings?: string[];
  source?: "backend" | "viewer";
};

const zoomMin = 25;
const zoomWheelStep = 10;
const uploadedDrawingInitialZoom = 150;
const designViewport = { width: 1280, height: 720 };
const defaultPanelWidths: PanelWidths = { left: 230, right: 240 };
const panelResizeLimits = {
  leftMin: 170,
  rightMin: 180,
  sideMax: 420,
  centerMin: 420,
};

// 인식된 소방 심볼 클래스 → 사용자가 지정할 설비 종류(HITL 명명). 값은 엔진 facility 키.
const FACILITY_LABELS: Record<string, string> = {
  detector_smoke: "연기감지기",
  detector_heat: "열감지기(차동/정온)",
  sprinkler: "스프링클러",
  hydrant: "옥내소화전",
  extinguisher: "소화기",
  evacuation: "피난구",
  ignore: "설비 아님(무시)",
};

type SymbolClass = {
  classId: string; layer: string; count: number;
  guess: string | null; needsHitl: boolean; isDetector: boolean;
  source: string; reason: string; thumbnail: string;
};
type Recognition = { classes: SymbolClass[]; legendTypes: string[]; facilityOptions: string[];
  detectorContext?: boolean; scopeHint?: string } | null;

export function App() {
  const viewportFrame = useViewportFrame();
  const [toast, setToast] = useState("대기 중 · 도면을 업로드해주세요");
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [zoomLevel, setZoomLevel] = useState(100);
  const [panOffset, setPanOffset] = useState<PanOffset>({ x: 0, y: 0 });
  const [dragState, setDragState] = useState<DragState | null>(null);
  const [panelWidths, setPanelWidths] = useState<PanelWidths>(defaultPanelWidths);
  const [columnResize, setColumnResize] = useState<ColumnResizeState | null>(null);
  const [dialog, setDialog] = useState<DialogType>(null);
  const workspaceRef = useRef<HTMLDivElement | null>(null);
  const drawingCardRef = useRef<HTMLDivElement | null>(null);
  const cadViewerRef = useRef<CadFileViewerHandle | null>(null);   // AI 방 클릭→뷰어 이동
  const zoomLevelRef = useRef(zoomLevel);
  const analysisRequestIdRef = useRef(0);

  // ── 백엔드(FireVal/FireOpt 엔진) 실시간 연결: POST /api/analyze ──
  const [structure, setStructure] = useState<string>("");   // "" 미상 | "fireproof" | "other"
  const [occupancy, setOccupancy] = useState<string>("");   // "" 미상 | 용도 문자열
  const [mount, setMount] = useState<string>("");           // "" 미상 | "lt4"(<4m) | "ge4"(≥4m)
  const [analysis, setAnalysis] = useState<{
    drawingInfo?: DrawingInfo | null;
    roomJudgments?: Array<{
      room?: string; status?: string; area_m2?: number;
      detail?: string; reason?: string; basis?: string;
    }>;
    violations?: Array<{
      ruleId?: string; status?: string; severity?: string; description?: string;
    }>;
    judgmentSource?: string;
  }>({ drawingInfo: null, roomJudgments: [], violations: [] });
  const [analysisPendingMessage, setAnalysisPendingMessage] = useState<string | null>(null);
  const [viewerDrawingInfo, setViewerDrawingInfo] = useState<DrawingInfo | null>(null);
  const effectiveDrawingInfo = getEffectiveDrawingInfo(analysis.drawingInfo ?? null, viewerDrawingInfo);
  const visibleDrawingInfo = analysisPendingMessage ? null : effectiveDrawingInfo;
  const analysisError = analysis.drawingInfo?.error;
  const analysisRecovered = analysis.drawingInfo?.analysisStatus === "recovered";
  const analysisWarnings = analysis.drawingInfo?.analysisWarnings ?? [];
  const statusDrawingInfo = analysisError || analysisPendingMessage ? null : effectiveDrawingInfo;
  const displayedStatus = analysisPendingMessage ?? (analysisError ? `추출 실패: ${analysisError}` : analysisRecovered ? `복구 분석 완료: ${uploadedFile?.name ?? analysis.drawingInfo?.fileName ?? "도면"}` : toast);
  const [showReport, setShowReport] = useState(false);   // 뷰 토글: 기본=분석·판정, 보고서 탭 클릭 시만 보고서
  const shouldShowReport = Boolean(uploadedFile && !analysisPendingMessage && (analysis.drawingInfo || effectiveDrawingInfo));

  // 소방 심볼 인식(HITL 명명): 업로드 시 /api/recognize 매니페스트, labels=사용자 지정 종류
  const [recognition, setRecognition] = useState<Recognition>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  // AI 방찾기(기하): 방 레이어 없는 실무 도면용. rooms=[{name,area_m2,center,status}], loading 상태
  const [aiResult, setAiResult] = useState<{
    rooms?: Array<{ name?: string; area_m2?: number; center?: number[]; status?: string; bridged?: boolean }>;
    violations?: Array<{ ruleId?: string; status?: string; description?: string; roomName?: string; center?: number[] }>;
    note?: string; available?: boolean; loading?: boolean;
  } | null>(null);
  // HITL 방 확인: SAM 신뢰도·raycast 교차검사 둘 다 자동신뢰 불가로 측정 확증(2026-07-06) →
  // 자동 게이트 없음. 사용자가 방별로 확인/제외, 확인된 방만 판정에 반영(미확인=검토 필요).
  const [roomDecisions, setRoomDecisions] = useState<Record<string, "confirmed" | "excluded">>({});
  // needs_boundary(벽 안 닫힌) 방: 사람이 면적을 직접 입력해 해소. 안전정책상 어떤 방도 자동 최종 아님.
  const [manualAreas, setManualAreas] = useState<Record<string, string>>({});
  // 요청 시퀀스 가드: 연속 업로드/파라미터 변경 시 이전 in-flight 응답이 최신 상태를 덮어쓰지 않게.
  const analyzeSeqRef = useRef(0);
  const recognizeSeqRef = useRef(0);
  const aiSeqRef = useRef(0);   // AI 방찾기 응답 레이스 가드(느린 요청이 새 파일 상태 덮어쓰기 방지)

  // 업로드 파일 + 구조/용도를 FormData로 전송 → 백엔드가 사실 + 방별요구 + (깨끗규격이면) 실 pass/fail 반환
  const runAnalysis = useCallback((file?: File, structureVal?: string, occupancyVal?: string, mountVal?: string, labelsArg?: Record<string, string>) => {
    const requestId = analysisRequestIdRef.current + 1;
    analysisRequestIdRef.current = requestId;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    const options: RequestInit = { method: "POST", signal: controller.signal };
    setAnalysisPendingMessage(file ? `${file.name} 분석 중… (도면 정보 추출)` : null);
    if (file) {
      const form = new FormData();
      form.append("file", file);
      if (structureVal) {
        form.append("structure", structureVal);
      }
      if (occupancyVal) {
        form.append("occupancy", occupancyVal);
      }
      if (mountVal) {
        form.append("mount", mountVal);
      }
      if (labelsArg && Object.keys(labelsArg).length > 0) {
        form.append("labels", JSON.stringify(labelsArg));   // HITL 인식 M 경로
      }
      options.body = form;
    }
    fetch("/api/analyze", options)
      .then((res) => {
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        return res.json();
      })
      .then((d) => {
        if (analysisRequestIdRef.current !== requestId) {
          return;
        }
        setAnalysis({
          drawingInfo: d.drawingInfo ?? null,
          roomJudgments: d.roomJudgments ?? [],
          violations: d.violations ?? [],
          judgmentSource: d.judgmentSource,
        });
        setAnalysisPendingMessage(null);
        if (file) {
          setToast(d.drawingInfo?.error ? `추출 실패: ${d.drawingInfo.error}` : d.drawingInfo?.analysisStatus === "recovered" ? `${file.name} 복구 분석 완료` : `${file.name} 분석 완료`);
        }
      })
      .catch((err) => {
        if (analysisRequestIdRef.current !== requestId) {
          return;
        }
        const message = err?.name === "AbortError"
          ? "도면 분석 시간 초과 (30초) — 백엔드 상태를 확인해주세요"
          : "백엔드 연결 실패 — 서버 상태를 확인해주세요";
        setAnalysisPendingMessage(null);
        if (file) {
          setAnalysis({
            drawingInfo: { fileName: file.name, error: message, errorCode: err?.name === "AbortError" ? "analysis_timeout" : "analysis_connection_failed" },
            roomJudgments: [],
            violations: [],
          });
        }
        setToast(message);
      })
      .finally(() => clearTimeout(timer));
  }, []);

  // 업로드 도면의 소방 심볼 인식(HITL 매니페스트). 자동추정을 labels 초기값으로 프리필.
  const runRecognize = useCallback((file: File) => {
    const seq = ++recognizeSeqRef.current;   // 최신 인식 요청만 반영(연속 업로드 레이스 방지)
    const form = new FormData();
    form.append("file", file);
    fetch("/api/recognize", { method: "POST", body: form })
      .then((res) => res.json())
      .then((d) => {
        if (seq !== recognizeSeqRef.current) return;   // 더 최신 인식이 진행됨 → 폐기
        if (!Array.isArray(d.classes)) {
          setRecognition(null);
          return;
        }
        setRecognition({ classes: d.classes, legendTypes: d.legendTypes ?? [], facilityOptions: d.facilityOptions ?? [],
          detectorContext: d.detectorContext, scopeHint: d.scopeHint });
        const init: Record<string, string> = {};
        for (const c of d.classes as SymbolClass[]) {
          if (c.guess) init[c.classId] = c.guess;    // 레이어/블록명 자동추정 프리필
        }
        setLabels(init);
      })
      .catch(() => { if (seq === recognizeSeqRef.current) setRecognition(null); });   // 실패는 조용히
  }, []);

  const handleLabelChange = (classId: string, facility: string) => {
    setLabels((prev) => {
      const next = { ...prev };
      if (facility) {
        next[classId] = facility;
      } else {
        delete next[classId];
      }
      return next;
    });
  };
  useEffect(() => {
    runAnalysis();
  }, [runAnalysis]);

  const updateStatus = useCallback((message: string) => {
    setToast(message);
  }, []);

  useEffect(() => {
    zoomLevelRef.current = zoomLevel;
  }, [zoomLevel]);

  const handleTopAction = (action: Exclude<DialogType, null>) => {
    setDialog((current) => current === action ? null : action);
  };

  const handleDrawingUpload = (file: File) => {
    setUploadedFile(file);
    setAnalysis({ drawingInfo: null, roomJudgments: [], violations: [] });
    setViewerDrawingInfo(null);
    setZoomLevel((current) => Math.max(current, uploadedDrawingInitialZoom));
    setPanOffset({ x: 0, y: 0 });
    setRecognition(null);
    setLabels({});
    setAiResult(null);
    setShowReport(false);   // 업로드 시 분석·판정 뷰로 시작(보고서는 사용자가 탭 선택 시만)
    setAnalysis({ drawingInfo: null, roomJudgments: [], violations: [] });   // 이전 파일 결과 잔류 방지
    setToast(`${file.name} 분석 중… (도면 정보 + 방·심볼 추출)`);
    runAnalysis(file, structure, occupancy, mount);
    runRecognize(file);        // 소방 심볼 인식 매니페스트(HITL 명명용)
    runAiRooms(file);          // 기하 방추출 자동 실행(심볼처럼 바로 — flood-fill 대체)
  };

  // 사용자가 지정한 종류(labels)로 인식 M 기반 실판정
  const handleJudgeWithLabels = () => {
    if (uploadedFile) {
      setToast("지정한 소방 심볼 종류로 판정 중…");
      runAnalysis(uploadedFile, structure, occupancy, mount, labels);
    }
  };

  // AI 방찾기(기하): 방 레이어 없는 실무 도면에서 벽으로 방 경계·면적 추출 + (라벨 있으면)감지기 판정.
  // fileArg=업로드 직후 자동실행용(state 반영 전 stale 방지). seq(aiSeqRef)만으로 레이스 가드.
  const runAiRooms = (fileArg?: File, judge = false, sOv?: string, oOv?: string, mOv?: string) => {
    const file = fileArg ?? uploadedFile;
    if (!file) {
      return;
    }
    const s = sOv ?? structure, o = oOv ?? occupancy, m = mOv ?? mount;   // 조건 override(selector 변경 시 fresh 값)
    const seq = ++aiSeqRef.current;   // 더 최신 요청/새 업로드면 이 결과 폐기(seq-only 가드)
    setAiResult({ loading: true });
    if (!judge) { setRoomDecisions({}); setManualAreas({}); }   // 판정 재실행은 확인/제외 유지
    setToast(judge ? "라벨한 심볼로 불법/합법 판정 중…" : "벽으로 방을 추출 중… (기하)");
    const form = new FormData();
    form.append("file", file);
    if (s) form.append("structure", s);
    if (o) form.append("occupancy", o);
    if (m) form.append("mount", m);
    if (Object.keys(labels).length > 0) {
      form.append("labels", JSON.stringify(labels));   // 라벨한 연기/열 감지기로 배치 판정(불법/합법)
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 120000);
    fetch("/api/rooms_ai", { method: "POST", body: form, signal: controller.signal })
      .then((res) => res.json())
      .then((d) => {
        if (seq !== aiSeqRef.current) return;   // 더 최신 요청 진행 중 → 폐기(레이스 방지)
        setAiResult({ rooms: d.aiRooms ?? [], violations: d.violations ?? [],
                      note: d.note, available: d.available, loading: false });
        setToast(d.available === false ? "기하 방추출 미설치 환경입니다"
                 : `방 ${(d.aiRooms ?? []).length}개 추출${judge ? " · 판정 완료" : ""}`);
      })
      .catch((err) => {
        if (seq !== aiSeqRef.current) return;
        setAiResult({ rooms: [], violations: [], loading: false });
        setToast(err?.name === "AbortError" ? "시간 초과(1분)" : "방 추출 실패");
      })
      .finally(() => clearTimeout(timer));
  };

  // AI 방 판정 클릭 → 뷰어를 그 방으로 이동(월드좌표 center → zoom/pan 계산).
  const focusRoom = (center?: number[]) => {
    if (!center || center.length < 2 || !cadViewerRef.current) {
      return;
    }
    const target = cadViewerRef.current.focusOnWorld(center[0], center[1], 260);
    if (target) {
      setZoomLevel(target.zoomLevel);
      setPanOffset(target.panOffset);
      setToast("선택한 방으로 이동");
    }
  };

  // 구조/용도/층고 변경 → 재판정. HITL 라벨이 있으면 유지(인식 M 판정을 자동으로 되돌리지 않음).
  const labelsOrUndef = Object.keys(labels).length > 0 ? labels : undefined;
  // 조건 변경 시 AI 방은 지우지 않고 **새 조건으로 재추출·재판정**(확인/제외 유지). 방 자체는 조건 무관이나
  // 판정(감지면적)은 조건 의존 → fresh 값으로 다시 돈다. runAiRooms가 seq로 in-flight 정리.

  // 건물 구조 변경 → 재판정(구조는 열감지기 기준면적에 영향 = 안전 임계 입력)
  const handleStructureChange = (value: string) => {
    setStructure(value);
    if (uploadedFile) {
      runAnalysis(uploadedFile, value, occupancy, mount, labelsOrUndef);
      runAiRooms(uploadedFile, true, value, occupancy, mount);
    }
  };

  // 용도 변경 → 재판정(취침거실 연기의무·스프링클러 반경 등에 영향)
  const handleOccupancyChange = (value: string) => {
    setOccupancy(value);
    if (uploadedFile) {
      runAnalysis(uploadedFile, structure, value, mount, labelsOrUndef);
      runAiRooms(uploadedFile, true, structure, value, mount);
    }
  };

  // 부착높이(층고) 변경 → 재판정(4m 경계로 감지면적이 갈림 = 안전 임계 입력)
  const handleMountChange = (value: string) => {
    setMount(value);
    if (uploadedFile) {
      runAnalysis(uploadedFile, structure, occupancy, value, labelsOrUndef);
      runAiRooms(uploadedFile, true, structure, occupancy, value);
    }
  };

  const handleFitView = () => {
    setZoomLevel(100);
    setPanOffset({ x: 0, y: 0 });
    setToast("맞춤 명령 실행 · ZOOM EXTENTS");
  };

  const handleDrawingWheel = useCallback((event: globalThis.WheelEvent) => {
    event.preventDefault();
    const target = event.currentTarget;
    if (!(target instanceof HTMLElement)) {
      return;
    }

    const cursorOffset = getPointerOffsetFromCenter(event, target);
    const currentZoom = zoomLevelRef.current;
    const nextZoom = getWheelZoomLevel(currentZoom, event.deltaY);
    const zoomRatio = nextZoom / currentZoom;
    zoomLevelRef.current = nextZoom;
    setPanOffset((currentPan) => getCursorAnchoredPanOffset(currentPan, cursorOffset, zoomRatio));
    setZoomLevel(nextZoom);
  }, []);

  useEffect(() => {
    const drawingCard = drawingCardRef.current;
    if (!drawingCard) {
      return;
    }

    drawingCard.addEventListener("wheel", handleDrawingWheel, { passive: false });
    return () => drawingCard.removeEventListener("wheel", handleDrawingWheel);
  }, [handleDrawingWheel]);

  const handleDrawingPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || isInteractiveDrawingTarget(event.target)) {
      return;
    }

    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragState({
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      x: panOffset.x,
      y: panOffset.y,
    });
  };

  const handleDrawingPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragState || dragState.pointerId !== event.pointerId) {
      return;
    }

    if ((event.buttons & 1) !== 1) {
      setDragState(null);
      return;
    }

    event.preventDefault();
    setPanOffset({
      x: dragState.x + event.clientX - dragState.startX,
      y: dragState.y + event.clientY - dragState.startY,
    });
  };

  const stopDrawingPan = (event: PointerEvent<HTMLDivElement>) => {
    if (dragState?.pointerId === event.pointerId) {
      setDragState(null);
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
    }
  };

  const handleColumnResizeStart = (side: ColumnResizeSide, event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    setColumnResize({
      pointerId: event.pointerId,
      side,
      startX: event.clientX,
      leftWidth: panelWidths.left,
      rightWidth: panelWidths.right,
    });
  };

  const handleColumnResizeMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!columnResize || columnResize.pointerId !== event.pointerId) {
      return;
    }

    if ((event.buttons & 1) !== 1) {
      setColumnResize(null);
      return;
    }

    event.preventDefault();
    const scale = viewportFrame.scale || 1;
    const deltaX = (event.clientX - columnResize.startX) / scale;
    setPanelWidths(getResizedPanelWidths(columnResize, deltaX, workspaceRef.current, scale));
  };

  const stopColumnResize = (event: PointerEvent<HTMLDivElement>) => {
    if (columnResize?.pointerId === event.pointerId) {
      setColumnResize(null);
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
    }
  };

  const workspaceStyle = {
    "--left-panel-width": `${panelWidths.left}px`,
    "--right-panel-width": `${panelWidths.right}px`,
  } as CSSProperties;

  return (
    <div
      className="viewport-shell"
      style={{
        "--viewport-scale": viewportFrame.scale,
        "--app-frame-height": `${viewportFrame.frameHeight}px`,
      } as CSSProperties}
    >
      <main className="app-shell">
      <TopBar
        selectedFileName={uploadedFile?.name}
        onTopAction={handleTopAction}
        shareMenuOpen={dialog === "export"}
        onShareMenuClose={() => setDialog(null)}
        onEmailFacts={() => {
          openFactsEmail(getFactsMarkdown(
            uploadedFile?.name ?? "선택된 도면 없음",
            visibleDrawingInfo,
            analysisError,
            analysisRecovered,
          ));
          setDialog(null);
        }}
      />
      <div
        ref={workspaceRef}
        className={`workspace ${columnResize ? "is-resizing-columns" : ""}`}
        style={workspaceStyle}
      >
        <aside className="left-panel">
          <section className="panel-block">
            <div className="panel-heading">
              <h2>도면 관리</h2>
            </div>
            <div className="panel-scroll">
              <div className="section-label-row">
                <span>도면 파일</span>
                <label className="primary-small upload-control">
                  파일 추가
                  <input
                    type="file"
                    accept=".dwg,.dxf"
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file) {
                        handleDrawingUpload(file);
                      }
                    }}
                  />
                </label>
              </div>
              <div className="file-list">
                {uploadedFile ? (
                  <button
                    className="file-item active"
                    onClick={() => setToast(`${uploadedFile.name} 도면을 선택했습니다.`)}
                  >
                    <span>
                      <strong>{uploadedFile.name}</strong>
                      <small>{formatFileSize(uploadedFile.size)} · 업로드됨</small>
                    </span>
                    <em>표시됨</em>
                  </button>
                ) : null}
              </div>
              <div className="upload-guide">
                <strong>권장 도면 조건</strong>
                <ul>
                  <li><b>건축 평면도</b>(벽 위주) — 가구·집기·설비 배관이 <b>없을수록</b> 방·면적 자동추출이 정확합니다</li>
                  <li>방 이름이 <b>문자(텍스트)</b>로 표기</li>
                  <li>소방 설비(감지기·스프링클러)가 <b>레이어로 구분</b></li>
                  <li><b>1개 층</b> 평면 · DWG 또는 DXF</li>
                </ul>
                <p>※ 가구가 섞이면 작은 방이 잘게 쪼개져 면적 추출이 부정확해집니다.</p>
              </div>
            </div>
          </section>

          {analysis.drawingInfo ? (
            <section className="panel-block compact">
              <div className="panel-heading inline">
                <h3>도면 정보</h3>
                <Icon name="layers" />
              </div>
              <div className="panel-scroll">
                {analysis.drawingInfo.error ? (
                  <p style={{ fontSize: 12.5, opacity: 0.7, padding: "4px" }}>{analysis.drawingInfo.error}</p>
                ) : (
                  <div style={{ fontSize: 12.5, lineHeight: 1.7, padding: "2px 4px" }}>
                    {analysis.drawingInfo.analysisStatus === "recovered" ? (
                      <div style={{ marginBottom: 6, color: "#facc15" }}>복구 분석 · {getAnalysisSourceLabel(analysis.drawingInfo.analysisSource)}</div>
                    ) : null}
                    <div>레이어 <b>{analysis.drawingInfo.layerCount}</b> · 요소 <b>{analysis.drawingInfo.entityCount?.toLocaleString()}</b></div>
                    <div style={{ marginTop: 6, opacity: 0.85 }}>소방 레이어: {(analysis.drawingInfo.fireLayers ?? []).slice(0, 6).join(", ") || "—"}</div>
                    <div style={{ marginTop: 6, opacity: 0.85 }}>실명: {(analysis.drawingInfo.roomNames ?? []).slice(0, 8).join(", ") || "—"}</div>
                    {(analysis.drawingInfo.analysisWarnings ?? []).length > 0 ? (
                      <div style={{ marginTop: 8, opacity: 0.72 }}>
                        {(analysis.drawingInfo.analysisWarnings ?? []).slice(0, 2).map((warning) => (
                          <div key={warning}>※ {warning}</div>
                        ))}
                      </div>
                    ) : null}
                  </div>
                )}
              </div>
            </section>
          ) : null}
        </aside>

        <div
          className={`column-resize-handle ${columnResize?.side === "left" ? "active" : ""}`}
          role="separator"
          aria-orientation="vertical"
          aria-label="도면 관리 구역 폭 조절"
          onPointerDown={(event) => handleColumnResizeStart("left", event)}
          onPointerMove={handleColumnResizeMove}
          onPointerUp={stopColumnResize}
          onPointerCancel={stopColumnResize}
        />

        <section className="canvas-panel">
          <div
            ref={drawingCardRef}
            className={`drawing-card autocad-space tool-pan ${dragState ? "is-panning" : ""}`}
            onPointerDown={handleDrawingPointerDown}
            onPointerMove={handleDrawingPointerMove}
            onPointerUp={stopDrawingPan}
            onPointerCancel={stopDrawingPan}
          >
            {uploadedFile ? (
              <CadFileViewer
                ref={cadViewerRef}
                file={uploadedFile}
                visibleLayerIds={NO_VISIBLE_LAYERS}
                opacity={100}
                zoomLevel={zoomLevel}
                resolutionBaselineZoomLevel={uploadedDrawingInitialZoom}
                panOffset={panOffset}
                onStatusChange={updateStatus}
                onDrawingInfoChange={setViewerDrawingInfo}
              />
            ) : null}
            <button
              type="button"
              className="fit-floating-button"
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.stopPropagation();
                handleFitView();
              }}
              aria-label="도면 맞춤"
            >
              <Icon name="fit" />
              <span>맞춤</span>
            </button>
          </div>
        </section>

        <div
          className={`column-resize-handle ${columnResize?.side === "right" ? "active" : ""}`}
          role="separator"
          aria-orientation="vertical"
          aria-label="법규 판정 구역 폭 조절"
          onPointerDown={(event) => handleColumnResizeStart("right", event)}
          onPointerMove={handleColumnResizeMove}
          onPointerUp={stopColumnResize}
          onPointerCancel={stopColumnResize}
        />

        <aside className="right-panel">
          {uploadedFile ? (
            <div className="view-tabs">
              <button onClick={() => setShowReport(false)}
                style={{ cursor: "pointer",
                  border: `1px solid ${!showReport ? "rgba(130,160,210,0.6)" : "rgba(120,130,150,0.3)"}`,
                  background: !showReport ? "rgba(90,120,180,0.3)" : "transparent", color: !showReport ? "#dce7f8" : "#9aa6ba" }}>
                🔍 분석 · 판정
              </button>
              <button onClick={() => setShowReport(true)} disabled={!shouldShowReport}
                style={{ cursor: shouldShowReport ? "pointer" : "not-allowed",
                  border: `1px solid ${showReport ? "rgba(130,160,210,0.6)" : "rgba(120,130,150,0.3)"}`,
                  background: showReport ? "rgba(90,120,180,0.3)" : "transparent", color: showReport ? "#dce7f8" : "#9aa6ba", opacity: shouldShowReport ? 1 : 0.5 }}>
                📄 보고서
              </button>
            </div>
          ) : null}
          <div className="right-panel-body">
          {!showReport ? (
            <section className="analysis-panel fit-panel">
              <div className="list-header">
                <h3>법규 판정 (NFTC)</h3>
                <span className="panel-status">
                  {(analysis.violations ?? []).length > 0 ? "실 판정" : "요구 산정"}
                </span>
              </div>
              {(analysis.violations ?? []).length > 0 ? (
                <div className="analysis-violation-block">
                  <div className="analysis-counts">
                    <span className="danger">위반 {(analysis.violations ?? []).filter((v) => v.status === "violation").length}</span>
                    <span className="success">적합 {(analysis.violations ?? []).filter((v) => v.status === "compliant").length}</span>
                    <span className="warning">확인필요 {(analysis.violations ?? []).filter((v) => v.status === "not_applicable").length}</span>
                    <span>· 배치 vs 필요</span>
                  </div>
                  <div className="analysis-list">
                    {(analysis.violations ?? []).slice().sort((a, b) => (a.status === "violation" ? 0 : a.status === "not_applicable" ? 1 : 2) - (b.status === "violation" ? 0 : b.status === "not_applicable" ? 1 : 2)).map((v, i) => {
                      const tone = v.status === "violation" ? { className: "violation", label: "위반" }
                        : v.status === "not_applicable" ? { className: "review", label: "확인필요" }
                        : { className: "compliant", label: "적합" };
                      return (
                        <div key={`${v.ruleId}-${i}`} className={`analysis-item ${tone.className}`}>
                          <b>{tone.label}</b> · {v.description}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : null}
              {visibleDrawingInfo && !visibleDrawingInfo.error ? (
                <div className="drawing-facts">
                  <p className="analysis-kicker">
                    {visibleDrawingInfo.source === "viewer" && analysisError ? "브라우저 렌더링 보조 정보" : analysisRecovered ? <>복구 분석으로 확인한 <b>도면 사실</b></> : <>업로드 도면에서 확인한 <b>실제 사실</b></>}
                  </p>
                  {visibleDrawingInfo.source === "viewer" && analysisError ? null : (
                    <>
                      <div className="fact-label">
                        방 {visibleDrawingInfo.roomNames?.length ?? 0}개
                      </div>
                      <div className="fact-chip-row">
                        {(visibleDrawingInfo.roomNames ?? []).map((r) => (
                          <span key={r} className="fact-chip">{r}</span>
                        ))}
                      </div>
                      <div className="fact-label">
                        소방 설비 레이어 {visibleDrawingInfo.fireLayers?.length ?? 0}개
                      </div>
                      <div className="fact-chip-row">
                        {(visibleDrawingInfo.fireLayers ?? []).slice(0, 8).map((l) => (
                          <span key={l} className="fact-chip fire">{l}</span>
                        ))}
                      </div>
                    </>
                  )}
                  {visibleDrawingInfo.source === "viewer" ? (
                    <p className="analysis-subtle">
                      브라우저 렌더러 기준: 레이어 <b>{visibleDrawingInfo.layerCount ?? 0}</b>개 · 요소 <b>{visibleDrawingInfo.entityCount?.toLocaleString() ?? 0}</b>개
                    </p>
                  ) : null}
                  {analysisError ? (
                    <p className="analysis-subtle">
                      정밀 분석 보류: {analysisError}
                    </p>
                  ) : null}
                  {analysisRecovered ? (
                    <p className="analysis-subtle">
                      기본 DXF 정밀 파싱은 실패했지만 {getAnalysisSourceLabel(analysis.drawingInfo?.analysisSource)} 경로로 레이어·텍스트 정보를 복구했습니다.
                    </p>
                  ) : null}
                  {analysisRecovered && analysisWarnings.length > 0 ? (
                    <div className="analysis-subtle">
                      {analysisWarnings.slice(0, 3).map((warning) => (
                        <div key={warning}>※ {warning}</div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : (
                <p className="analysis-empty">
                  도면을 업로드하면 이 도면의 방·소방 설비를 추출합니다.
                </p>
              )}
            </section>
          ) : null}
          {uploadedFile && !showReport ? (
            <section className="analysis-panel">
            {analysisError ? (
              <div style={{ marginBottom: 14, padding: "10px 12px", borderRadius: 8,
                background: "rgba(210,150,60,0.12)", border: "1px solid rgba(210,150,60,0.34)" }}>
                <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 6, color: "#e8b871" }}>
                  ⚠️ 심볼 인식 · 방 추출 · 불법/합법 판정을 쓸 수 없습니다
                </div>
                <div style={{ fontSize: 11, lineHeight: 1.65, opacity: 0.9 }}>
                  {(uploadedFile?.name.split(".").pop()?.toUpperCase() === "DWG") ? (
                    <>이 도면은 <b>DWG</b>라 브라우저 뷰어는 그리지만, FireVal 엔진(심볼·방·판정)은 <b>DXF</b>가 필요합니다.
                    이 서버엔 DWG→DXF 변환 도구가 없어 정밀 분석을 못 합니다.<br />
                    → CAD에서 <b>DXF로 저장(다른 이름으로 저장 → DXF)</b> 후 다시 업로드하면 모든 기능이 동작합니다.</>
                  ) : (
                    <>정밀 분석을 못 했습니다: {analysisError}<br />
                    → DXF 파일이 손상되지 않았는지 확인하거나 다시 내보내 업로드해 주세요.</>
                  )}
                </div>
                <div style={{ fontSize: 10.5, opacity: 0.62, marginTop: 7, lineHeight: 1.5 }}>
                  위 <b>법규 판정(NFTC)</b> 카드의 값은 브라우저 렌더러가 읽은 <b>레이어·요소 수 참고 정보</b>이며, 실제 법규 판정이 아닙니다.
                </div>
              </div>
            ) : null}
            {recognition && recognition.classes.length > 0 ? (
              <div style={{ marginBottom: 14 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "4px 0 6px" }}>
                  <span style={{ fontSize: 12.5, fontWeight: 600 }}>소방 심볼 인식</span>
                  <span style={{ fontSize: 11, opacity: 0.6 }}>{recognition.classes.length}종{recognition.detectorContext === false ? "" : " · 종류 지정 시 실판정"}</span>
                </div>
                {recognition.scopeHint ? (
                  <div style={{ fontSize: 10.5, opacity: 0.72, margin: "0 0 6px" }}>도면 설비: {recognition.scopeHint}</div>
                ) : null}
                {recognition.detectorContext === false ? (
                  <div style={{ fontSize: 11, lineHeight: 1.5, margin: "0 0 8px", padding: "7px 9px", borderRadius: 6,
                    background: "rgba(90,150,210,0.12)", color: "#9fc0e5", border: "1px solid rgba(90,150,210,0.28)" }}>
                    ℹ️ 이 도면은 <b>감지기(자동화재탐지) 도면이 아닙니다</b> — 감지면적 판정 해당 없음. 아래 심볼은 참고용이라 종류 지정 없이 넘어가도 됩니다.
                  </div>
                ) : null}
                {recognition.legendTypes.length > 0 ? (
                  <div style={{ fontSize: 11, opacity: 0.72, marginBottom: 6 }}>범례 종류(힌트): {recognition.legendTypes.slice(0, 6).join(", ")}</div>
                ) : null}
                <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 300, overflowY: "auto" }}>
                  {recognition.classes.map((c) => (
                    <div key={c.classId} style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 7px", borderRadius: 6,
                      background: c.needsHitl ? "rgba(210,160,60,0.10)" : "rgba(90,192,138,0.08)",
                      border: `1px solid ${c.needsHitl ? "rgba(210,160,60,0.3)" : "rgba(90,192,138,0.22)"}` }}>
                      <span aria-hidden style={{ width: 40, height: 40, flexShrink: 0, color: "#cbd5e8",
                        background: "rgba(18,24,38,0.5)", borderRadius: 4, display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden" }}
                        dangerouslySetInnerHTML={{ __html: c.thumbnail }} />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 11.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                          <b>×{c.count}</b> <span style={{ opacity: 0.55 }}>{c.layer}</span>
                          {c.needsHitl ? null : <span style={{ fontSize: 9.5, opacity: 0.55 }} title={c.reason}> · 자동</span>}
                        </div>
                        <select value={labels[c.classId] ?? ""} onChange={(e) => handleLabelChange(c.classId, e.target.value)}
                          style={{ fontSize: 11, padding: "2px 5px", marginTop: 3, width: "100%", borderRadius: 5,
                            background: "rgba(120,140,170,0.15)", color: "inherit", border: "1px solid rgba(120,140,170,0.35)" }}>
                          <option value="">{c.isDetector ? "감지기 종별 지정…" : "종류 지정…"}</option>
                          {recognition.facilityOptions.map((f) => (
                            <option key={f} value={f}>{FACILITY_LABELS[f] ?? f}</option>
                          ))}
                        </select>
                      </div>
                    </div>
                  ))}
                </div>
                <button onClick={handleJudgeWithLabels} style={{ marginTop: 8, width: "100%", fontSize: 12, padding: "7px", borderRadius: 6, cursor: "pointer",
                  background: "rgba(90,120,180,0.25)", color: "#cdddf5", border: "1px solid rgba(120,150,200,0.4)" }}>
                  이 종류로 실판정 →
                </button>
                <p style={{ fontSize: 10.5, opacity: 0.55, marginTop: 6, lineHeight: 1.5 }}>
                  감지기 <b>연기/열 종별</b>은 도면 기하로 자동 구분이 안 돼(안전) 직접 지정합니다. 클래스당 1번 = 같은 심볼 전체에 적용.
                </p>
              </div>
            ) : null}
            {analysis.drawingInfo && !analysis.drawingInfo.error ? (
              <div style={{ marginBottom: 12 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "4px 0 8px" }}>
                  <span style={{ fontSize: 12.5, fontWeight: 600 }}>판정 조건</span>
                  <select
                    value={structure}
                    onChange={(event) => handleStructureChange(event.target.value)}
                    style={{ fontSize: 11.5, padding: "2px 6px", borderRadius: 6, background: "rgba(120,140,170,0.15)", color: "inherit", border: "1px solid rgba(120,140,170,0.35)" }}
                  >
                    <option value="">건물구조 미상</option>
                    <option value="fireproof">내화구조</option>
                    <option value="other">기타구조</option>
                  </select>
                  <select
                    value={occupancy}
                    onChange={(event) => handleOccupancyChange(event.target.value)}
                    style={{ fontSize: 11.5, padding: "2px 6px", borderRadius: 6, background: "rgba(120,140,170,0.15)", color: "inherit", border: "1px solid rgba(120,140,170,0.35)" }}
                  >
                    <option value="">용도 미상</option>
                    <option value="공동주택">공동주택</option>
                    <option value="숙박시설">숙박시설</option>
                    <option value="의료시설">의료시설</option>
                    <option value="노유자시설">노유자시설</option>
                    <option value="교육연구시설">교육연구시설</option>
                    <option value="업무시설">업무시설</option>
                  </select>
                  <select
                    value={mount}
                    onChange={(event) => handleMountChange(event.target.value)}
                    style={{ fontSize: 11.5, padding: "2px 6px", borderRadius: 6, background: "rgba(120,140,170,0.15)", color: "inherit", border: "1px solid rgba(120,140,170,0.35)" }}
                  >
                    <option value="">층고 미상</option>
                    <option value="lt4">천장 4m 미만</option>
                    <option value="ge4">천장 4m 이상</option>
                  </select>
                </div>
                {aiResult?.loading ? (
                  <p style={{ fontSize: 11.5, opacity: 0.65 }}>벽으로 방을 추출 중…(기하)</p>
                ) : (
                  <p style={{ fontSize: 11, opacity: 0.6, lineHeight: 1.55 }}>
                    업로드 시 벽으로 방을 자동 추출합니다(아래). 조건을 정하고 심볼 종류를 라벨한 뒤 <b>불법/합법 판정</b>을 누르세요.
                  </p>
                )}
              </div>
            ) : null}
            {aiResult && !aiResult.loading && (aiResult.rooms ?? []).length > 0 ? (
              (() => {
                const viols = aiResult.violations ?? [];
                const rooms = aiResult.rooms ?? [];
                // 면적은 이름이 아니라 center(고유 위치)로 매칭 — 동명 방 오염 방지(6축 [10]).
                const areaByCenter: Record<string, number | undefined> = {};
                rooms.forEach((r) => { if (Array.isArray(r.center)) areaByCenter[String(r.center)] = r.area_m2; });
                const areaOf = (v: { center?: number[] }) => (Array.isArray(v.center) ? areaByCenter[String(v.center)] : undefined);
                const keyOf = (v: { roomName?: string }, i: number) => `${v.roomName || "room"}#${i}`;   // 인덱스 포함 → 동명 방 충돌 방지
                const isNB = (v: { ruleId?: string }) => v.ruleId === "FV-DET-need_boundary";   // 벽 안 닫힘 = 경계 확인 필요
                const validArea = (k: string) => { const n = parseFloat(manualAreas[k]); return isFinite(n) && n > 0; };
                const setDecision = (k: string, val: "confirmed" | "excluded") =>
                  setRoomDecisions((prev) => {
                    const next = { ...prev };
                    if (prev[k] === val) delete next[k]; else next[k] = val;   // 같은 버튼 재클릭 = 취소
                    return next;
                  });
                const setArea = (k: string, val: string) => setManualAreas((prev) => ({ ...prev, [k]: val }));
                // NB(경계 미확정) 방은 백엔드 재판정이 없어 '확정'으로 세지 않음(6축 [2/6] false-resolution 방지).
                // 확정 = 확인된 기하 방만. NB·미확인은 대기(판정 미완료).
                let resolved = 0, excluded = 0, pending = 0;
                viols.forEach((v, i) => {
                  const k = keyOf(v, i);
                  if (roomDecisions[k] === "excluded") { excluded++; return; }
                  if (!isNB(v) && roomDecisions[k] === "confirmed") resolved++; else pending++;
                });
                const nViol = viols.filter((v, i) => !isNB(v) && roomDecisions[keyOf(v, i)] === "confirmed" && v.status === "violation").length;
                const judged = viols.some((v) => v.status === "violation" || v.status === "compliant");   // 실제 pass/fail 존재?(라벨 판정)
                const btn = (active: boolean, on: string, off: string) => ({
                  fontSize: 10.5, padding: "2px 9px", borderRadius: 5, cursor: "pointer",
                  border: `1px solid ${active ? on : "rgba(150,150,160,0.4)"}`,
                  background: active ? off : "transparent", color: active ? on : "#9aa6ba", fontWeight: active ? 600 : 400,
                });
                return (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 12.5, fontWeight: 600, margin: "4px 0 4px", color: "#cbb8f0" }}>
                  🤖 AI 방 추출 {rooms.length}개
                </div>
                <div style={{ fontSize: 10.5, lineHeight: 1.5, margin: "0 0 7px", padding: "6px 9px", borderRadius: 6,
                  background: "rgba(210,160,60,0.10)", color: "#d9b060", border: "1px solid rgba(210,160,60,0.25)" }}>
                  ⚠ <b>모든 방은 사람 확인 후 최종</b> — 자동 최종판정 없음. 기하 면적도 오차 가능(확인 대상), 경계 미확정 방은 면적 직접 입력.
                </div>
                <button onClick={() => runAiRooms(undefined, true)} disabled={aiResult?.loading}
                  style={{ width: "100%", fontSize: 12.5, fontWeight: 700, padding: "9px", borderRadius: 8, margin: "0 0 5px",
                    cursor: aiResult?.loading ? "wait" : "pointer",
                    background: "linear-gradient(90deg, rgba(90,120,180,0.38), rgba(120,90,200,0.34))",
                    color: "#dce7f8", border: "1px solid rgba(130,160,210,0.5)" }}>
                  {aiResult?.loading ? "판정 중…" : "🔍 불법 / 합법 판정하기"}
                </button>
                <p style={{ fontSize: 10, opacity: 0.55, margin: "0 0 7px", lineHeight: 1.5 }}>
                  {Object.keys(labels).length > 0
                    ? "라벨한 심볼 + 조건(구조·용도·층고)으로 방별 위반/적합을 산정합니다."
                    : "위 심볼 패널에서 종류를 라벨하면 위반/적합 판정, 없으면 요구 개수만 산정됩니다."}
                </p>
                <div style={{ fontSize: 11, margin: "0 0 6px", display: "flex", gap: 10 }}>
                  <span style={{ color: "#8d8" }}>확정 {resolved}</span>
                  <span style={{ color: "#d9b060" }}>대기 {pending}</span>
                  <span style={{ color: "#c88" }}>제외 {excluded}</span>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 320, overflowY: "auto" }}>
                  {viols.map((v, i) => {
                    const k = keyOf(v, i);
                    const nb = isNB(v);
                    const dec = roomDecisions[k];
                    const excludedR = dec === "excluded";
                    const confirmed = !nb && dec === "confirmed";
                    const areaOk = nb && validArea(k);
                    const resolvedR = confirmed;    // NB는 백엔드 재판정 없어 '해소'로 안 봄(6축 [2/6])
                    const area = areaOf(v);          // center 기반 매칭(동명 방 오염 방지, 6축 [10])
                    const clickable = Array.isArray(v.center) && v.center.length >= 2;
                    const tone = v.status === "violation" ? { fg: "#e88", label: "위반" }
                      : v.status === "compliant" ? { fg: "#8d8", label: "적합" }
                      : { fg: "#d9b060", label: "확인필요" };
                    const barColor = excludedR ? "#666" : nb ? "#d2a03c" : resolvedR ? "#5ac08a" : "#6c7688";
                    return (
                      <div key={`ai-${i}`}
                        style={{ fontSize: 11.5, lineHeight: 1.4, padding: "6px 8px", borderRadius: 6,
                          background: excludedR ? "rgba(120,120,130,0.07)" : nb ? "rgba(210,160,60,0.09)" : "rgba(90,192,138,0.07)",
                          borderLeft: `3px solid ${barColor}`, opacity: excludedR ? 0.5 : resolvedR ? 1 : 0.82 }}>
                        <div onClick={clickable ? () => focusRoom(v.center) : undefined}
                          title={clickable ? "클릭 → 도면에서 이 방으로 이동" : undefined}
                          style={{ cursor: clickable ? "pointer" : "default", textDecoration: excludedR ? "line-through" : "none" }}>
                          <b style={{ color: nb ? "#d9b060" : "#8fce9f" }}>
                            {nb ? "⚠ 경계 확인 필요" : `🟢 기하 면적${area ? ` ${area}㎡` : ""}`}
                          </b>
                          <span style={{ opacity: 0.6, marginLeft: 5 }}>{v.roomName}</span>
                          {clickable ? <span style={{ opacity: 0.5, marginLeft: 4 }}>↗</span> : null}
                        </div>
                        {!nb ? (
                          <div style={{ marginTop: 3 }}>
                            {confirmed ? <b style={{ color: tone.fg }}>{tone.label}</b> : <span style={{ color: "#9aa6ba" }}>확인 전 — 판정 보류</span>}
                            <span style={{ opacity: 0.7 }}> · {v.description}</span>
                          </div>
                        ) : (
                          <div style={{ marginTop: 3, opacity: 0.8 }}>
                            {areaOk ? `면적 ${manualAreas[k]}㎡ 입력됨 — 판정은 감지기 인식 후(경계 미확정)` : "벽이 안 닫힘(문틈/병합) — 면적 직접 입력하거나 제외"}
                          </div>
                        )}
                        <div style={{ display: "flex", gap: 6, marginTop: 5, alignItems: "center" }}>
                          {!nb ? (
                            <button onClick={() => setDecision(k, "confirmed")} style={btn(confirmed, "#8d8", "rgba(90,192,138,0.25)")}>
                              {confirmed ? "✓ 면적 확인됨" : "면적 확인"}
                            </button>
                          ) : (
                            <span style={{ display: "flex", alignItems: "center", gap: 3 }}>
                              <input type="number" min="0" value={manualAreas[k] ?? ""} onChange={(e) => setArea(k, e.target.value)}
                                placeholder="면적" style={{ width: 54, fontSize: 10.5, padding: "2px 5px", borderRadius: 4,
                                  border: `1px solid ${areaOk ? "#5ac08a" : "rgba(150,150,160,0.4)"}`,
                                  background: "rgba(255,255,255,0.06)", color: "#cde" }} />
                              <span style={{ fontSize: 10.5, color: areaOk ? "#8d8" : "#9aa6ba" }}>㎡{areaOk ? " ✓ 입력됨" : ""}</span>
                            </span>
                          )}
                          <button onClick={() => setDecision(k, "excluded")} style={btn(excludedR, "#e0a0a0", "rgba(200,110,110,0.22)")}>
                            {excludedR ? "✕ 제외됨" : "제외"}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
                {resolved > 0 ? (
                  <div style={{ fontSize: 11, marginTop: 7, padding: "6px 9px", borderRadius: 6,
                    background: "rgba(90,192,138,0.10)", color: "#9ecdb0" }}>
                    면적확인 <b>{resolved}</b>개{judged ? <> · 위반 <b>{nViol}</b></> : <span style={{ opacity: 0.7 }}> · pass/fail은 감지기 종류 지정 후</span>}{pending > 0 ? <span style={{ opacity: 0.7 }}> · 대기 {pending}개</span> : null}
                  </div>
                ) : (
                  <div style={{ fontSize: 11, marginTop: 7, padding: "6px 9px", borderRadius: 6,
                    background: "rgba(210,160,60,0.10)", color: "#d9b060" }}>
                    아직 확정된 방이 없습니다 — 기하 방은 <b>면적 확인</b>, 경계 미확정 방은 <b>면적 입력</b>. 확인 전엔 판정을 신뢰하지 않습니다(안전).
                  </div>
                )}
                <p style={{ fontSize: 10.5, opacity: 0.55, marginTop: 6, lineHeight: 1.5 }}>{aiResult.note}</p>
              </div>
                );
              })()
            ) : aiResult && !aiResult.loading && aiResult.available === false ? (
              <p style={{ fontSize: 11.5, opacity: 0.6, marginBottom: 12 }}>이 서버는 기하 방추출(shapely) 미설치 — 방 레이어 있는 도면만 실판정 가능.</p>
            ) : null}
            <p style={{ fontSize: 11.5, margin: 0, lineHeight: 1.6, padding: "10px 12px", borderRadius: 8, background: "rgba(90,120,180,0.12)", color: "#9fb4d8" }}>
              방 면적은 <b>기하 추출</b>(벽 닫힌 면, 안전마진). 벽 안 닫힌 방은 경계 확인 필요. 실 pass/fail엔 <b>구조·층고</b>도 필요(미상이면 보류). 어떤 방도 사람 확인 후 최종.
            </p>
            </section>
          ) : null}
          {showReport ? (
            <ReportPanel
              fileName={uploadedFile?.name ?? "선택된 도면 없음"}
              drawingInfo={visibleDrawingInfo}
              analysisError={analysisError}
              analysisRecovered={analysisRecovered}
            />
          ) : null}
          </div>
        </aside>
      </div>

      <footer className="status-bar">
        <span className="status-dot" />
        <strong>상태: {displayedStatus}</strong>
        {analysisError ? (
          <span className="status-warning">정밀 분석 보류: {analysisError}</span>
        ) : analysisRecovered ? (
          <span className="status-warning">복구 분석: 방 <b>{statusDrawingInfo?.roomNames?.length ?? 0}</b>개 · 소방레이어 <b>{statusDrawingInfo?.fireLayers?.length ?? 0}</b></span>
        ) : statusDrawingInfo && !statusDrawingInfo.error ? (
          <span>확인: 방 <b>{statusDrawingInfo.roomNames?.length ?? 0}</b>개 · 소방레이어 <b>{statusDrawingInfo.fireLayers?.length ?? 0}</b></span>
        ) : null}
        <em><Icon name="shield" /> 한국 화재안전기술기준 (NFTC) · FireVal 엔진</em>
      </footer>
      </main>
    </div>
  );
}

function useViewportFrame() {
  const getFrame = useCallback(() => {
    if (typeof window === "undefined") {
      return {
        scale: 1,
        frameHeight: designViewport.height,
      };
    }

    const scale = window.innerWidth / designViewport.width;
    return {
      scale,
      frameHeight: window.innerHeight / scale,
    };
  }, []);

  const [frame, setFrame] = useState(getFrame);

  useEffect(() => {
    const updateFrame = () => setFrame(getFrame());
    updateFrame();
    window.addEventListener("resize", updateFrame);
    return () => window.removeEventListener("resize", updateFrame);
  }, [getFrame]);

  return frame;
}

function TopBar({
  selectedFileName,
  onTopAction,
  shareMenuOpen,
  onShareMenuClose,
  onEmailFacts,
}: {
  selectedFileName?: string;
  onTopAction: (action: Exclude<DialogType, null>) => void;
  shareMenuOpen: boolean;
  onShareMenuClose: () => void;
  onEmailFacts: () => void;
}) {
  const topActionsRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!shareMenuOpen) {
      return;
    }

    const handlePointerDown = (event: globalThis.PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && topActionsRef.current?.contains(target)) {
        return;
      }
      onShareMenuClose();
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onShareMenuClose();
      }
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onShareMenuClose, shareMenuOpen]);

  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-mark"><Icon name="flame" /></div>
        <div>
          <h1>FireOpt</h1>
          <p>소방 설계 자동 최적화 시스템</p>
        </div>
      </div>
      <button className="project-select" disabled title="프로젝트 선택 — 준비 중">
        프로젝트_샘플 <Icon name="chevron" />
      </button>
      <div className="file-pill">
        <Icon name="document" />
        <span>{selectedFileName ?? "도면 파일을 선택해주세요"}</span>
      </div>
      <nav className="top-actions" ref={topActionsRef}>
        <button
          className="round"
          onClick={() => onTopAction("export")}
          aria-expanded={shareMenuOpen}
          aria-haspopup="menu"
          aria-label="공유"
          title="공유"
        >
          <Icon name="share" />
        </button>
        {shareMenuOpen ? (
          <ShareMenu
            onEmailFacts={onEmailFacts}
          />
        ) : null}
      </nav>
    </header>
  );
}

function ShareMenu({
  onEmailFacts,
}: {
  onEmailFacts: () => void;
}) {
  return (
    <div className="share-menu" role="menu" aria-label="공유 메뉴">
      <button type="button" role="menuitem" onClick={onEmailFacts}>
        <span>이메일로 보내기</span>
      </button>
    </div>
  );
}

function formatFileSize(size: number) {
  if (size < 1024 * 1024) {
    return `${Math.round(size / 1024)}KB`;
  }

  return `${(size / (1024 * 1024)).toFixed(1)}MB`;
}

function clampZoom(value: number) {
  return Math.max(zoomMin, Math.round(value));
}

function getResizedPanelWidths(
  resize: ColumnResizeState,
  deltaX: number,
  workspace: HTMLDivElement | null,
  viewportScale: number,
): PanelWidths {
  const availableWidth = getWorkspaceContentWidth(workspace, viewportScale);
  const maxLeft = Math.min(
    panelResizeLimits.sideMax,
    availableWidth - resize.rightWidth - panelResizeLimits.centerMin,
  );
  const maxRight = Math.min(
    panelResizeLimits.sideMax,
    availableWidth - resize.leftWidth - panelResizeLimits.centerMin,
  );

  if (resize.side === "left") {
    return {
      left: clampPanelWidth(resize.leftWidth + deltaX, panelResizeLimits.leftMin, maxLeft),
      right: resize.rightWidth,
    };
  }

  return {
    left: resize.leftWidth,
    right: clampPanelWidth(resize.rightWidth - deltaX, panelResizeLimits.rightMin, maxRight),
  };
}

function getWorkspaceContentWidth(workspace: HTMLDivElement | null, viewportScale: number) {
  if (!workspace || typeof window === "undefined") {
    return designViewport.width
      - panelResizeLimits.leftMin
      - panelResizeLimits.rightMin;
  }

  const style = window.getComputedStyle(workspace);
  const scale = viewportScale || 1;
  const width = workspace.getBoundingClientRect().width / scale;
  const paddingX = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
  const handleWidth = parseFloat(style.getPropertyValue("--resize-handle-width")) || 0;
  return width - paddingX - handleWidth * 2;
}

function clampPanelWidth(value: number, min: number, max: number) {
  const safeMax = Math.max(min, max);
  return Math.round(Math.min(Math.max(value, min), safeMax));
}

function getWheelZoomLevel(current: number, deltaY: number) {
  const zoomFactor = 1 + zoomWheelStep / 100;
  return clampZoom(deltaY < 0 ? current * zoomFactor : current / zoomFactor);
}

function getPointerOffsetFromCenter(event: globalThis.WheelEvent, element: HTMLElement): PanOffset {
  const rect = element.getBoundingClientRect();
  return {
    x: event.clientX - rect.left - rect.width / 2,
    y: event.clientY - rect.top - rect.height / 2,
  };
}

function getCursorAnchoredPanOffset(
  currentPan: PanOffset,
  cursorOffset: PanOffset,
  zoomRatio: number,
): PanOffset {
  if (!Number.isFinite(zoomRatio) || zoomRatio <= 0 || zoomRatio === 1) {
    return currentPan;
  }

  return {
    x: currentPan.x + (1 - zoomRatio) * (cursorOffset.x - currentPan.x),
    y: currentPan.y + (1 - zoomRatio) * (cursorOffset.y - currentPan.y),
  };
}

function isInteractiveDrawingTarget(target: EventTarget) {
  return target instanceof Element
    && Boolean(target.closest("button, input, select, textarea, label"));
}

function getEffectiveDrawingInfo(backendInfo: DrawingInfo | null, viewerInfo: DrawingInfo | null) {
  if (backendInfo && !backendInfo.error) {
    return { ...backendInfo, source: backendInfo.source ?? "backend" };
  }

  return viewerInfo;
}

function getAnalysisSourceLabel(source?: string) {
  if (source === "dwgread-json") {
    return "DWG JSON 복구";
  }
  if (source === "dwg2dxf-minimal") {
    return "minimal DXF 복구";
  }
  return "백엔드 정밀 분석";
}

function getFactsMarkdown(fileName: string, drawingInfo: DrawingInfo | null, analysisError?: string, analysisRecovered?: boolean) {
  const rooms = drawingInfo?.roomNames ?? [];
  const fireLayers = drawingInfo?.fireLayers ?? [];
  const layerNames = drawingInfo?.layerNames ?? [];
  const hasFacts = Boolean(drawingInfo && !drawingInfo.error);
  const viewerOnly = drawingInfo?.source === "viewer" && Boolean(analysisError);
  const sourceLabel = drawingInfo?.source === "viewer" ? "브라우저 CAD 렌더러" : analysisRecovered ? getAnalysisSourceLabel(drawingInfo?.analysisSource) : "백엔드 FireVal 분석";

  return [
    `# 소방 도면 사실 요약 — ${drawingInfo?.fileName ?? fileName}`,
    ``,
    hasFacts ? `- 기준: ${sourceLabel}` : ``,
    hasFacts ? `- 레이어: ${drawingInfo?.layerCount ?? "-"}개` : `- (업로드된 도면 없음 또는 파싱 실패)`,
    ...(hasFacts ? [`- 도형 요소: ${(drawingInfo?.entityCount ?? 0).toLocaleString()}개`] : []),
    ...(analysisError ? [`- 정밀 분석 상태: ${analysisError}`] : []),
    ...(analysisRecovered ? [`- 정밀 분석 상태: 기본 DXF 파싱 실패 후 복구 분석 완료`] : []),
    ...((drawingInfo?.analysisWarnings ?? []).map((warning) => `- 주의: ${warning}`)),
    ``,
    ...(viewerOnly ? [
      `## 브라우저 렌더링 보조 정보`,
      `- DWG/DXF 화면 렌더링은 완료됐지만 백엔드 정밀 분석은 보류됐습니다.`,
      `- 아래 레이어 샘플은 방·소방 설비 판정 결과가 아닙니다.`,
    ] : [
      `## 추출된 방 (${rooms.length})`,
      ...(rooms.length > 0 ? rooms.map((r) => `- ${r}`) : [`- 정밀 분석 연결 후 표시`]),
      ``,
      `## 소방 설비 레이어 (${fireLayers.length})`,
      ...(fireLayers.length > 0 ? fireLayers.map((l) => `- ${l}`) : [`- 정밀 분석 연결 후 표시`]),
    ]),
    ...(layerNames.length > 0 ? [
      ``,
      `## 렌더러 확인 레이어 샘플 (${layerNames.length})`,
      ...layerNames.map((l) => `- ${l}`),
    ] : []),
    ``,
    `---`,
    `※ NFTC 적정성 판정(방별 필요 감지기 수)은 설비 심볼 인식·방 면적 자동추출 연결 후 제공됩니다.`,
    `   본 문서는 도면에서 자동 추출한 '사실'만 담습니다.`,
  ].join("\n");
}

function downloadFactsMarkdown(factsMarkdown: string) {
  const blob = new Blob([factsMarkdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "소방도면_사실요약.md";
  anchor.click();
  URL.revokeObjectURL(url);
}

function openFactsEmail(factsMarkdown: string) {
  downloadFactsMarkdown(factsMarkdown);
  const subject = encodeURIComponent("소방 도면 사실 요약");
  const body = encodeURIComponent(factsMarkdown);
  window.open(`https://mail.google.com/mail/?view=cm&fs=1&su=${subject}&body=${body}`, "_blank", "noopener,noreferrer");
}

function ReportPanel({
  fileName,
  drawingInfo,
  analysisError,
  analysisRecovered,
}: {
  fileName: string;
  drawingInfo: DrawingInfo | null;
  analysisError?: string;
  analysisRecovered?: boolean;
}) {
  const hasFacts = Boolean(drawingInfo && !drawingInfo.error);
  const viewerOnly = drawingInfo?.source === "viewer" && Boolean(analysisError);
  const factsMarkdown = getFactsMarkdown(fileName, drawingInfo, analysisError, analysisRecovered);

  return (
    <section className="report-panel">
      <div className="list-header">
        <h3>보고서</h3>
        <button
          className="report-icon-button"
          aria-label="사실 요약 다운로드"
          title="사실 요약 다운로드"
          onClick={() => downloadFactsMarkdown(factsMarkdown)}
        >
          <Icon name="download" />
        </button>
      </div>
      <div className="report-summary">
        <article>
          <span>도면</span>
          <strong>{fileName}</strong>
        </article>
        <article>
          <span>{viewerOnly ? "렌더러 레이어" : "레이어"}</span>
          <strong>{hasFacts ? `${drawingInfo?.layerCount ?? 0}개` : "-"}</strong>
        </article>
        <article>
          <span>{viewerOnly ? "렌더러 요소" : "도형 요소"}</span>
          <strong>{hasFacts ? `${(drawingInfo?.entityCount ?? 0).toLocaleString()}개` : "-"}</strong>
        </article>
      </div>
      <div className="report-sheet">
        <h3>소방 도면 사실 요약</h3>
        {analysisRecovered ? (
          <p className="report-note">기본 DXF 정밀 파싱 실패 후 {getAnalysisSourceLabel(drawingInfo?.analysisSource)}로 복구한 요약입니다.</p>
        ) : null}
        <pre className="report-markdown-preview">
          {factsMarkdown}
        </pre>
        <p className="report-note">※ 도면에서 자동 추출한 사실 · NFTC 적정성 판정은 인식 파이프라인 연결 후</p>
      </div>
    </section>
  );
}

function Icon({ name }: { name: string }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" } as const;
  switch (name) {
    case "flame":
      return <svg viewBox="0 0 24 24" {...common}><path d="M3 10h18M5 10v9M9 10v9M15 10v9M19 10v9M4 21h16M12 3 3 8h18Z" /></svg>;
    case "document":
      return <svg viewBox="0 0 24 24" {...common}><path d="M6 3h8l4 4v14H6Z" /><path d="M14 3v5h4M9 13h6M9 17h6" /></svg>;
    case "layers":
      return <svg viewBox="0 0 24 24" {...common}><path d="M12 3 3 8l9 5 9-5Z" /><path d="m3 12 9 5 9-5M3 16l9 5 9-5" /></svg>;
    case "chevron":
      return <svg viewBox="0 0 24 24" {...common}><path d="m7 9 5 5 5-5" /></svg>;
    case "fit":
      return <svg viewBox="0 0 24 24" {...common}><path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5" /></svg>;
    case "download":
      return <svg viewBox="0 0 24 24" {...common}><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></svg>;
    case "share":
      return <svg viewBox="0 0 24 24" {...common}><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><path d="m8.6 10.6 6.8-4.2M8.6 13.4l6.8 4.2" /></svg>;
    case "shield":
      return <svg viewBox="0 0 24 24" {...common}><path d="M12 3 5 6v5c0 5 3 8 7 10 4-2 7-5 7-10V6Z" /><path d="m9 12 2 2 4-5" /></svg>;
    default:
      return <svg viewBox="0 0 24 24" {...common}><circle cx="12" cy="12" r="8" /></svg>;
  }
}
