import { useCallback, useEffect, useRef, useState, type PointerEvent } from "react";
import { CadFileViewer } from "./components/CadFileViewer";
import type { LayerId } from "./types";

// CAD 뷰어는 도면 자체의 레이어 가시성으로 렌더 — 앱 레이어 필터는 미연결(PART B 예정)
const NO_VISIBLE_LAYERS = new Set<LayerId>();

type ToolId = "pan" | "zoomIn" | "zoomOut" | "fit";
type DialogType = "save" | "report" | "export" | "notifications" | null;
type PanOffset = { x: number; y: number };
type DragState = PanOffset & { pointerId: number; startX: number; startY: number };
type DrawingInfo = {
  fileName?: string;
  layerCount?: number;
  entityCount?: number;
  fireLayers?: string[];
  roomNames?: string[];
  layerNames?: string[];
  error?: string;
  source?: "backend" | "viewer";
};

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

export function App() {
  const [toast, setToast] = useState("대기 중 · 도면을 업로드해주세요");
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [activeTool, setActiveTool] = useState<ToolId>("pan");
  const [zoomLevel, setZoomLevel] = useState(100);
  const [panOffset, setPanOffset] = useState<PanOffset>({ x: 0, y: 0 });
  const [dragState, setDragState] = useState<DragState | null>(null);
  const [dialog, setDialog] = useState<DialogType>(null);
  const drawingCardRef = useRef<HTMLDivElement | null>(null);
  const zoomLevelRef = useRef(zoomLevel);

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
  const [viewerDrawingInfo, setViewerDrawingInfo] = useState<DrawingInfo | null>(null);
  const effectiveDrawingInfo = getEffectiveDrawingInfo(analysis.drawingInfo ?? null, viewerDrawingInfo);
  const analysisError = analysis.drawingInfo?.error;

  // 업로드 파일 + 구조/용도를 FormData로 전송 → 백엔드가 사실 + 방별요구 + (깨끗규격이면) 실 pass/fail 반환
  const runAnalysis = useCallback((file?: File, structureVal?: string, occupancyVal?: string, mountVal?: string) => {
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
        setAnalysis({
          drawingInfo: d.drawingInfo ?? null,
          roomJudgments: d.roomJudgments ?? [],
          violations: d.violations ?? [],
        });
        if (file) {
          setToast(d.drawingInfo?.error ? `추출 실패: ${d.drawingInfo.error}` : `${file.name} 분석 완료`);
        }
      })
      .catch((err) => {
        setToast(err?.name === "AbortError"
          ? "도면 분석 시간 초과 (30초) — 백엔드 상태를 확인해주세요"
          : "백엔드 연결 실패 — 서버 상태를 확인해주세요");
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
    if (action === "save") {
      setToast("저장 기능은 준비 중입니다.");
      return;
    }

    openDialog(action);
  };

  const handleDrawingUpload = (file: File) => {
    setUploadedFile(file);
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

  return (
    <main className="app-shell">
      <TopBar
        selectedFileName={uploadedFile?.name}
        onTopAction={handleTopAction}
      />
      <div className="workspace">
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
                file={uploadedFile}
                visibleLayerIds={NO_VISIBLE_LAYERS}
                opacity={100}
                zoomLevel={zoomLevel}
                panOffset={panOffset}
                onStatusChange={updateStatus}
                onDrawingInfoChange={setViewerDrawingInfo}
              />
            ) : null}
          </div>
        </section>

        <aside className="right-panel">
          <section className="analysis-panel">
            <div className="list-header">
              <h3>법규 판정 (NFTC)</h3>
              <span style={{ fontSize: 12, opacity: 0.7 }}>
                {(analysis.violations ?? []).length > 0 ? "실 판정" : "요구 산정"}
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
            {effectiveDrawingInfo && !effectiveDrawingInfo.error ? (
              <div style={{ marginBottom: 12 }}>
                <p style={{ fontSize: 12.5, margin: "0 0 8px", lineHeight: 1.6 }}>
                  업로드 도면에서 확인한 <b>실제 사실</b>
                </p>
                <div style={{ fontSize: 12, opacity: 0.85, margin: "0 0 5px" }}>
                  방 {effectiveDrawingInfo.roomNames?.length ?? 0}개
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginBottom: 10 }}>
                  {(effectiveDrawingInfo.roomNames ?? []).map((r) => (
                    <span key={r} style={{ fontSize: 11.5, padding: "3px 8px", borderRadius: 6, background: "rgba(120,140,170,0.18)" }}>{r}</span>
                  ))}
                </div>
                <div style={{ fontSize: 12, opacity: 0.85, margin: "0 0 5px" }}>
                  소방 설비 레이어 {effectiveDrawingInfo.fireLayers?.length ?? 0}개
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                  {(effectiveDrawingInfo.fireLayers ?? []).slice(0, 8).map((l) => (
                    <span key={l} style={{ fontSize: 11, padding: "3px 8px", borderRadius: 6, background: "rgba(210,80,80,0.16)", color: "#e79b9b" }}>{l}</span>
                  ))}
                </div>
                {effectiveDrawingInfo.source === "viewer" ? (
                  <p style={{ fontSize: 11.5, opacity: 0.65, lineHeight: 1.5, margin: "10px 0 0" }}>
                    브라우저 렌더러 기준: 레이어 <b>{effectiveDrawingInfo.layerCount ?? 0}</b>개 · 요소 <b>{effectiveDrawingInfo.entityCount?.toLocaleString() ?? 0}</b>개
                  </p>
                ) : null}
                {analysisError ? (
                  <p style={{ fontSize: 11.5, opacity: 0.65, lineHeight: 1.5, margin: "8px 0 0" }}>
                    정밀 분석 보류: {analysisError}
                  </p>
                ) : null}
              </div>
            ) : (
              <p style={{ fontSize: 12.5, margin: "0 0 12px", lineHeight: 1.6, opacity: 0.7 }}>
                도면을 업로드하면 이 도면의 방·소방 설비를 추출합니다.
              </p>
            )}
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
            <p style={{ fontSize: 11.5, margin: 0, lineHeight: 1.6, padding: "10px 12px", borderRadius: 8, background: "rgba(90,120,180,0.12)", color: "#9fb4d8" }}>
              방 면적은 flood-fill로 추출(신뢰 방만). 실 pass/fail 판정엔 <b>구조·층고</b>가 필요(감지면적 기준을 가름) — 미상이면 판정 보류(안전). 배치 확정은 감지기 인식 연결 후.
            </p>
          </section>
        </aside>
      </div>

      <footer className="status-bar">
        <span className="status-dot" />
        <strong>상태: {toast}</strong>
        {effectiveDrawingInfo && !effectiveDrawingInfo.error ? (
          <span>확인: 방 <b>{effectiveDrawingInfo.roomNames?.length ?? 0}</b>개 · 소방레이어 <b>{effectiveDrawingInfo.fireLayers?.length ?? 0}</b></span>
        ) : null}
        <em><Icon name="shield" /> 한국 화재안전기술기준 (NFTC) · FireVal 엔진</em>
      </footer>
      <ActionDialog
        dialog={dialog}
        fileName={uploadedFile?.name ?? "선택된 도면 없음"}
        drawingInfo={effectiveDrawingInfo}
        analysisError={analysisError}
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

