import { useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { CadFileViewer } from "./components/CadFileViewer";
import type { LayerId } from "./types";

// CAD 뷰어는 도면 자체의 레이어 가시성으로 렌더 — 앱 레이어 필터는 미연결(PART B 예정)
const NO_VISIBLE_LAYERS = new Set<LayerId>();

type ToolId = "pan" | "zoomIn" | "zoomOut" | "fit";
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
const zoomButtonStep = 25;
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

const toolDefinitions: Array<{ id: ToolId; label: string; icon: string; command: string }> = [
  { id: "pan", label: "이동", icon: "move", command: "PAN" },
  { id: "zoomIn", label: "확대", icon: "zoomIn", command: "ZOOM +" },
  { id: "zoomOut", label: "축소", icon: "zoomOut", command: "ZOOM -" },
  { id: "fit", label: "맞춤", icon: "fit", command: "ZOOM EXTENTS" },
];

export function App() {
  const viewportFrame = useViewportFrame();
  const [toast, setToast] = useState("대기 중 · 도면을 업로드해주세요");
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [activeTool, setActiveTool] = useState<ToolId>("pan");
  const [zoomLevel, setZoomLevel] = useState(100);
  const [panOffset, setPanOffset] = useState<PanOffset>({ x: 0, y: 0 });
  const [dragState, setDragState] = useState<DragState | null>(null);
  const [panelWidths, setPanelWidths] = useState<PanelWidths>(defaultPanelWidths);
  const [columnResize, setColumnResize] = useState<ColumnResizeState | null>(null);
  const [dialog, setDialog] = useState<DialogType>(null);
  const workspaceRef = useRef<HTMLDivElement | null>(null);
  const drawingCardRef = useRef<HTMLDivElement | null>(null);
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
  const shouldShowReport = Boolean(uploadedFile && !analysisPendingMessage && (analysis.drawingInfo || effectiveDrawingInfo));

  // 업로드 파일 + 구조/용도를 FormData로 전송 → 백엔드가 사실 + 방별요구 + (깨끗규격이면) 실 pass/fail 반환
  const runAnalysis = useCallback((file?: File, structureVal?: string, occupancyVal?: string, mountVal?: string) => {
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
    openDialog(action);
  };

  const handleDrawingUpload = (file: File) => {
    setUploadedFile(file);
    setAnalysis({ drawingInfo: null, roomJudgments: [], violations: [] });
    setViewerDrawingInfo(null);
    setZoomLevel((current) => Math.max(current, uploadedDrawingInitialZoom));
    setPanOffset({ x: 0, y: 0 });
    setToast(`${file.name} 분석 중… (도면 정보 추출)`);
    runAnalysis(file, structure, occupancy, mount);
  };

  // 건물 구조 변경 → 재판정(구조는 열감지기 기준면적에 영향 = 안전 임계 입력)
  const handleStructureChange = (value: string) => {
    setStructure(value);
    if (uploadedFile) {
      runAnalysis(uploadedFile, value, occupancy, mount);
    }
  };

  // 용도 변경 → 재판정(취침거실 연기의무·스프링클러 반경 등에 영향)
  const handleOccupancyChange = (value: string) => {
    setOccupancy(value);
    if (uploadedFile) {
      runAnalysis(uploadedFile, structure, value, mount);
    }
  };

  // 부착높이(층고) 변경 → 재판정(4m 경계로 감지면적이 갈림 = 안전 임계 입력)
  const handleMountChange = (value: string) => {
    setMount(value);
    if (uploadedFile) {
      runAnalysis(uploadedFile, structure, occupancy, value);
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
            className={`drawing-card autocad-space tool-${activeTool} ${dragState ? "is-panning" : ""}`}
            onPointerDown={handleDrawingPointerDown}
            onPointerMove={handleDrawingPointerMove}
            onPointerUp={stopDrawingPan}
            onPointerCancel={stopDrawingPan}
          >
            {uploadedFile ? (
              <CadFileViewer
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
                handleToolAction("fit");
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
          {!shouldShowReport ? (
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
              {analysis.drawingInfo && !analysis.drawingInfo.error ? (
                <div className="room-judgment-block">
                  <div className="room-controls">
                    <span>방별 판정</span>
                    <select
                      className="compact-select"
                      value={structure}
                      onChange={(event) => handleStructureChange(event.target.value)}
                    >
                      <option value="">건물구조 미상</option>
                      <option value="fireproof">내화구조</option>
                      <option value="other">기타구조</option>
                    </select>
                    <select
                      className="compact-select"
                      value={occupancy}
                      onChange={(event) => handleOccupancyChange(event.target.value)}
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
                      className="compact-select"
                      value={mount}
                      onChange={(event) => handleMountChange(event.target.value)}
                    >
                      <option value="">층고 미상</option>
                      <option value="lt4">천장 4m 미만</option>
                      <option value="ge4">천장 4m 이상</option>
                    </select>
                  </div>
                  {(analysis.roomJudgments ?? []).length === 0 ? (
                    <p className="analysis-empty">추출된 방 판정 없음(벽 레이어 인식 한계 가능).</p>
                  ) : (
                    <div className="room-judgment-list">
                      {(analysis.roomJudgments ?? []).map((j, i) => (
                        <div key={`${j.room}-${i}`} className={`room-judgment-card ${j.status === "determined" ? "determined" : "review"}`}>
                          <b>{j.room || "—"}</b>{j.area_m2 ? ` · ${j.area_m2}㎡` : ""}
                          {j.status === "determined" ? <span> · 요구</span> : null}
                          {j.status === "needs_review" ? <span> · 확인 필요</span> : null}
                          <div>{j.detail || j.reason}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ) : null}
              <p className="analysis-note">
                방 면적은 flood-fill로 추출(신뢰 방만). 실 pass/fail 판정엔 <b>구조·층고</b>가 필요(감지면적 기준을 가름) — 미상이면 판정 보류(안전). 배치 확정은 감지기 인식 연결 후.
              </p>
            </section>
          ) : null}
          {shouldShowReport ? (
            <ReportPanel
              fileName={uploadedFile?.name ?? "선택된 도면 없음"}
              drawingInfo={visibleDrawingInfo}
              analysisError={analysisError}
              analysisRecovered={analysisRecovered}
            />
          ) : null}
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
      <ActionDialog
        dialog={dialog}
        fileName={uploadedFile?.name ?? "선택된 도면 없음"}
        drawingInfo={visibleDrawingInfo}
        analysisError={analysisError}
        analysisRecovered={analysisRecovered}
        onClose={() => setDialog(null)}
      />
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
        <button
          className="round"
          onClick={() => onTopAction("export")}
          aria-label="공유"
          title="공유"
        >
          <Icon name="share" />
        </button>
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

function ActionDialog({
  dialog,
  fileName,
  drawingInfo,
  analysisError,
  analysisRecovered,
  onClose,
}: {
  dialog: DialogType;
  fileName: string;
  drawingInfo: DrawingInfo | null;
  analysisError?: string;
  analysisRecovered?: boolean;
  onClose: () => void;
}) {
  const factsMarkdown = getFactsMarkdown(fileName, drawingInfo, analysisError, analysisRecovered);

  if (!dialog) {
    return null;
  }

  const title = {
    export: "공유",
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

        {dialog === "export" ? (
          <div className="dialog-content">
            <div className="export-panel">
              <span>내보내기 가능</span>
              <strong>소방 도면 사실 요약 (.md)</strong>
              <p>도면에서 추출한 방·소방 설비 레이어 목록을 파일로 저장합니다. (도면 DWG/DXF 주석 내보내기, NFTC 판정서는 준비 중)</p>
            </div>
            <button className="dialog-primary" onClick={() => downloadFactsMarkdown(factsMarkdown)}>사실 요약 다운로드 (.md)</button>
          </div>
        ) : null}

      </section>
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
