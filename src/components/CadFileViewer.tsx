import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState, type CSSProperties } from "react";
import {
  CadViewer,
  resolveCadColor,
  type CadLoadProgress,
  type CadViewerLoadResult,
} from "@flyfish-dev/cad-viewer";
import "@flyfish-dev/cad-viewer/style.css";
import type { CadBounds, CadDocument, CadEntity, CadPoint2D, CadPoint3D } from "@flyfish-dev/cad-viewer";
import type { LayerId } from "../types";

interface CadFileViewerProps {
  file: File | null;
  visibleLayerIds: Set<LayerId>;
  opacity: number;
  zoomLevel: number;
  panOffset: { x: number; y: number };
  onStatusChange: (message: string) => void;
}

// AI 방찾기 → 방 클릭 시 뷰어를 그 방으로 이동시키기 위한 핸들. 월드좌표(원시 DXF)를 받아
// renderCadDocument와 동일 변환으로 zoom/pan을 계산해 반환(App이 그 값으로 상태 set).
export interface CadFileViewerHandle {
  focusOnWorld: (worldX: number, worldY: number, zoomPercent?: number)
    => { zoomLevel: number; panOffset: { x: number; y: number } } | null;
}

type LoadState = "idle" | "loading" | "ready" | "error";

const cadWasmBase = "/wasm/";
const renderFailureTimeoutMs = 15_000;
const cadColorOptions = {
  background: "#07111d",
  foreground: "#d7e6f8",
  contrastMode: "adaptive",
  minColorContrast: 2.2,
} as const;

interface Transform2D {
  a: number;
  b: number;
  c: number;
  d: number;
  e: number;
  f: number;
}

