import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Loader2,
  AlertTriangle,
  ExternalLink,
  Download,
  Box,
  RefreshCw,
} from "lucide-react";
import * as GaussianSplats3D from "@mkkellogg/gaussian-splats-3d";
import { getModelUrl } from "../api/client";

interface ModelViewerProps {
  taskId: string;
  reconstructionId: string;
  ossUrl?: string | null;
}

type RenderMode = "splat" | "online";

export default function ModelViewer({
  taskId,
  reconstructionId,
  ossUrl,
}: ModelViewerProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<GaussianSplats3D.Viewer | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [mode, setMode] = useState<RenderMode>("splat");
  const [webgl2Supported, setWebgl2Supported] = useState(true);

  // 优先使用 OSS 公开链接（跨域直加载），否则回退到后端代理
  const modelUrl = ossUrl || getModelUrl(taskId);

  // 检测 WebGL2 支持
  useEffect(() => {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl2");
    setWebgl2Supported(gl !== null);
  }, []);

  // 加载高斯泼溅模型
  useEffect(() => {
    if (mode !== "splat" || !containerRef.current) return;

    let cancelled = false;
    setLoading(true);
    setError("");

    // 等待容器有尺寸后再初始化
    const initViewer = async (retries = 30) => {
      if (cancelled || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      if (rect.width < 10 || rect.height < 10) {
        if (retries > 0) {
          await new Promise((r) => setTimeout(r, 100));
          return initViewer(retries - 1);
        }
      }

      try {
        const viewer = new GaussianSplats3D.Viewer({
          rootElement: containerRef.current,
          sharedMemoryForWorkers: false,
          gpuAcceleratedSort: false,
          halfPrecisionCovariancesOnGPU: true,
          useBuiltInControls: true,
          // 默认视角：与 superspl.at 一致的初始位置
          initialCameraPosition: [0, 2, 5],
          initialCameraLookAt: [0, 0, 0],
        });
        viewerRef.current = viewer;

        const loadStart = Date.now();
        await viewer.addSplatScenes(
          [
            {
              path: modelUrl,
              // COLMAP 坐标系 Y 朝下，Three.js Y 朝上 → 绕 X 轴旋转 180° 翻正
              // 四元数 [x, y, z, w] = [sin(π/2), 0, 0, cos(π/2)] = [1, 0, 0, 0]
              rotation: [1, 0, 0, 0],
            },
          ],
          false
        );

        // ---- 解锁 OrbitControls，实现与 superspl.at 同等的全方位视角 ----
        // 原因：gaussian-splats-3d 内置的 setupControls() 写死了
        // maxPolarAngle = 0.75π（看不到底部）+ rotateSpeed = 0.5（拖拽太慢）
        // 在 addSplatScenes 之后，controls 已完成初始化，直接改配置即可生效
        // 注：gaussian-splats-3d 类型定义未声明 controls，运行时存在，故断言访问
        const controls = (viewer as GaussianSplats3D.Viewer & { controls: any }).controls;
        if (controls) {
          // 1) 俯仰角：0°（正上方）~ 180°（正下方），全方位无死角
          controls.maxPolarAngle = Math.PI;
          controls.minPolarAngle = 0;
          // 2) 水平角：无限制（可无限旋转一周）
          controls.minAzimuthAngle = -Infinity;
          controls.maxAzimuthAngle = Infinity;
          // 3) 距离：不做限制
          controls.minDistance = 0;
          controls.maxDistance = Infinity;
          // 4) 拖拽旋转/平移/缩放速度，对齐 superspl.at
          controls.rotateSpeed = 1.0;
          controls.panSpeed = 1.0;
          controls.zoomSpeed = 1.2;
          // 5) 阻尼惯性
          controls.enableDamping = true;
          controls.dampingFactor = 0.08;
          // 6) 立刻生效一次
          controls.update();
        }

        if (!cancelled) {
          viewer.start();
          setLoading(false);
          // eslint-disable-next-line no-console
          console.info(
            "[ModelViewer] 模型加载完成，耗时",
            Math.round((Date.now() - loadStart) / 1000),
            "s | URL:",
            modelUrl,
            controls ? "| controls 已解锁(全方位视角)" : ""
          );
        }
      } catch (err) {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : String(err);
          // eslint-disable-next-line no-console
          console.error("[ModelViewer] 模型加载失败:", err, "\nURL:", modelUrl);
          setError(msg || t("viewer.error"));
          setLoading(false);
        }
      }
    };

    initViewer();

    return () => {
      cancelled = true;
      const viewer = viewerRef.current;
      if (viewer) {
        viewer.dispose().catch(() => {
          /* 忽略清理错误 */
        });
      }
      viewerRef.current = null;
      if (containerRef.current) {
        containerRef.current.innerHTML = "";
      }
    };
  }, [mode, modelUrl]);

  const handleOpenSupersplat = () => {
    window.open("https://superspl.at/editor", "_blank", "noopener,noreferrer");
  };

  const handleDownload = () => {
    const a = document.createElement("a");
    a.href = modelUrl;
    a.download = `${reconstructionId}.ply`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const handleRetry = () => {
    setMode("online");
    setTimeout(() => setMode("splat"), 100);
  };

  return (
    <div className="space-y-4">
      {/* 模式切换 */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => setMode("splat")}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-all ${
            mode === "splat"
              ? "bg-gold-400/20 text-gold-300"
              : "text-gray-400 hover:bg-gold-400/5"
          }`}
        >
          <Box className="h-3.5 w-3.5" />
          {t("viewer.title")}
        </button>
        <button
          onClick={() => setMode("online")}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-all ${
            mode === "online"
              ? "bg-gold-400/20 text-gold-300"
              : "text-gray-400 hover:bg-gold-400/5"
          }`}
        >
          <ExternalLink className="h-3.5 w-3.5" />
          superspl.at
        </button>
        <div className="ml-auto flex gap-2">
          <button
            onClick={handleDownload}
            className="inline-flex items-center gap-1.5 border border-gold-400/30 px-3 py-1.5 text-xs text-gold-300 transition-all hover:bg-gold-400/10"
          >
            <Download className="h-3.5 w-3.5" />
            {t("detail.download")}
          </button>
        </div>
      </div>

      {/* 渲染区域 */}
      {mode === "splat" ? (
        <div className="relative h-[500px] overflow-hidden  border border-gold-400/20 bg-ink-900">
          {!webgl2Supported && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 p-8 text-center">
              <AlertTriangle className="h-10 w-10 text-yellow-400/60" />
              <p className="text-sm text-gray-400">
                {t("viewer.webgl2Unsupported")}
                <br />
                {t("viewer.webgl2Fallback")}
              </p>
              <button onClick={handleOpenSupersplat} className="gold-btn-outline">
                <ExternalLink className="h-4 w-4" />
                {t("viewer.openExternal")}
              </button>
            </div>
          )}

          {webgl2Supported && loading && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-ink-900/80 backdrop-blur-sm">
              <Loader2 className="h-8 w-8 animate-spin text-gold-400" />
              <p className="text-sm text-gray-400">{t("viewer.loading")}</p>
            </div>
          )}

          {webgl2Supported && error && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 p-8 text-center">
              <AlertTriangle className="h-10 w-10 text-red-400/60" />
              <p className="text-sm text-red-300">{t("viewer.error")}</p>
              <p className="max-w-md text-xs text-gray-500">{error}</p>
              <div className="flex gap-2">
                <button onClick={handleRetry} className="gold-btn-outline">
                  <RefreshCw className="h-4 w-4" />
                  {t("common.retry")}
                </button>
                <button
                  onClick={() => setMode("online")}
                  className="gold-btn-outline"
                >
                  <ExternalLink className="h-4 w-4" />
                  {t("viewer.openExternal")}
                </button>
              </div>
            </div>
          )}

          <div
            ref={containerRef}
            className="h-full w-full"
            style={{ minHeight: "500px" }}
          />
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center gap-6  border border-gold-400/20 bg-ink-900 p-12">
          <div className="flex h-16 w-16 items-center justify-center bg-gold-400/10">
            <ExternalLink className="h-8 w-8 text-gold-400" />
          </div>
          <div className="max-w-md text-center">
            <h3 className="mb-2 text-lg font-semibold text-gold-200">
              {t("viewer.onlineViewerTitle")}
            </h3>
            <p className="text-sm text-gray-400">{t("viewer.onlineGuide")}</p>
            <p className="mt-3 text-xs text-gray-500">
              {t("viewer.downloadGuide")}
            </p>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row">
            <button onClick={handleOpenSupersplat} className="gold-btn">
              <ExternalLink className="h-4 w-4" />
              {t("viewer.openExternal")}
            </button>
            <button onClick={handleDownload} className="gold-btn-outline">
              <Download className="h-4 w-4" />
              {t("detail.download")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
