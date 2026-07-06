import { useCallback, useEffect, useRef, useState, type PointerEvent } from "react";
import { CadFileViewer, type CadFileViewerHandle } from "./components/CadFileViewer";
import type { LayerId } from "./types";

// CAD 뷰어는 도면 자체의 레이어 가시성으로 렌더 — 앱 레이어 필터는 미연결(PART B 예정)
const NO_VISIBLE_LAYERS = new Set<LayerId>();

type ToolId = "pan" | "zoomIn" | "zoomOut" | "fit";
type DialogType = "save" | "report" | "export" | "notifications" | null;
type PanOffset = { x: number; y: number };
type DragState = PanOffset & { pointerId: number; startX: number; startY: number };

const zoomMin = 25;
const zoomButtonStep = 25;
const zoomWheelStep = 10;
const uploadedDrawingInitialZoom = 150;

const toolDefinitions: Array<{ id: ToolId; label: string; icon: string; command: string }> = [
  { id: "pan", label: "이동", icon: "move", command: "PAN" },
  { id: "zoomIn", label: "확대", icon: "zoomIn", command: "ZOOM +" },
  { id: "zoomOut", label: "축소", icon: "zoomOut", command: "ZOOM -" },
  { id: "fit", label: "맞춤", icon: "fit", command: "ZOOM EXTENTS" },
];