export const CadFileViewer = forwardRef<CadFileViewerHandle, CadFileViewerProps>(function CadFileViewer({
  file,
  visibleLayerIds,
  opacity,
  zoomLevel,
  panOffset,
  onStatusChange,
}: CadFileViewerProps, ref) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<CadViewer | null>(null);
  const activeLoadIdRef = useRef(0);
  const acceptsViewerEventsRef = useRef(false);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [progress, setProgress] = useState<CadLoadProgress | null>(null);
  const [summary, setSummary] = useState<CadViewerLoadResult["summary"] | null>(null);
  const [cadDocument, setCadDocument] = useState<CadDocument | null>(null);
  const [errorMessage, setErrorMessage] = useState("");

  const focusDocumentView = useCallback((document: CadDocument) => {
    const viewer = viewerRef.current;
    const container = containerRef.current;
    if (!viewer || !container) {
      return;
    }

    const targetBounds = computeDenseDocumentBounds(document) ?? viewer.renderer.getBounds();
    if (!isFiniteBounds(targetBounds)) {
      viewer.fit();
      return;
    }

    const width = Math.max(targetBounds.maxX - targetBounds.minX, 1e-9);
    const height = Math.max(targetBounds.maxY - targetBounds.minY, 1e-9);
    const viewportWidth = Math.max(container.clientWidth - 72, 1);
    const viewportHeight = Math.max(container.clientHeight - 132, 1);
    const scale = Math.min(viewportWidth / width, viewportHeight / height) * 0.9;

    viewer.renderer.setViewState({
      centerX: (targetBounds.minX + targetBounds.maxX) / 2,
      centerY: (targetBounds.minY + targetBounds.maxY) / 2,
      scale,
    });
  }, []);

  // 월드좌표(원시 DXF) → 그 지점을 화면 중앙에 놓는 zoom/pan 계산(renderCadDocument와 동일 공식).
  useImperativeHandle(ref, () => ({
    focusOnWorld(worldX, worldY, zoomPercent = 260) {
      // renderCadDocument는 canvas.parentElement(=.cad-file-viewer 루트, 미변환)를 측정한다.
      // containerRef(cad-viewer-host)는 CSS zoom/pan 변환이 걸려 rect가 왜곡되므로 루트를 쓴다.
      const container = containerRef.current?.parentElement;
      if (!container || !cadDocument) {
        return null;
      }
      const bounds = computeDenseDocumentBounds(cadDocument);
      if (!bounds || !isFiniteBounds(bounds)) {
        return null;
      }
      const rect = container.getBoundingClientRect();
      const width = Math.max(rect.width, 1);
      const height = Math.max(rect.height, 1);
      const boundsWidth = Math.max(bounds.maxX - bounds.minX, 1e-9);
      const boundsHeight = Math.max(bounds.maxY - bounds.minY, 1e-9);
      const scale = Math.min((width - 80) / boundsWidth, (height - 150) / boundsHeight) * 0.9 * (zoomPercent / 100);
      const centerX = (bounds.minX + bounds.maxX) / 2;
      const centerY = (bounds.minY + bounds.maxY) / 2;
      return {
        zoomLevel: zoomPercent,
        panOffset: { x: -(worldX - centerX) * scale, y: (worldY - centerY) * scale },
      };
    },
  }), [cadDocument]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return undefined;
    }

    viewerRef.current = new CadViewer({
      container,
      renderer: "canvas2d",
      wasmPath: cadWasmBase,
      dwfWasmUrl: `${cadWasmBase}dwfv-render.wasm`,
      workerUrl: `${cadWasmBase}dwg-worker.js`,
      workerTimeoutMs: 120_000,
      includePaperSpace: true,
      autoFit: true,
      canvasOptions: {
        background: "#07111d",
        foreground: "#d7e6f8",
        contrastMode: "adaptive",
        minColorContrast: 2.2,
        showPageBounds: true,
      },
      onLoadProgress: (nextProgress) => {
        if (!acceptsViewerEventsRef.current) {
          return;
        }

        setProgress(nextProgress);
        onStatusChange(`도면 렌더링 중: ${nextProgress.message}`);
      },
    });

    return () => {
      viewerRef.current?.destroy();
      viewerRef.current = null;
    };
  }, [file?.name, focusDocumentView, onStatusChange]);

  useEffect(() => {
    if (!file || !viewerRef.current) {
      activeLoadIdRef.current += 1;
      acceptsViewerEventsRef.current = false;
      setLoadState("idle");
      setProgress(null);
      setSummary(null);
      setCadDocument(null);
      setErrorMessage("");
      viewerRef.current?.clear();
      return;
    }

    const controller = new AbortController();
    const loadId = activeLoadIdRef.current + 1;
    activeLoadIdRef.current = loadId;
    acceptsViewerEventsRef.current = true;
    setLoadState("loading");
    setProgress({ phase: "read", message: "파일 읽는 중", percent: 5 });
    setSummary(null);
    setCadDocument(null);
    setErrorMessage("");

    const isActiveLoad = () => activeLoadIdRef.current === loadId && !controller.signal.aborted;
    const failRendering = (message: string) => {
      if (activeLoadIdRef.current !== loadId) {
        return;
      }

      acceptsViewerEventsRef.current = false;
      controller.abort();
      setProgress(null);
      setErrorMessage(message);
      setLoadState("error");
      onStatusChange(`도면 렌더링 실패: ${message}`);
    };
    const timeoutId = window.setTimeout(() => {
      failRendering(`${file.name} 렌더링이 15초 안에 완료되지 않았습니다.`);
    }, renderFailureTimeoutMs);

    const loadCadFile = async () => {
      if (!viewerRef.current) {
        return;
      }

      const buffer = await file.arrayBuffer();
      if (!isActiveLoad()) {
        return;
      }

      const result = await viewerRef.current.loadBuffer(buffer, file.name, {
        wasmPath: cadWasmBase,
        dwfWasmUrl: `${cadWasmBase}dwfv-render.wasm`,
        workerUrl: `${cadWasmBase}dwg-worker.js`,
        includePaperSpace: true,
        useWorker: true,
        signal: controller.signal,
      });
      if (!isActiveLoad()) {
        return;
      }

      window.clearTimeout(timeoutId);
      acceptsViewerEventsRef.current = false;
      setSummary(result.summary);
      setCadDocument(result.document);
      setLoadState("ready");
      requestAnimationFrame(() => {
        viewerRef.current?.resize();
        focusDocumentView(result.document);
      });
      onStatusChange(`${result.fileName ?? file.name} 렌더링 완료`);
    };

    loadCadFile()
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }

        window.clearTimeout(timeoutId);
        acceptsViewerEventsRef.current = false;
        const message = error instanceof Error ? error.message : "알 수 없는 렌더링 오류";
        setErrorMessage(message);
        setLoadState("error");
        onStatusChange(`도면 렌더링 실패: ${message}`);
      });

    return () => {
      window.clearTimeout(timeoutId);
      acceptsViewerEventsRef.current = false;
      controller.abort();
    };
  }, [file, focusDocumentView, onStatusChange]);

  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer) {
      return;
    }

    viewer.setCanvasOptions({
      background: "#07111d",
      foreground: "#d7e6f8",
      contrastMode: "adaptive",
      minColorContrast: 2.2,
      showPageBounds: true,
    });
    viewer.resize();
  }, [opacity, visibleLayerIds]);

  const progressPercent = progress?.percent ?? (loadState === "ready" ? 100 : 0);
  const formatLabel = file?.name.split(".").pop()?.toUpperCase() ?? "DWG/DXF";

  return (
    <div
      className="cad-file-viewer"
      style={{
        "--cad-zoom": zoomLevel / 100,
        "--cad-pan-x": `${panOffset.x}px`,
        "--cad-pan-y": `${panOffset.y}px`,
      } as CSSProperties}
    >
      <div ref={containerRef} className="cad-viewer-host" />

      {cadDocument && loadState === "ready" ? (
        <DetailedCadDocumentCanvas document={cadDocument} zoomLevel={zoomLevel} panOffset={panOffset} />
      ) : null}

      {loadState !== "ready" ? (
        <div className="cad-empty-state">
          <div className="cad-empty-icon">{formatLabel}</div>
          {loadState === "idle" ? (
            <>
              <strong>도면 파일을 업로드해주세요</strong>
              <span>DWG, DXF, DWF 파일을 선택하면 브라우저에서 바로 렌더링합니다.</span>
            </>
          ) : null}
          {loadState === "loading" ? (
            <>
              <strong>{file?.name} 렌더링 중</strong>
              <span>{progress?.message ?? "DWG 파서 준비 중"}</span>
              <div className="cad-progress">
                <i style={{ width: `${progressPercent}%` }} />
              </div>
            </>
          ) : null}
          {loadState === "error" ? (
            <>
              <strong>렌더링 실패</strong>
              <span>{errorMessage}</span>
            </>
          ) : null}
        </div>
      ) : null}

      {summary ? (
        <div className="cad-render-badge">
          <span>{summary.format.toUpperCase()}</span>
          <b>{summary.entityCount.toLocaleString()} entities</b>
          <em>{summary.layerCount.toLocaleString()} layers</em>
        </div>
      ) : null}
    </div>
  );
});

