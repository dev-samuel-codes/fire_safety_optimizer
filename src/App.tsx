import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { CadFileViewer } from "./components/CadFileViewer";
import {
  conflicts,
  initialLayers,
  recommendations,
} from "./data/projectData";
import type { LayerId, Severity } from "./types";

type ConflictFilter = "전체" | Severity;
type ToolId = "select" | "pan" | "rotate" | "measure" | "zoomIn" | "zoomOut" | "fit" | "layers";
type DialogType = "save" | "report" | "export" | "notifications" | null;
type PanOffset = { x: number; y: number };
type DragState = PanOffset & { pointerId: number; startX: number; startY: number };

const zoomMin = 25;
const zoomButtonStep = 25;
const zoomWheelStep = 10;
const uploadedDrawingInitialZoom = 150;

const toolDefinitions: Array<{ id: ToolId; label: string; icon: string; command: string }> = [
  { id: "select", label: "선택", icon: "cursor", command: "SELECT" },
  { id: "pan", label: "이동", icon: "move", command: "PAN" },
  { id: "rotate", label: "회전", icon: "rotate", command: "ROTATE" },
  { id: "measure", label: "측정", icon: "ruler", command: "DIST" },
  { id: "zoomIn", label: "확대", icon: "zoomIn", command: "ZOOM +" },
  { id: "zoomOut", label: "축소", icon: "zoomOut", command: "ZOOM -" },
  { id: "fit", label: "맞춤", icon: "fit", command: "ZOOM EXTENTS" },
  { id: "layers", label: "층 선택", icon: "layers", command: "LAYER" },
];

const navItems = [
  { label: "프로젝트", icon: "folder" },
  { label: "도면 관리", icon: "plan", active: true },
  { label: "분석", icon: "spark" },
  { label: "최적화", icon: "optimize" },
  { label: "보고서", icon: "report" },
  { label: "설정", icon: "settings" },
];