const navItems = [
  { label: "프로젝트", icon: "folder" },
  { label: "도면 관리", icon: "plan", active: true },
  { label: "분석", icon: "spark" },
  { label: "최적화", icon: "optimize" },
  { label: "보고서", icon: "report" },
  { label: "설정", icon: "settings" },
];

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
  const [toast, setToast] = useState("대기 중 · 도면을 업로드해주세요");
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [activeTool, setActiveTool] = useState<ToolId>("pan");
  const [zoomLevel, setZoomLevel] = useState(100);
  const [panOffset, setPanOffset] = useState<PanOffset>({ x: 0, y: 0 });
  const [dragState, setDragState] = useState<DragState | null>(null);
  const [dialog, setDialog] = useState<DialogType>(null);
  const drawingCardRef = useRef<HTMLDivElement | null>(null);
  const cadViewerRef = useRef<CadFileViewerHandle | null>(null);   // AI 방 클릭→뷰어 이동
  const zoomLevelRef = useRef(zoomLevel);

  // ── 백엔드(FireVal/FireOpt 엔진) 실시간 연결: POST /api/analyze ──
  const [structure, setStructure] = useState<string>("");   // "" 미상 | "fireproof" | "other"
  const [occupancy, setOccupancy] = useState<string>("");   // "" 미상 | 용도 문자열
  const [mount, setMount] = useState<string>("");           // "" 미상 | "lt4"(<4m) | "ge4"(≥4m)
  const [analysis, setAnalysis] = useState<{
    drawingInfo?: {
      fileName?: string; layerCount?: number; entityCount?: number;
      fireLayers?: string[]; roomNames?: string[]; error?: string;
    } | null;
    roomJudgments?: Array<{
      room?: string; status?: string; area_m2?: number;
      detail?: string; reason?: string; basis?: string;
    }>;
    violations?: Array<{
      ruleId?: string; status?: string; severity?: string; description?: string;
    }>;
    judgmentSource?: string;
  }>({ drawingInfo: null, roomJudgments: [], violations: [] });

  // 소방 심볼 인식(HITL 명명): 업로드 시 /api/recognize 매니페스트, labels=사용자 지정 종류
  const [recognition, setRecognition] = useState<Recognition>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  // AI 방찾기(SAM): 방 레이어 없는 실무 도면용. rooms=[{name,area_m2,confidence}], loading 상태
  const [aiResult, setAiResult] = useState<{
    rooms?: Array<{ name?: string; area_m2?: number; confidence?: number }>;
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
    const seq = ++analyzeSeqRef.current;   // 이 요청의 순번 — 더 최신 요청이 생기면 결과 폐기
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    const options: RequestInit = { method: "POST", signal: controller.signal };
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
        if (seq !== analyzeSeqRef.current) return;   // 더 최신 분석이 진행됨 → 이 결과 폐기(레이스 방지)
        setAnalysis({
          drawingInfo: d.drawingInfo ?? null,
          roomJudgments: d.roomJudgments ?? [],
          violations: d.violations ?? [],
          judgmentSource: d.judgmentSource,
        });
        if (file) {
          const src = d.judgmentSource === "hitl" && (d.violations ?? []).length > 0 ? " (인식 M)" : "";
          setToast(d.drawingInfo?.error ? `추출 실패: ${d.drawingInfo.error}` : `${file.name} 분석 완료${src}`);
        }
      })
      .catch((err) => {
        if (seq !== analyzeSeqRef.current) return;
        setToast(err?.name === "AbortError"
          ? "도면 분석 시간 초과 (30초) — 백엔드 상태를 확인해주세요"
          : "백엔드 연결 실패 — 서버 상태를 확인해주세요");
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
        setRecognition({ classes: d.classes, legendTypes: d.legendTypes ?? [], facilityOptions: d.facilityOptions ?? [] });
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

  const openDialog = (nextDialog: Exclude<DialogType, null>) => {
    setDialog(nextDialog);
  };

  const handleTopAction = (action: Exclude<DialogType, null>) => {
    if (action === "save") {
      setToast("저장 기능은 준비 중입니다.");
      return;
    }

    openDialog(action);
  };

  const handleDrawingUpload = (file: File) => {
    setUploadedFile(file);
    setZoomLevel((current) => Math.max(current, uploadedDrawingInitialZoom));
    setPanOffset({ x: 0, y: 0 });
    setRecognition(null);
    setLabels({});
    setAiResult(null);
    setAnalysis({ drawingInfo: null, roomJudgments: [], violations: [] });   // 이전 파일 결과 잔류 방지
    setToast(`${file.name} 분석 중… (도면 정보 추출)`);
    runAnalysis(file, structure, occupancy, mount);
    runRecognize(file);        // 소방 심볼 인식 매니페스트(HITL 명명용)
  };

  // 사용자가 지정한 종류(labels)로 인식 M 기반 실판정
  const handleJudgeWithLabels = () => {
    if (uploadedFile) {
      setToast("지정한 소방 심볼 종류로 판정 중…");
      runAnalysis(uploadedFile, structure, occupancy, mount, labels);
    }
  };

  // AI 방찾기(SAM): 방 레이어 없는 실무 도면에서 방 경계를 AI로 추출 + 감지기 배정 + 판정. 느림.
  const runAiRooms = () => {
    if (!uploadedFile) {
      return;
    }
    const seq = ++aiSeqRef.current;   // 이 요청 순번 — 더 최신 요청/새 업로드면 결과 폐기
    const forFile = uploadedFile;
    setAiResult({ loading: true });
    setRoomDecisions({}); setManualAreas({});   // 새 실행 → 이전 확인/제외·수동면적 초기화
    setToast("AI로 방을 찾는 중… (모델 추론, 최대 1분 소요)");
    const form = new FormData();
    form.append("file", uploadedFile);
    if (structure) form.append("structure", structure);
    if (occupancy) form.append("occupancy", occupancy);
    if (mount) form.append("mount", mount);
    if (Object.keys(labels).length > 0) {
      form.append("labels", JSON.stringify(labels));   // 사용자 라벨한 연기/열 감지기로 정확 판정
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 120000);
    fetch("/api/rooms_ai", { method: "POST", body: form, signal: controller.signal })
      .then((res) => res.json())
      .then((d) => {
        if (seq !== aiSeqRef.current || forFile !== uploadedFile) return;   // 새 요청/새 파일 → 폐기(레이스 방지)
        setAiResult({ rooms: d.aiRooms ?? [], violations: d.violations ?? [],
                      note: d.note, available: d.available, loading: false });
        setToast(d.available === false ? "AI 방찾기 미설치 환경입니다"
                 : `AI 방찾기 완료 — 방 ${(d.aiRooms ?? []).length}개`);
      })
      .catch((err) => {
        if (seq !== aiSeqRef.current || forFile !== uploadedFile) return;
        setAiResult({ rooms: [], violations: [], loading: false });
        setToast(err?.name === "AbortError" ? "AI 방찾기 시간 초과(1분)" : "AI 방찾기 실패");
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
  // 안전 임계 입력이 바뀌면 기존 AI 방판정은 무효(옛 구조/층고로 계산됨) → 리셋(사용자 재실행).
  const invalidateAi = () => { aiSeqRef.current++; setAiResult(null); setRoomDecisions({}); setManualAreas({}); };

  // 건물 구조 변경 → 재판정(구조는 열감지기 기준면적에 영향 = 안전 임계 입력)
  const handleStructureChange = (value: string) => {
    setStructure(value);
    invalidateAi();
    if (uploadedFile) {
      runAnalysis(uploadedFile, value, occupancy, mount, labelsOrUndef);
    }
  };

  // 용도 변경 → 재판정(취침거실 연기의무·스프링클러 반경 등에 영향)
  const handleOccupancyChange = (value: string) => {
    setOccupancy(value);
    invalidateAi();
    if (uploadedFile) {
      runAnalysis(uploadedFile, structure, value, mount, labelsOrUndef);
    }
  };

  // 부착높이(층고) 변경 → 재판정(4m 경계로 감지면적이 갈림 = 안전 임계 입력)
  const handleMountChange = (value: string) => {
    setMount(value);
    invalidateAi();
    if (uploadedFile) {
      runAnalysis(uploadedFile, structure, occupancy, value, labelsOrUndef);
    }
  };

  const handleToolAction = (toolId: ToolId) => {
    const tool = toolDefinitions.find((item) => item.id === toolId);
    if (!tool) {
      return;
    }

    if (toolId === "zoomIn") {
      setZoomLevel((current) => clampZoom(current + zoomButtonStep));
    } else if (toolId === "zoomOut") {
      setZoomLevel((current) => clampZoom(current - zoomButtonStep));
    } else if (toolId === "fit") {
      setZoomLevel(100);
      setPanOffset({ x: 0, y: 0 });
    } else {
      setActiveTool(toolId);
    }

    setToast(`${tool.label} 도구가 활성화되었습니다.`);
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
    setActiveTool("pan");
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

  return (
    <main className="app-shell">
      <TopBar
        selectedFileName={uploadedFile?.name}
        onTopAction={handleTopAction}
      />
      <div className="workspace">
        <SideNav />
        <aside className="left-panel">
          <section className="panel-block">
            <div className="panel-heading">
              <h2>도면 관리</h2>
            </div>
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
          </section>

          {analysis.drawingInfo ? (
            <section className="panel-block compact">
              <div className="panel-heading inline">
                <h3>도면 정보</h3>
                <Icon name="layers" />
              </div>
              {analysis.drawingInfo.error ? (
                <p style={{ fontSize: 12.5, opacity: 0.7, padding: "4px" }}>{analysis.drawingInfo.error}</p>
              ) : (
                <div style={{ fontSize: 12.5, lineHeight: 1.7, padding: "2px 4px" }}>
                  <div>레이어 <b>{analysis.drawingInfo.layerCount}</b> · 요소 <b>{analysis.drawingInfo.entityCount?.toLocaleString()}</b></div>
                  <div style={{ marginTop: 6, opacity: 0.85 }}>소방 레이어: {(analysis.drawingInfo.fireLayers ?? []).slice(0, 6).join(", ") || "—"}</div>
                  <div style={{ marginTop: 6, opacity: 0.85 }}>실명: {(analysis.drawingInfo.roomNames ?? []).slice(0, 8).join(", ") || "—"}</div>
                </div>
              )}
            </section>
          ) : null}
        </aside>

        <section className="canvas-panel">
          <Toolbar activeTool={activeTool} onToolAction={handleToolAction} />
          <div
            ref={drawingCardRef}
            className={`drawing-card autocad-space tool-${activeTool} ${dragState ? "is-panning" : ""}`}
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
                panOffset={panOffset}
                onStatusChange={updateStatus}
              />
            ) : null}
          </div>
        </section>

        <aside className="right-panel">
          <section className="analysis-panel">
            <div className="list-header">
              <h3>법규 판정 (NFTC)</h3>
              <span style={{ fontSize: 12, opacity: 0.7 }}>
                {(analysis.violations ?? []).length > 0
                  ? (analysis.judgmentSource === "hitl" ? "실 판정 (인식 M)" : "실 판정")
                  : "요구 산정"}
              </span>
            </div>
            {(analysis.violations ?? []).length > 0 ? (
              <div style={{ marginBottom: 14 }}>
                <div style={{ display: "flex", gap: 10, marginBottom: 8, fontSize: 12.5 }}>
                  <span style={{ color: "#e05a5a", fontWeight: 600 }}>위반 {(analysis.violations ?? []).filter((v) => v.status === "violation").length}</span>
                  <span style={{ color: "#5ac08a", fontWeight: 600 }}>적합 {(analysis.violations ?? []).filter((v) => v.status === "compliant").length}</span>
                  <span style={{ color: "#d2a03c", fontWeight: 600 }}>확인필요 {(analysis.violations ?? []).filter((v) => v.status === "not_applicable").length}</span>
                  <span style={{ opacity: 0.6 }}>· 배치 vs 필요</span>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 220, overflowY: "auto" }}>
                  {(analysis.violations ?? []).slice().sort((a, b) => (a.status === "violation" ? 0 : a.status === "not_applicable" ? 1 : 2) - (b.status === "violation" ? 0 : b.status === "not_applicable" ? 1 : 2)).map((v, i) => {
                    const tone = v.status === "violation" ? { bg: "rgba(224,90,90,0.16)", bar: "#e05a5a", fg: "#e88", label: "위반" }
                      : v.status === "not_applicable" ? { bg: "rgba(210,160,60,0.13)", bar: "#d2a03c", fg: "#d9b060", label: "확인필요" }
                      : { bg: "rgba(90,192,138,0.12)", bar: "#5ac08a", fg: "#8d8", label: "적합" };
                    return (
                      <div key={`${v.ruleId}-${i}`} style={{ fontSize: 11.5, lineHeight: 1.4, padding: "5px 8px", borderRadius: 6,
                        background: tone.bg, borderLeft: `3px solid ${tone.bar}` }}>
                        <b style={{ color: tone.fg }}>{tone.label}</b> · {v.description}
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : null}
            {analysis.drawingInfo && !analysis.drawingInfo.error ? (
              <div style={{ marginBottom: 12 }}>
                <p style={{ fontSize: 12.5, margin: "0 0 8px", lineHeight: 1.6 }}>
                  업로드 도면에서 추출한 <b>실제 사실</b>
                </p>
                <div style={{ fontSize: 12, opacity: 0.85, margin: "0 0 5px" }}>
                  방 {analysis.drawingInfo.roomNames?.length ?? 0}개
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginBottom: 10 }}>
                  {(analysis.drawingInfo.roomNames ?? []).map((r) => (
                    <span key={r} style={{ fontSize: 11.5, padding: "3px 8px", borderRadius: 6, background: "rgba(120,140,170,0.18)" }}>{r}</span>
                  ))}
                </div>
                <div style={{ fontSize: 12, opacity: 0.85, margin: "0 0 5px" }}>
                  소방 설비 레이어 {analysis.drawingInfo.fireLayers?.length ?? 0}개
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                  {(analysis.drawingInfo.fireLayers ?? []).slice(0, 8).map((l) => (
                    <span key={l} style={{ fontSize: 11, padding: "3px 8px", borderRadius: 6, background: "rgba(210,80,80,0.16)", color: "#e79b9b" }}>{l}</span>
                  ))}
                </div>
              </div>
            ) : (
              <p style={{ fontSize: 12.5, margin: "0 0 12px", lineHeight: 1.6, opacity: 0.7 }}>
                도면을 업로드하면 이 도면의 방·소방 설비를 추출합니다.
              </p>
            )}
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
                  <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 6 }}>범례 감지: {recognition.legendTypes.slice(0, 5).join(", ")}</div>
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
                  <span style={{ fontSize: 12.5, fontWeight: 600 }}>방별 판정</span>
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
                {(analysis.roomJudgments ?? []).length === 0 ? (
                  <p style={{ fontSize: 11.5, opacity: 0.6 }}>추출된 방 판정 없음(벽 레이어 인식 한계 가능).</p>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 5, maxHeight: 260, overflowY: "auto" }}>
                    {(analysis.roomJudgments ?? []).map((j, i) => (
                      <div key={`${j.room}-${i}`} style={{ fontSize: 11.5, lineHeight: 1.45, padding: "6px 8px", borderRadius: 6,
                        background: j.status === "determined" ? "rgba(210,160,60,0.13)" : "rgba(120,140,170,0.1)",
                        borderLeft: `3px solid ${j.status === "determined" ? "#d2a03c" : "#8fa4c8"}` }}>
                        <b>{j.room || "—"}</b>{j.area_m2 ? ` · ${j.area_m2}㎡` : ""}
                        {j.status === "determined" ? <span style={{ opacity: 0.65 }}> · 요구</span> : null}
                        {j.status === "needs_review" ? <span style={{ opacity: 0.65 }}> · 확인 필요</span> : null}
                        <div style={{ opacity: 0.85, marginTop: 2 }}>{j.detail || j.reason}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : null}
            {analysis.drawingInfo && !analysis.drawingInfo.error && (analysis.violations ?? []).length === 0 ? (
              <div style={{ marginBottom: 12 }}>
                <button onClick={runAiRooms} disabled={aiResult?.loading}
                  style={{ width: "100%", fontSize: 12.5, padding: "9px", borderRadius: 8, cursor: aiResult?.loading ? "wait" : "pointer",
                    background: "rgba(120,90,200,0.25)", color: "#cbb8f0", border: "1px solid rgba(150,120,220,0.45)", fontWeight: 600 }}>
                  {aiResult?.loading ? "🤖 방 찾는 중…" : "🤖 AI로 방 찾기 (실험)"}
                </button>
                <p style={{ fontSize: 10.5, opacity: 0.55, margin: "5px 0 0", lineHeight: 1.5 }}>
                  방 레이어 없는 실무 도면에서 벽으로 방 면적을 추출(기하)해 판정합니다. 벽 안 닫힌 방은 <b>경계 확인 필요</b> — 모든 방 사람 확인 후 최종.
                </p>
              </div>
            ) : null}
            {aiResult && !aiResult.loading && (aiResult.rooms ?? []).length > 0 ? (
              (() => {
                const viols = aiResult.violations ?? [];
                const rooms = aiResult.rooms ?? [];
                const roomArea: Record<string, number | undefined> = {};
                rooms.forEach((r) => { if (r.name && !(r.name in roomArea)) roomArea[r.name] = r.area_m2; });
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
                let resolved = 0, excluded = 0, pending = 0;
                viols.forEach((v, i) => {
                  const k = keyOf(v, i);
                  if (roomDecisions[k] === "excluded") { excluded++; return; }
                  if (isNB(v) ? validArea(k) : roomDecisions[k] === "confirmed") resolved++; else pending++;
                });
                const nViol = viols.filter((v, i) => !isNB(v) && roomDecisions[keyOf(v, i)] === "confirmed" && v.status === "violation").length;
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
                    const resolvedR = confirmed || areaOk;
                    const area = roomArea[v.roomName || ""];
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
                          <div style={{ marginTop: 3, opacity: 0.8 }}>벽이 안 닫힘(문틈/병합) — 면적 직접 입력하거나 제외</div>
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
                    확정 <b>{resolved}</b>개(사람 확인) · 위반 <b>{nViol}</b>{pending > 0 ? <span style={{ opacity: 0.7 }}> · 대기 {pending}개</span> : null}
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
        </aside>
      </div>

      <footer className="status-bar">
        <span className="status-dot" />
        <strong>상태: {toast}</strong>
        {analysis.drawingInfo && !analysis.drawingInfo.error ? (
          <span>추출: 방 <b>{analysis.drawingInfo.roomNames?.length ?? 0}</b>개 · 소방레이어 <b>{analysis.drawingInfo.fireLayers?.length ?? 0}</b></span>
        ) : null}
        <em><Icon name="shield" /> 한국 화재안전기술기준 (NFTC) · FireVal 엔진</em>
      </footer>
      <ActionDialog
        dialog={dialog}
        fileName={uploadedFile?.name ?? "선택된 도면 없음"}
        drawingInfo={analysis.drawingInfo ?? null}
        onClose={() => setDialog(null)}
      />
    </main>
  );
}

function TopBar({
  selectedFileName,
  onTopAction,
}: {
  selectedFileName?: string;
  onTopAction: (action: Exclude<DialogType, null>) => void;
}) {
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
      <nav className="top-actions">
        <button onClick={() => onTopAction("save")}>저장</button>
        <button onClick={() => onTopAction("report")}>보고서</button>
        <button className="export" onClick={() => onTopAction("export")}>내보내기</button>
        <button className="round" aria-label="알림" onClick={() => onTopAction("notifications")}><Icon name="bell" /></button>
      </nav>
    </header>
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

function ActionDialog({
  dialog,
  fileName,
  drawingInfo,
  onClose,
}: {
  dialog: DialogType;
  fileName: string;
  drawingInfo: {
    fileName?: string; layerCount?: number; entityCount?: number;
    fireLayers?: string[]; roomNames?: string[]; error?: string;
  } | null;
  onClose: () => void;
}) {
  const rooms = drawingInfo?.roomNames ?? [];
  const fireLayers = drawingInfo?.fireLayers ?? [];
  const hasFacts = Boolean(drawingInfo && !drawingInfo.error);

  // 도면에서 자동 추출한 '사실'만 담은 마크다운(가짜 판정 없음).
  const factsMarkdown = [
    `# 소방 도면 사실 요약 — ${drawingInfo?.fileName ?? fileName}`,
    ``,
    hasFacts ? `- 레이어: ${drawingInfo?.layerCount ?? "-"}개` : `- (업로드된 도면 없음 또는 파싱 실패)`,
    ...(hasFacts ? [`- 도형 요소: ${(drawingInfo?.entityCount ?? 0).toLocaleString()}개`] : []),
    ``,
    `## 추출된 방 (${rooms.length})`,
    ...rooms.map((r) => `- ${r}`),
    ``,
    `## 소방 설비 레이어 (${fireLayers.length})`,
    ...fireLayers.map((l) => `- ${l}`),
    ``,
    `---`,
    `※ NFTC 적정성 판정(방별 필요 감지기 수)은 설비 심볼 인식·방 면적 자동추출 연결 후 제공됩니다.`,
    `   본 문서는 도면에서 자동 추출한 '사실'만 담습니다.`,
  ].join("\n");

  const downloadFacts = () => {
    const blob = new Blob([factsMarkdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "소방도면_사실요약.md";
    anchor.click();
    URL.revokeObjectURL(url);
  };

  if (!dialog || dialog === "save") {
    return null;
  }

  const title = {
    report: "도면 사실 요약",
    export: "내보내기",
    notifications: "알림 센터",
  }[dialog];

  return (
    <div className="action-dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="action-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span>FireOpt</span>
            <h2>{title}</h2>
          </div>
          <button aria-label="닫기" onClick={onClose}>
            <Icon name="close" />
          </button>
        </header>

        {dialog === "report" ? (
          <div className="dialog-content">
            <div className="report-summary">
              <article>
                <span>도면</span>
                <strong>{fileName}</strong>
              </article>
              <article>
                <span>추출된 방</span>
                <strong>{rooms.length}개</strong>
              </article>
              <article>
                <span>소방 레이어</span>
                <strong>{fireLayers.length}개</strong>
              </article>
            </div>
            <div className="report-sheet">
              <h3>소방 도면 사실 요약</h3>
              <pre style={{ whiteSpace: "pre-wrap", fontSize: 12, lineHeight: 1.6, margin: 0, maxHeight: 300, overflow: "auto", fontFamily: "inherit" }}>
                {factsMarkdown}
              </pre>
              <p style={{ fontSize: 11.5, opacity: 0.6, marginTop: 8 }}>※ 도면에서 자동 추출한 사실 · NFTC 적정성 판정은 인식 파이프라인 연결 후</p>
            </div>
            <button className="dialog-primary" onClick={downloadFacts}>사실 요약 다운로드 (.md)</button>
          </div>
        ) : null}

        {dialog === "export" ? (
          <div className="dialog-content">
            <div className="export-panel">
              <span>내보내기 가능</span>
              <strong>소방 도면 사실 요약 (.md)</strong>
              <p>도면에서 추출한 방·소방 설비 레이어 목록을 파일로 저장합니다. (도면 DWG/DXF 주석 내보내기, NFTC 판정서는 준비 중)</p>
            </div>
            <button className="dialog-primary" onClick={downloadFacts}>사실 요약 다운로드 (.md)</button>
          </div>
        ) : null}

        {dialog === "notifications" ? (
          <div className="dialog-content">
            <div className="notification-list">
              <article>
                <strong>도면 렌더링</strong>
                <p>업로드된 CAD 파일은 브라우저에서 미리보기 렌더링됩니다.</p>
              </article>
              <article>
                <strong>사실 추출</strong>
                <p>백엔드가 도면에서 레이어·소방 설비 레이어·실명을 추출합니다.</p>
              </article>
              <article>
                <strong>NFTC 판정 (준비 중)</strong>
                <p>방별 적정성 판정은 설비 심볼 인식·방 면적 자동추출 연결 후 제공됩니다.</p>
              </article>
            </div>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function SideNav() {
  return (
    <nav className="sidenav" aria-label="주요 메뉴">
      {navItems.map((item) => (
        <button
          key={item.label}
          className={item.active ? "active" : ""}
          disabled={!item.active}
          title={item.active ? undefined : "준비 중"}
        >
          <Icon name={item.icon} />
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  );
}

function Toolbar({
  activeTool,
  onToolAction,
}: {
  activeTool: ToolId;
  onToolAction: (toolId: ToolId) => void;
}) {
  return (
    <div className="toolbar">
      {toolDefinitions.map((tool) => (
        <button
          key={tool.id}
          className={activeTool === tool.id ? "active" : ""}
          onClick={() => onToolAction(tool.id)}
        >
          <Icon name={tool.icon} />
          <span>{tool.label}</span>
        </button>
      ))}
    </div>
  );
}

function Icon({ name }: { name: string }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" } as const;
  switch (name) {
    case "flame":
      return <svg viewBox="0 0 24 24" {...common}><path d="M3 10h18M5 10v9M9 10v9M15 10v9M19 10v9M4 21h16M12 3 3 8h18Z" /></svg>;
    case "plan":
      return <svg viewBox="0 0 24 24" {...common}><rect x="4" y="4" width="16" height="16" rx="2" /><path d="M9 4v6h6V4M9 20v-5H4M15 20v-7h5" /></svg>;
    case "folder":
      return <svg viewBox="0 0 24 24" {...common}><path d="M4 6h6l2 2h8v10a2 2 0 0 1-2 2H4Z" /><path d="M4 6v12" /></svg>;
    case "spark":
      return <svg viewBox="0 0 24 24" {...common}><path d="M12 3l2.1 6 6 2.1-6 2.1L12 21l-2.1-7.8-6-2.1 6-2.1Z" /></svg>;
    case "optimize":
      return <svg viewBox="0 0 24 24" {...common}><path d="M4 17l5-5 4 4 7-9" /><path d="M15 7h5v5" /></svg>;
    case "report":
    case "document":
      return <svg viewBox="0 0 24 24" {...common}><path d="M6 3h8l4 4v14H6Z" /><path d="M14 3v5h4M9 13h6M9 17h6" /></svg>;
    case "settings":
      return <svg viewBox="0 0 24 24" {...common}><circle cx="12" cy="12" r="3" /><path d="M19 12a7 7 0 0 0-.1-1l2-1.6-2-3.4-2.4 1a8 8 0 0 0-1.8-1L12 3H8l-.7 3a8 8 0 0 0-1.8 1l-2.4-1-2 3.4L3.1 11a7 7 0 0 0 0 2L1.1 14.6l2 3.4 2.4-1a8 8 0 0 0 1.8 1L8 21h4l.7-3a8 8 0 0 0 1.8-1l2.4 1 2-3.4-2-1.6a7 7 0 0 0 .1-1Z" /></svg>;
    case "eye":
      return <svg viewBox="0 0 24 24" {...common}><path d="M2 12s4-6 10-6 10 6 10 6-4 6-10 6S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></svg>;
    case "layers":
      return <svg viewBox="0 0 24 24" {...common}><path d="M12 3 3 8l9 5 9-5Z" /><path d="m3 12 9 5 9-5M3 16l9 5 9-5" /></svg>;
    case "chevron":
      return <svg viewBox="0 0 24 24" {...common}><path d="m7 9 5 5 5-5" /></svg>;
    case "bell":
      return <svg viewBox="0 0 24 24" {...common}><path d="M18 9a6 6 0 0 0-12 0c0 7-2 7-2 9h16c0-2-2-2-2-9" /><path d="M10 21h4" /></svg>;
    case "cursor":
      return <svg viewBox="0 0 24 24" {...common}><path d="M5 3l12 9-6 1-3 6Z" /></svg>;
    case "move":
      return <svg viewBox="0 0 24 24" {...common}><path d="M12 3v18M3 12h18M7 7l-4 5 4 5M17 7l4 5-4 5M7 7l5-4 5 4M7 17l5 4 5-4" /></svg>;
    case "rotate":
      return <svg viewBox="0 0 24 24" {...common}><path d="M20 12a8 8 0 1 1-2.3-5.7" /><path d="M20 4v6h-6" /></svg>;
    case "ruler":
      return <svg viewBox="0 0 24 24" {...common}><path d="m4 17 13-13 3 3L7 20Z" /><path d="m14 7 3 3M11 10l2 2M8 13l3 3" /></svg>;
    case "zoomIn":
      return <svg viewBox="0 0 24 24" {...common}><circle cx="10" cy="10" r="6" /><path d="m15 15 5 5M10 7v6M7 10h6" /></svg>;
    case "zoomOut":
      return <svg viewBox="0 0 24 24" {...common}><circle cx="10" cy="10" r="6" /><path d="m15 15 5 5M7 10h6" /></svg>;
    case "fit":
      return <svg viewBox="0 0 24 24" {...common}><path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5" /></svg>;
    case "close":
      return <svg viewBox="0 0 24 24" {...common}><path d="M6 6l12 12M18 6 6 18" /></svg>;
    case "arrow":
      return <svg viewBox="0 0 24 24" {...common}><path d="M5 12h14M13 6l6 6-6 6" /></svg>;
    case "shield":
      return <svg viewBox="0 0 24 24" {...common}><path d="M12 3 5 6v5c0 5 3 8 7 10 4-2 7-5 7-10V6Z" /><path d="m9 12 2 2 4-5" /></svg>;
    default:
      return <svg viewBox="0 0 24 24" {...common}><circle cx="12" cy="12" r="8" /></svg>;
  }
}