function DetailedCadDocumentCanvas({
  document,
  zoomLevel,
  panOffset,
}: {
  document: CadDocument;
  zoomLevel: number;
  panOffset: { x: number; y: number };
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = canvas?.parentElement;
    if (!canvas || !host) {
      return undefined;
    }

    const draw = () => {
      const rect = host.getBoundingClientRect();
      const width = Math.max(rect.width, 1);
      const height = Math.max(rect.height, 1);
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;

      const context = canvas.getContext("2d");
      if (!context) {
        return;
      }

      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      context.clearRect(0, 0, width, height);
      renderCadDocument(context, document, width, height, zoomLevel, panOffset);
    };

    draw();
    const resizeObserver = new ResizeObserver(draw);
    resizeObserver.observe(host);
    return () => resizeObserver.disconnect();
  }, [document, zoomLevel, panOffset]);

  return <canvas ref={canvasRef} className="detailed-cad-canvas" aria-label="상세 CAD 도면 렌더링" />;
}

function renderCadDocument(
  context: CanvasRenderingContext2D,
  document: CadDocument,
  width: number,
  height: number,
  zoomLevel: number,
  panOffset: { x: number; y: number },
) {
  const bounds = computeDenseDocumentBounds(document);
  if (!bounds || !isFiniteBounds(bounds)) {
    return;
  }

  const boundsWidth = Math.max(bounds.maxX - bounds.minX, 1e-9);
  const boundsHeight = Math.max(bounds.maxY - bounds.minY, 1e-9);
  const scale = Math.min((width - 80) / boundsWidth, (height - 150) / boundsHeight) * 0.9 * (zoomLevel / 100);
  const center = {
    x: (bounds.minX + bounds.maxX) / 2,
    y: (bounds.minY + bounds.maxY) / 2,
  };
  const screen = (point: CadPoint2D) => ({
    x: width / 2 + panOffset.x + (point.x - center.x) * scale,
    y: height / 2 + panOffset.y - (point.y - center.y) * scale,
  });

  context.save();
  context.lineCap = "round";
  context.lineJoin = "round";
  context.globalCompositeOperation = "source-over";
  drawEntities(context, document, document.entities, identityTransform(), screen, scale, 0);
  for (const page of document.pages ?? []) {
    drawEntities(context, document, page.entities, identityTransform(), screen, scale, 0);
  }
  context.restore();
}