export function App() {
  const [layers, setLayers] = useState(initialLayers);
  const [opacity, setOpacity] = useState(68);
  const [filter, setFilter] = useState<ConflictFilter>("전체");
  const [tab, setTab] = useState<"result" | "collisions">("result");
  const [applied, setApplied] = useState(false);
  const [toast, setToast] = useState("분석 완료");
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [activeTool, setActiveTool] = useState<ToolId>("select");
  const [zoomLevel, setZoomLevel] = useState(100);
  const [panOffset, setPanOffset] = useState<PanOffset>({ x: 0, y: 0 });
  const [dragState, setDragState] = useState<DragState | null>(null);
  const [layoutTab, setLayoutTab] = useState("Model");
  const [commandLine, setCommandLine] = useState("명령: SELECT");
  const [commandInput, setCommandInput] = useState("");
  const [dialog, setDialog] = useState<DialogType>(null);
  const [draftSaved, setDraftSaved] = useState(false);
  const [cadToggles, setCadToggles] = useState({
    grid: true,
    snap: true,
    ortho: false,
    osnap: true,
  });
  const drawingCardRef = useRef<HTMLDivElement | null>(null);
  const zoomLevelRef = useRef(zoomLevel);

  const visibleLayerIds = useMemo(
    () => new Set(layers.filter((layer) => layer.visible).map((layer) => layer.id)),
    [layers],
  );

  const filteredConflicts = useMemo(() => {
    if (filter === "전체") {
      return conflicts;
    }
    return conflicts.filter((conflict) => conflict.severity === filter);
  }, [filter]);

  const severeCount = conflicts.filter((conflict) => conflict.severity === "심각").length;
  const warningCount = conflicts.filter((conflict) => conflict.severity === "경고").length;
  const resolvedCount = applied ? 6 : 4;

  const toggleLayer = (layerId: LayerId) => {
    setLayers((current) =>
      current.map((layer) =>
        layer.id === layerId ? { ...layer, visible: !layer.visible } : layer,
      ),
    );
  };

  const updateStatus = useCallback((message: string) => {
    setToast(message);
  }, []);

  useEffect(() => {
    zoomLevelRef.current = zoomLevel;
  }, [zoomLevel]);

  const openDialog = (nextDialog: Exclude<DialogType, null>) => {
    setDialog(nextDialog);
    setCommandLine(`명령: ${dialogCommand(nextDialog)}`);
  };

  const handleTopAction = (action: Exclude<DialogType, null>) => {
    if (action === "save") {
      setDraftSaved(true);
      setToast("현재 도면 검토 상태를 저장했습니다.");
      setCommandLine("명령: QSAVE");
      return;
    }

    openDialog(action);
  };

  const handleDrawingUpload = (file: File) => {
    setUploadedFile(file);
    setZoomLevel((current) => Math.max(current, uploadedDrawingInitialZoom));
    setPanOffset({ x: 0, y: 0 });
    setToast(`${file.name} 파일을 불러오는 중입니다.`);
    setCommandLine(`명령: OPEN ${file.name}`);
  };

  const applyRecommendation = () => {
    setApplied(true);
    setToast("권장 대안을 적용했습니다. 충돌 해결 가능 수치가 갱신되었습니다.");
    setCommandLine("명령: OPTIMIZE APPLY");
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

    setCommandLine(`명령: ${tool.command}`);
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
    setCommandLine(event.deltaY < 0 ? "명령: ZOOM WHEEL +" : "명령: ZOOM WHEEL -");
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
    setCommandLine("명령: PAN");
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

  const runCommand = () => {
    const value = commandInput.trim();
    if (!value) {
      return;
    }

    const normalized = value.toUpperCase();
    setCommandInput("");
    setCommandLine(`명령: ${normalized}`);

    if (normalized === "ZOOM" || normalized === "Z") {
      setZoomLevel(125);
      setToast("ZOOM 명령을 실행했습니다.");
      return;
    }

    if (normalized === "EXTENTS" || normalized === "ZE") {
      setZoomLevel(100);
      setPanOffset({ x: 0, y: 0 });
      setToast("도면 범위를 화면에 맞췄습니다.");
      return;
    }

    if (normalized === "LAYER" || normalized === "LA") {
      setActiveTool("layers");
      setToast("레이어 패널을 활성화했습니다.");
      return;
    }

    setToast(`${normalized} 명령을 기록했습니다.`);
  };

  const toggleCadOption = (key: keyof typeof cadToggles) => {
    setCadToggles((current) => ({ ...current, [key]: !current[key] }));
    setCommandLine(`명령: ${key.toUpperCase()}`);
  };

  return (
    <main className="app-shell">
      <TopBar
        selectedFileName={uploadedFile?.name}
        draftSaved={draftSaved}
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
                  accept=".dwg,.dxf,.dwf,.dwfx,.xps"
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
                  <em>렌더링</em>
                </button>
              ) : null}
            </div>
          </section>

          <section className="panel-block compact">
            <div className="panel-heading inline">
              <h3>도면 레이어</h3>
              <Icon name="layers" />
            </div>
            <div className="layer-list">
              {layers.map((layer) => (
                <label key={layer.id} className="layer-row">
                  <input
                    type="checkbox"
                    checked={layer.visible}
                    onChange={() => toggleLayer(layer.id)}
                  />
                  <span>{layer.label}</span>
                  <i style={{ backgroundColor: layer.color }} />
                </label>
              ))}
            </div>
          </section>

          <section className="panel-block compact">
            <label className="range-row">
              <span>투명도</span>
              <input
                type="range"
                min="20"
                max="100"
                value={opacity}
                onChange={(event) => setOpacity(Number(event.target.value))}
              />
            </label>
          </section>
        </aside>

        <section className="canvas-panel">
          <Toolbar activeTool={activeTool} onToolAction={handleToolAction} />
          <div className="floor-controls">
            <button className="floor-select">1F <Icon name="chevron" /></button>
          </div>
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
                visibleLayerIds={visibleLayerIds}
                opacity={opacity}
                zoomLevel={zoomLevel}
                panOffset={panOffset}
                onStatusChange={updateStatus}
              />
            ) : null}
            {cadToggles.grid ? <div className="model-grid-overlay" aria-hidden="true" /> : null}
            <div className="cad-crosshair" aria-hidden="true" />
            <div className="ucs-widget" aria-label="UCS 축 표시">
              <span className="ucs-y">Y</span>
              <span className="ucs-x">X</span>
            </div>
            <div className="layout-tabs" aria-label="도면 레이아웃">
              {["Model", "Layout1", "Layout2"].map((item) => (
                <button
                  key={item}
                  className={layoutTab === item ? "active" : ""}
                  onClick={() => {
                    setLayoutTab(item);
                    setCommandLine(`명령: ${item.toUpperCase()}`);
                  }}
                >
                  {item}
                </button>
              ))}
            </div>
            <form
              className="command-line"
              onSubmit={(event) => {
                event.preventDefault();
                runCommand();
              }}
            >
              <span>{commandLine}</span>
              <input
                value={commandInput}
                placeholder="명령 입력"
                onChange={(event) => setCommandInput(event.target.value)}
              />
            </form>
            <div className="cad-status-toggles">
              <button className={cadToggles.grid ? "on" : ""} onClick={() => toggleCadOption("grid")}>GRID</button>
              <button className={cadToggles.snap ? "on" : ""} onClick={() => toggleCadOption("snap")}>SNAP</button>
              <button className={cadToggles.ortho ? "on" : ""} onClick={() => toggleCadOption("ortho")}>ORTHO</button>
              <button className={cadToggles.osnap ? "on" : ""} onClick={() => toggleCadOption("osnap")}>OSNAP</button>
              <strong>{zoomLevel}%</strong>
            </div>
          </div>
        </section>

        <aside className="right-panel">
          <section className="analysis-panel">
            <div className="tabs">
              <button className={tab === "result" ? "active" : ""} onClick={() => setTab("result")}>
                분석 결과
              </button>
              <button className={tab === "collisions" ? "active" : ""} onClick={() => setTab("collisions")}>
                충돌 감지 <strong>{conflicts.length + 2}</strong>
              </button>
            </div>

            <div className="metric-grid">
              <article>
                <span>총 충돌</span>
                <b>{conflicts.length + 2}<small>건</small></b>
                <p>심각: {severeCount + 1} <i /> 경고: {warningCount + 1}</p>
              </article>
              <article className="success">
                <span>해결 가능</span>
                <b>{resolvedCount}<small>건</small></b>
                <p>{applied ? "100%" : "67%"}</p>
              </article>
            </div>

            <div className="list-header">
              <h3>충돌 목록</h3>
              <select value={filter} onChange={(event) => setFilter(event.target.value as ConflictFilter)}>
                <option>전체</option>
                <option>심각</option>
                <option>경고</option>
              </select>
            </div>
            <div className="conflict-list">
              {filteredConflicts.map((conflict) => (
                <article key={conflict.id} className="conflict-card">
                  <div>
                    <span className={`severity ${conflict.tone}`}>{conflict.severity}</span>
                    <strong>{conflict.title}</strong>
                    <p>위치: {conflict.location}</p>
                    <p>높이: {conflict.height}</p>
                  </div>
                  <CollisionThumb tone={conflict.tone} />
                </article>
              ))}
            </div>
            <button
              className="text-link"
              onClick={() => {
                setTab("collisions");
                setFilter("전체");
                setToast("전체 충돌 목록을 표시했습니다.");
                setCommandLine("명령: COLLISION LIST");
              }}
            >
              모든 충돌 보기 <Icon name="arrow" />
            </button>
          </section>

          <section className="recommend-panel">
            <h3>최적화 제안</h3>
            {recommendations.map((item) => (
              <article key={item.id} className={`recommend-card ${item.recommended ? "recommended" : ""}`}>
                <div className="recommend-title">
                  <strong>{item.title}{item.recommended ? " (권장)" : ""}</strong>
                  {item.recommended ? <span>비용 절감</span> : <Icon name="chevron" />}
                </div>
                <p>{item.summary}</p>
                <div className="saving">
                  <small>예상 비용 절감</small>
                  <b>{item.saving}</b>
                </div>
                {item.recommended ? (
                  <button className="apply-button" onClick={applyRecommendation}>
                    적용하기
                  </button>
                ) : null}
              </article>
            ))}
          </section>
        </aside>
      </div>

      <footer className="status-bar">
        <span className="status-dot" />
        <strong>상태: {toast}</strong>
        <span>스프링클러 <b>52</b></span>
        <span>감지기 <b>38</b></span>
        <span>소화기 <b>6</b></span>
        <span>소화전 <b>2</b></span>
        <span>피난구 <b>3</b></span>
        <em><Icon name="shield" /> 한국 소방법 (NFSC) 기준 적용 중</em>
      </footer>
      <ActionDialog
        dialog={dialog}
        fileName={uploadedFile?.name ?? "선택된 도면 없음"}
        resolvedCount={resolvedCount}
        conflictCount={conflicts.length + 2}
        onClose={() => setDialog(null)}
      />
    </main>
  );
}