function getEffectiveDrawingInfo(backendInfo: DrawingInfo | null, viewerInfo: DrawingInfo | null) {
  if (backendInfo && !backendInfo.error) {
    return { ...backendInfo, source: backendInfo.source ?? "backend" };
  }

  return viewerInfo;
}

function ActionDialog({
  dialog,
  fileName,
  drawingInfo,
  analysisError,
  onClose,
}: {
  dialog: DialogType;
  fileName: string;
  drawingInfo: DrawingInfo | null;
  analysisError?: string;
  onClose: () => void;
}) {
  const rooms = drawingInfo?.roomNames ?? [];
  const fireLayers = drawingInfo?.fireLayers ?? [];
  const layerNames = drawingInfo?.layerNames ?? [];
  const hasFacts = Boolean(drawingInfo && !drawingInfo.error);
  const sourceLabel = drawingInfo?.source === "viewer" ? "브라우저 CAD 렌더러" : "백엔드 FireVal 분석";

  // 도면에서 자동 추출한 '사실'만 담은 마크다운(가짜 판정 없음).
  const factsMarkdown = [
    `# 소방 도면 사실 요약 — ${drawingInfo?.fileName ?? fileName}`,
    ``,
    hasFacts ? `- 기준: ${sourceLabel}` : ``,
    hasFacts ? `- 레이어: ${drawingInfo?.layerCount ?? "-"}개` : `- (업로드된 도면 없음 또는 파싱 실패)`,
    ...(hasFacts ? [`- 도형 요소: ${(drawingInfo?.entityCount ?? 0).toLocaleString()}개`] : []),
    ...(analysisError ? [`- 정밀 분석 상태: ${analysisError}`] : []),
    ``,
    `## 추출된 방 (${rooms.length})`,
    ...(rooms.length > 0 ? rooms.map((r) => `- ${r}`) : [`- 정밀 분석 연결 후 표시`]),
    ``,
    `## 소방 설비 레이어 (${fireLayers.length})`,
    ...(fireLayers.length > 0 ? fireLayers.map((l) => `- ${l}`) : [`- 정밀 분석 연결 후 표시`]),
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
                <span>레이어</span>
                <strong>{hasFacts ? `${drawingInfo?.layerCount ?? 0}개` : "-"}</strong>
              </article>
              <article>
                <span>도형 요소</span>
                <strong>{hasFacts ? `${(drawingInfo?.entityCount ?? 0).toLocaleString()}개` : "-"}</strong>
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
