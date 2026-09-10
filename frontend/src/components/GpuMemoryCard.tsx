import { useTranslation } from "react-i18next";
import { Cpu, TrendingUp } from "lucide-react";
import type { TaskInfo } from "../types";

interface GpuMemoryCardProps {
  gpu?: TaskInfo["gpu_memory"];
}

/**
 * GPU 显存占用卡片：展示 3090 训练阶段的显存占用与峰值。
 *
 * - used_gb / total_gb：采样到的显存已用 / 总容量（GB）
 * - percent：当前占用百分比
 * - peak_gb：整个训练过程采样到的最高占用（GB）
 */
export default function GpuMemoryCard({ gpu }: GpuMemoryCardProps) {
  const { t } = useTranslation();

  if (!gpu || (gpu.peak_gb ?? 0) <= 0) {
    return null;
  }

  const used = gpu.used_gb ?? 0;
  const total = gpu.total_gb ?? 0;
  const peak = gpu.peak_gb ?? 0;
  const percent = gpu.percent ?? (total > 0 ? Math.round((used / total) * 100) : 0);

  // 占用越高越红，越低越绿
  const barColor =
    percent >= 90
      ? "bg-red-500"
      : percent >= 70
        ? "bg-amber-400"
        : "bg-emerald-400";

  return (
    <div className="gold-card p-6">
      <div className="mb-5 flex items-center gap-2 border-b border-gold-400/15 pb-4">
        <Cpu className="h-4 w-4 text-gold-400" />
        <h2 className="font-mono text-xs uppercase tracking-[0.2em] text-gold-300">
          [ {t("detail.gpu.title")} ]
        </h2>
        <span className="ml-auto font-mono text-[10px] uppercase tracking-wider text-gray-600">
          {t("detail.gpu.subtitle")}
        </span>
      </div>

      <div className="flex items-end justify-between">
        <div className="flex items-baseline gap-1">
          <span className="font-mono text-2xl font-bold tracking-tight text-gold-300">
            {peak.toFixed(1)}
          </span>
          <span className="font-mono text-[10px] text-gray-500">/ {total.toFixed(1)} GB</span>
          <span className="ml-2 font-mono text-[10px] uppercase tracking-wider text-gray-500">
            {t("detail.gpu.peak")}
          </span>
        </div>
        <div className="flex items-center gap-1 text-[11px] text-gray-400">
          <TrendingUp className="h-3.5 w-3.5 text-gold-400/80" />
          <span className="font-mono">
            {t("detail.gpu.current")}: {used.toFixed(1)} GB ({percent}%)
          </span>
        </div>
      </div>

      {/* 当前显存占用进度条 */}
      <div className="mt-4 h-2 w-full overflow-hidden rounded-full bg-ink-400">
        <div
          className={`h-full ${barColor} transition-all duration-500`}
          style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
        />
      </div>

      <p className="mt-2 font-mono text-[10px] uppercase tracking-wider text-gray-600">
        {t("detail.gpu.hint")}
      </p>
    </div>
  );
}
