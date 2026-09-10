import type { TaskStatus } from "../types";
import { useTranslation } from "react-i18next";

// brutalist + taste-skill 颜色一致性锁：
// - 主 accent 为金色（brand），所有运行中状态用金色调
// - emerald 仅用于「完成」单一语义状态
// - red 仅用于「失败」hazard
// - gray 用于 pending
const STATUS_CONFIG: Record<
  TaskStatus,
  { color: string; labelKey: string }
> = {
  pending:     { color: "text-gray-400 bg-gray-400/5 border-gray-500/40",                      labelKey: "detail.stage.pending" },
  uploading:   { color: "text-gold-300 bg-gold-400/10 border-gold-400/40",                     labelKey: "detail.stage.uploading" },
  converting:  { color: "text-gold-300 bg-gold-400/10 border-gold-400/40",                     labelKey: "detail.stage.converting" },
  training:    { color: "text-gold-300 bg-gold-400/10 border-gold-400/40",                     labelKey: "detail.stage.training" },
  rendering:   { color: "text-gold-300 bg-gold-400/10 border-gold-400/40",                     labelKey: "detail.stage.rendering" },
  downloading: { color: "text-gold-300 bg-gold-400/10 border-gold-400/40",                     labelKey: "detail.stage.downloading" },
  oss_uploading: { color: "text-gold-300 bg-gold-400/10 border-gold-400/40",                   labelKey: "detail.stage.oss_uploading" },
  completed:   { color: "text-emerald-300 bg-emerald-400/10 border-emerald-400/50",            labelKey: "detail.stage.completed" },
  failed:      { color: "text-red-300 bg-red-500/10 border-red-500/50",                        labelKey: "detail.stage.failed" },
};

export default function StatusBadge({ status }: { status: TaskStatus }) {
  const { t } = useTranslation();
  const config = STATUS_CONFIG[status];
  const isActive = !["pending", "completed", "failed"].includes(status);

  return (
    <span
      className={`inline-flex items-center gap-1.5 border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider ${config.color}`}
    >
      <span
        className={`h-1.5 w-1.5 ${isActive ? "bg-gold-400 animate-pulse" : status === "completed" ? "bg-emerald-400" : status === "failed" ? "bg-red-500" : "bg-gray-500"}`}
      />
      {t(config.labelKey)}
    </span>
  );
}