function TopBar({
  selectedFileName,
  draftSaved,
  onTopAction,
}: {
  selectedFileName?: string;
  draftSaved: boolean;
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
      <button className="project-select">
        프로젝트_샘플 <Icon name="chevron" />
      </button>
      <div className="file-pill">
        <Icon name="document" />
        <span>{selectedFileName ?? "도면 파일을 선택해주세요"}</span>
      </div>
      <nav className="top-actions">
        <button className={draftSaved ? "saved" : ""} onClick={() => onTopAction("save")}>
          {draftSaved ? "저장됨" : "저장"}
        </button>
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
    && Boolean(target.closest("button, input, select, textarea, label, .command-line, .layout-tabs, .cad-status-toggles"));
}

function dialogCommand(dialog: Exclude<DialogType, null>) {
  const commands = {
    save: "QSAVE",
    report: "REPORT PREVIEW",
    export: "EXPORT",
    notifications: "NOTIFICATIONCENTER",
  };

  return commands[dialog];
}

function ActionDialog({
  dialog,
  fileName,
  resolvedCount,
  conflictCount,
  onClose,
}: {
  dialog: DialogType;
  fileName: string;
  resolvedCount: number;
  conflictCount: number;
  onClose: () => void;
}) {
  const [exportFormat, setExportFormat] = useState("PDF");

  if (!dialog || dialog === "save") {
    return null;
  }

  const title = {
    report: "보고서 미리보기",
    export: "도면 내보내기",
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
                <span>검토 도면</span>
                <strong>{fileName}</strong>
              </article>
              <article>
                <span>충돌 감지</span>
                <strong>{conflictCount}건</strong>
              </article>
              <article>
                <span>해결 가능</span>
                <strong>{resolvedCount}건</strong>
              </article>
            </div>
            <div className="report-sheet">
              <h3>소방 설계 검토 보고서</h3>
              <p>NFSC 기준과 도면 레이어 충돌 결과를 기준으로 요약했습니다.</p>
              <ul>
                <li>스프링클러 배치 간섭 및 배관 우회 권장안 포함</li>
                <li>감지기, 소화전, 피난구 레이어 표시 상태 반영</li>
                <li>비용 절감 예상치와 우선 조치 항목 분리</li>
              </ul>
            </div>
            <button className="dialog-primary" onClick={onClose}>미리보기 닫기</button>
          </div>
        ) : null}

        {dialog === "export" ? (
          <div className="dialog-content">
            <div className="export-options" role="radiogroup" aria-label="내보내기 형식">
              {["PDF", "DWG", "DXF"].map((format) => (
                <button
                  key={format}
                  className={exportFormat === format ? "selected" : ""}
                  onClick={() => setExportFormat(format)}
                >
                  <strong>{format}</strong>
                  <span>{format === "PDF" ? "검토 보고서" : "도면 파일"}</span>
                </button>
              ))}
            </div>
            <div className="export-panel">
              <span>대상 파일</span>
              <strong>{fileName}</strong>
              <p>{exportFormat} 형식으로 내보낼 준비가 되었습니다.</p>
            </div>
            <button className="dialog-primary" onClick={onClose}>{exportFormat} 내보내기 준비</button>
          </div>
        ) : null}

        {dialog === "notifications" ? (
          <div className="dialog-content">
            <div className="notification-list">
              <article>
                <strong>도면 렌더링 상태</strong>
                <p>업로드된 CAD 파일은 로컬 브라우저에서 미리보기 렌더링됩니다.</p>
              </article>
              <article>
                <strong>충돌 분석 업데이트</strong>
                <p>스프링클러 배관 우회안 적용 시 해결 가능 항목이 갱신됩니다.</p>
              </article>
              <article>
                <strong>NFSC 기준</strong>
                <p>현재 프로젝트는 한국 소방법 기준 모드로 표시 중입니다.</p>
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
        <button key={item.label} className={item.active ? "active" : ""}>
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

function CollisionThumb({ tone }: { tone: "danger" | "warning" }) {
  return (
    <svg className="collision-thumb" viewBox="0 0 90 70" aria-hidden="true">
      <rect x="7" y="10" width="72" height="48" rx="4" fill="#0f1b28" stroke="#32445a" />
      <path d="M14 48L72 18M14 32L72 58" stroke={tone === "danger" ? "#ef4444" : "#f59e0b"} strokeWidth="3" />
      <path d="M20 54H76V25" fill="none" stroke="#43566c" />
      <circle cx="56" cy="35" r="8" fill="none" stroke={tone === "danger" ? "#ef4444" : "#f59e0b"} strokeWidth="2" />
      <path d="M56 25v20M46 35h20" stroke={tone === "danger" ? "#ef4444" : "#f59e0b"} strokeWidth="1.8" />
    </svg>
  );
}

function Icon({ name }: { name: string }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" } as const;
  switch (name) {
    case "flame":
      return <svg viewBox="0 0 24 24"><path fill="currentColor" d="M12.2 22c-4.4 0-7.5-3-7.5-7.1 0-2.9 1.7-5.4 4.5-7.9.3 2 .9 3.3 2.1 4.1.4-3.5 1.4-6.3 3.8-9.1 2.7 3.4 4.2 6.5 4.2 10.5 0 5.7-3.8 9.5-7.1 9.5Z" /></svg>;
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