function drawEntities(
  context: CanvasRenderingContext2D,
  document: CadDocument,
  entities: CadEntity[],
  transform: Transform2D,
  screen: (point: CadPoint2D) => CadPoint2D,
  scale: number,
  depth: number,
) {
  if (depth > 8) {
    return;
  }

  for (const entity of entities) {
    drawEntity(context, document, entity, transform, screen, scale, depth);
  }
}

function drawEntity(
  context: CanvasRenderingContext2D,
  document: CadDocument,
  entity: CadEntity,
  transform: Transform2D,
  screen: (point: CadPoint2D) => CadPoint2D,
  scale: number,
  depth: number,
) {
  const layer = entity.layer ? document.layers[entity.layer] : undefined;
  if (layer?.isVisible === false || layer?.isFrozen) {
    return;
  }

  if (entity.kind === "insert" || entity.type.toUpperCase() === "INSERT") {
    const blockName = entity.blockName ?? entity.name;
    const block = blockName ? document.blocks[blockName] : undefined;
    if (block) {
      drawEntities(context, document, block.entities, multiplyTransform(transform, insertTransform(entity)), screen, scale, depth + 1);
    }
    return;
  }

  const color = resolveCadColor(entity, document, cadColorOptions);
  context.strokeStyle = color;
  context.fillStyle = color;
  context.lineWidth = Math.max(0.55, Math.min(1.8, (Number(entity.lineweight) || 25) / 25));
  context.globalAlpha = Number(entity.opacity ?? 1);

  const drawPolyline = (sourcePoints: CadPoint3D[] | undefined, closePath = false) => {
    const pathPoints = (sourcePoints ?? []).map((point) => screen(applyTransform(transform, point)));
    if (pathPoints.length < 2) {
      return;
    }

    context.beginPath();
    context.moveTo(pathPoints[0].x, pathPoints[0].y);
    for (const point of pathPoints.slice(1)) {
      context.lineTo(point.x, point.y);
    }
    if (closePath) {
      context.closePath();
    }
    context.stroke();
  };

  switch (entity.kind) {
    case "line":
      drawPolyline([entity.startPoint, entity.endPoint].filter(isCadPoint));
      return;
    case "polyline":
      drawPolyline(entity.vertices ?? entity.points, entity.isClosed);
      return;
    case "spline":
      drawPolyline(entity.fitPoints?.length ? entity.fitPoints : entity.controlPoints);
      return;
    case "circle":
      drawCircleLike(context, entity, transform, screen, scale);
      return;
    case "arc":
      drawArc(context, entity, transform, screen, scale);
      return;
    case "ellipse":
      drawEllipseApproximation(context, entity, transform, screen);
      return;
    case "solid":
    case "hatch":
      drawFilledEntity(context, entity, transform, screen);
      return;
    case "path":
      drawPathCommands(context, entity.commands, transform, screen, entity.isClosed);
      return;
    case "text":
      drawTextEntity(context, entity, transform, screen, scale);
      return;
    case "point":
      drawPointEntity(context, entity, transform, screen);
      return;
    default:
      if (entity.vertices || entity.points) {
        drawPolyline(entity.vertices ?? entity.points, entity.isClosed);
      }
  }

  context.globalAlpha = 1;
}

