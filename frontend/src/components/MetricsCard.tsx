import { useTranslation } from "react-i18next";
import { Gauge, TrendingUp, TrendingDown, Layers } from "lucide-react";
import type { ReconstructionMetrics } from "../types";

interface MetricsCardProps {
  metrics?: ReconstructionMetrics;
}

/**
 * 重建质量指标卡片：展示 PSNR / SSIM / LPIPS。
 *
 * - PSNR：峰值信噪比，越高越好（单位 dB，典型范围 20-40）
 * - SSIM：结构相似性，越高越好（范围 0-1，越接近 1 越好）
 * - LPIPS：感知差异，越低越好（典型范围 0-0.5）
 */
export default function MetricsCard({ metrics }: MetricsCardProps) {
  const { t } = useTranslation();

  const hasData =
    metrics &&
    (metrics.psnr != null || metrics.ssim != null || metrics.lpips != null);

  if (!hasData) {
    return null;
  }

  const psnr = metrics.psnr;
  const ssim = metrics.ssim;
  const lpips = metrics.lpips;

  const items = [
    {
      key: "psnr",
      icon: Gauge,
      value: psnr != null ? psnr.toFixed(4) : "N/A",
      unit: "dB",
      hint: t("detail.metrics.psnrHint"),
      trend: "up" as const,
    },
    {
      key: "ssim",
      icon: TrendingUp,
      value: ssim != null ? ssim.toFixed(4) : "N/A",
      unit: "",
      hint: t("detail.metrics.ssimHint"),
      trend: "up" as const,
    },
    {
      key: "lpips",
      icon: TrendingDown,
      value: lpips != null ? lpips.toFixed(4) : "N/A",
      unit: "",
      hint: t("detail.metrics.lpipsHint"),
      trend: "down" as const,
    },
  ];

  return (
    <div className="gold-card p-6">
      <div className="mb-5 flex items-center gap-2 border-b border-gold-400/15 pb-4">
        <Layers className="h-4 w-4 text-gold-400" />
        <h2 className="font-mono text-xs uppercase tracking-[0.2em] text-gold-300">
          [ {t("detail.metrics.title")} ]
        </h2>
        <span className="ml-auto font-mono text-[10px] uppercase tracking-wider text-gray-600">
          {t("detail.metrics.subtitle")}
        </span>
      </div>

      <div className="grid grid-cols-1 gap-px bg-gold-400/15 sm:grid-cols-3">
        {items.map(({ key, icon: Icon, value, unit, hint, trend }) => (
          <div
            key={key}
            className="group relative bg-ink-300 px-4 py-3.5"
            title={hint}
          >
            <div className="flex items-center gap-2">
              <Icon className="h-3.5 w-3.5 text-gold-400/80" />
              <span className="font-mono text-[10px] uppercase tracking-wider text-gray-400">
                {t(`detail.metrics.${key}`)}
              </span>
              <span className="ml-auto font-mono text-[10px] text-emerald-400/70">
                {trend === "up" ? "↑" : "↓"}
              </span>
            </div>
            <div className="mt-2 flex items-baseline gap-1">
              <span className="font-mono text-2xl font-bold tracking-tight text-gold-300">
                {value}
              </span>
              {unit && (
                <span className="font-mono text-[10px] text-gray-500">{unit}</span>
              )}
            </div>
            <p className="mt-1 text-[10px] leading-snug text-gray-500">
              {hint}
            </p>
          </div>
        ))}
      </div>

      {metrics.source && (
        <p className="mt-3 truncate font-mono text-[10px] uppercase tracking-wider text-gray-600">
          {t("detail.metrics.source")}:{" "}
          <span className="text-gold-400/70">{metrics.source}</span>
        </p>
      )}
    </div>
  );
}