function drawCircleLike(
  context: CanvasRenderingContext2D,
  entity: CadEntity,
  transform: Transform2D,
  screen: (point: CadPoint2D) => CadPoint2D,
  scale: number,
) {
  if (!entity.center || !Number.isFinite(entity.radius)) {
    return;
  }

  const center = screen(applyTransform(transform, entity.center));
  const radius = Math.abs(Number(entity.radius) * scale * transformScale(transform));
  if (radius < 0.45) {
    return;
  }

  context.beginPath();
  context.arc(center.x, center.y, radius, 0, Math.PI * 2);
  context.stroke();
}

function drawArc(
  context: CanvasRenderingContext2D,
  entity: CadEntity,
  transform: Transform2D,
  screen: (point: CadPoint2D) => CadPoint2D,
  scale: number,
) {
  if (!entity.center || !Number.isFinite(entity.radius)) {
    return;
  }

  const center = screen(applyTransform(transform, entity.center));
  const radius = Math.abs(Number(entity.radius) * scale * transformScale(transform));
  if (radius < 0.45) {
    return;
  }

  context.beginPath();
  context.arc(center.x, center.y, radius, normalizeAngle(entity.startAngle ?? 0), normalizeAngle(entity.endAngle ?? Math.PI * 2), false);
  context.stroke();
}

function drawEllipseApproximation(
  context: CanvasRenderingContext2D,
  entity: CadEntity,
  transform: Transform2D,
  screen: (point: CadPoint2D) => CadPoint2D,
) {
  if (!entity.center || !entity.majorAxisEndPoint) {
    return;
  }

  const ratio = Number(entity.axisRatio ?? 1);
  const center = entity.center;
  const major = entity.majorAxisEndPoint;
  const majorLength = Math.hypot(major.x, major.y);
  const rotation = Math.atan2(major.y, major.x);
  const points: CadPoint3D[] = [];
  for (let index = 0; index <= 56; index += 1) {
    const angle = (Math.PI * 2 * index) / 56;
    const x = Math.cos(angle) * majorLength;
    const y = Math.sin(angle) * majorLength * ratio;
    points.push({
      x: center.x + x * Math.cos(rotation) - y * Math.sin(rotation),
      y: center.y + x * Math.sin(rotation) + y * Math.cos(rotation),
    });
  }
  drawPathFromPoints(context, points, transform, screen, true);
}

function drawFilledEntity(
  context: CanvasRenderingContext2D,
  entity: CadEntity,
  transform: Transform2D,
  screen: (point: CadPoint2D) => CadPoint2D,
) {
  const loops = entity.loops ?? [];
  if (!loops.length && entity.vertices) {
    drawPathFromPoints(context, entity.vertices, transform, screen, true);
    return;
  }

  for (const loop of loops) {
    if (loop.vertices?.length) {
      drawPathFromPoints(context, loop.vertices, transform, screen, true);
    }
    if (loop.commands?.length) {
      drawPathCommands(context, loop.commands, transform, screen, true);
    }
  }
}

function drawPathFromPoints(
  context: CanvasRenderingContext2D,
  points: CadPoint3D[],
  transform: Transform2D,
  screen: (point: CadPoint2D) => CadPoint2D,
  closePath: boolean,
) {
  if (points.length < 2) {
    return;
  }

  const screenPoints = points.map((point) => screen(applyTransform(transform, point)));
  context.beginPath();
  context.moveTo(screenPoints[0].x, screenPoints[0].y);
  for (const point of screenPoints.slice(1)) {
    context.lineTo(point.x, point.y);
  }
  if (closePath) {
    context.closePath();
  }
  context.stroke();
}

function drawPathCommands(
  context: CanvasRenderingContext2D,
  commands: CadEntity["commands"],
  transform: Transform2D,
  screen: (point: CadPoint2D) => CadPoint2D,
  closePath = false,
) {
  if (!commands?.length) {
    return;
  }

  context.beginPath();
  for (const command of commands) {
    const points = command.points.map((point) => screen(applyTransform(transform, point)));
    if (command.cmd === "M" && points[0]) {
      context.moveTo(points[0].x, points[0].y);
    } else if (command.cmd === "L" && points[0]) {
      context.lineTo(points[0].x, points[0].y);
    } else if (command.cmd === "Q" && points.length >= 2) {
      context.quadraticCurveTo(points[0].x, points[0].y, points[1].x, points[1].y);
    } else if (command.cmd === "C" && points.length >= 3) {
      context.bezierCurveTo(points[0].x, points[0].y, points[1].x, points[1].y, points[2].x, points[2].y);
    } else if (command.cmd === "Z") {
      context.closePath();
    }
  }
  if (closePath) {
    context.closePath();
  }
  context.stroke();
}

function drawTextEntity(
  context: CanvasRenderingContext2D,
  entity: CadEntity,
  transform: Transform2D,
  screen: (point: CadPoint2D) => CadPoint2D,
  scale: number,
) {
  const anchor = entity.insertionPoint ?? entity.startPoint ?? entity.center;
  const text = entity.text ?? entity.value;
  if (!anchor || !text) {
    return;
  }

  const fontSize = Math.abs(Number(entity.height ?? entity.textHeight ?? 1) * scale * transformScale(transform));
  if (fontSize < 3 || fontSize > 42) {
    return;
  }

  const point = screen(applyTransform(transform, anchor));
  context.save();
  context.font = `${Math.max(4, fontSize)}px Arial`;
  context.fillText(String(text).slice(0, 80), point.x, point.y);
  context.restore();
}

function drawPointEntity(
  context: CanvasRenderingContext2D,
  entity: CadEntity,
  transform: Transform2D,
  screen: (point: CadPoint2D) => CadPoint2D,
) {
  const point = entity.insertionPoint ?? entity.center ?? entity.startPoint;
  if (!point) {
    return;
  }

  const screenPoint = screen(applyTransform(transform, point));
  context.beginPath();
  context.arc(screenPoint.x, screenPoint.y, 1.8, 0, Math.PI * 2);
  context.stroke();
}

function computeDenseDocumentBounds(document: CadDocument): CadBounds | null {
  const points: CadPoint3D[] = [];
  collectEntitiesPoints(document.entities, points);
  for (const page of document.pages ?? []) {
    collectEntitiesPoints(page.entities, points);
  }

  if (points.length < 4) {
    return null;
  }

  const xs = points.map((point) => point.x).filter(Number.isFinite).sort((a, b) => a - b);
  const ys = points.map((point) => point.y).filter(Number.isFinite).sort((a, b) => a - b);
  if (xs.length < 4 || ys.length < 4) {
    return null;
  }

  const trimRatio = points.length > 300 ? 0.12 : 0.02;
  const trimCount = Math.floor(Math.min(xs.length, ys.length) * trimRatio);
  const lowIndex = Math.min(trimCount, xs.length - 1);
  const highXIndex = Math.max(xs.length - 1 - trimCount, lowIndex);
  const highYIndex = Math.max(ys.length - 1 - trimCount, lowIndex);

  return {
    minX: xs[lowIndex],
    minY: ys[lowIndex],
    maxX: xs[highXIndex],
    maxY: ys[highYIndex],
  };
}

function collectEntitiesPoints(entities: CadEntity[], points: CadPoint3D[]) {
  for (const entity of entities) {
    collectEntityPoints(entity, points);
  }
}

function collectEntityPoints(entity: CadEntity, points: CadPoint3D[]) {
  appendPoint(points, entity.startPoint);
  appendPoint(points, entity.endPoint);
  appendPoint(points, entity.insertionPoint);

  if (entity.center && Number.isFinite(entity.radius)) {
    const radius = Number(entity.radius);
    appendPoint(points, { x: entity.center.x - radius, y: entity.center.y - radius });
    appendPoint(points, { x: entity.center.x + radius, y: entity.center.y + radius });
  } else {
    appendPoint(points, entity.center);
  }

  appendPoints(points, entity.vertices);
  appendPoints(points, entity.points);
  appendPoints(points, entity.controlPoints);
  appendPoints(points, entity.fitPoints);

  for (const loop of entity.loops ?? []) {
    appendPoints(points, loop.vertices);
    for (const command of loop.commands ?? []) {
      appendPoints(points, command.points);
    }
  }

  for (const command of entity.commands ?? []) {
    appendPoints(points, command.points);
  }
}

function appendPoints(points: CadPoint3D[], candidates?: CadPoint3D[]) {
  for (const candidate of candidates ?? []) {
    appendPoint(points, candidate);
  }
}

function appendPoint(points: CadPoint3D[], candidate?: CadPoint3D) {
  if (!candidate || !Number.isFinite(candidate.x) || !Number.isFinite(candidate.y)) {
    return;
  }

  points.push({ x: candidate.x, y: candidate.y });
}

function identityTransform(): Transform2D {
  return { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 };
}

function insertTransform(entity: CadEntity): Transform2D {
  const insertion = entity.insertionPoint ?? { x: 0, y: 0 };
  const rotation = normalizeAngle(Number(entity.rotation ?? 0));
  const scaleX = Number(entity.scale?.x ?? 1);
  const scaleY = Number(entity.scale?.y ?? scaleX);
  const cos = Math.cos(rotation);
  const sin = Math.sin(rotation);

  return {
    a: cos * scaleX,
    b: sin * scaleX,
    c: -sin * scaleY,
    d: cos * scaleY,
    e: insertion.x,
    f: insertion.y,
  };
}

function multiplyTransform(parent: Transform2D, child: Transform2D): Transform2D {
  return {
    a: parent.a * child.a + parent.c * child.b,
    b: parent.b * child.a + parent.d * child.b,
    c: parent.a * child.c + parent.c * child.d,
    d: parent.b * child.c + parent.d * child.d,
    e: parent.a * child.e + parent.c * child.f + parent.e,
    f: parent.b * child.e + parent.d * child.f + parent.f,
  };
}

function applyTransform(transform: Transform2D, point: CadPoint2D): CadPoint2D {
  return {
    x: transform.a * point.x + transform.c * point.y + transform.e,
    y: transform.b * point.x + transform.d * point.y + transform.f,
  };
}

function transformScale(transform: Transform2D) {
  const scaleX = Math.hypot(transform.a, transform.b);
  const scaleY = Math.hypot(transform.c, transform.d);
  return Math.max((scaleX + scaleY) / 2, 1e-9);
}

function normalizeAngle(angle: number) {
  if (!Number.isFinite(angle)) {
    return 0;
  }

  return Math.abs(angle) > Math.PI * 2 ? (angle * Math.PI) / 180 : angle;
}

function isCadPoint(candidate: CadPoint3D | undefined): candidate is CadPoint3D {
  return Boolean(candidate) && Number.isFinite(candidate?.x) && Number.isFinite(candidate?.y);
}

function isFiniteBounds(bounds: CadBounds) {
  return [bounds.minX, bounds.minY, bounds.maxX, bounds.maxY].every(Number.isFinite)
    && bounds.maxX > bounds.minX
    && bounds.maxY > bounds.minY;
}
