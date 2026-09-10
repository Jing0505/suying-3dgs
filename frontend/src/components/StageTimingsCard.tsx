import { useTranslation } from "react-i18next";
import {
  UploadCloud,
  Cpu,
  Brain,
  Boxes,
  DownloadCloud,
  CloudUpload,
  Clock,
  Loader2,
} from "lucide-react";
import type { StageTimings, TaskStatus } from "../types";

interface StageTimingsCardProps {
  timings?: StageTimings;
  totalDuration?: number | null;
  currentStatus: TaskStatus;
}

// 阶段定义：key / 图标 / 对应的运行中状态
const STAGES = [
  { key: "upload", icon: UploadCloud, runningStatus: "uploading" as const },
  { key: "convert", icon: Cpu, runningStatus: "converting" as const },
  { key: "train", icon: Brain, runningStatus: "training" as const },
  { key: "render", icon: Boxes, runningStatus: "rendering" as const },
  { key: "download", icon: DownloadCloud, runningStatus: "downloading" as const },
  { key: "oss_upload", icon: CloudUpload, runningStatus: "oss_uploading" as const },
];

function formatDuration(seconds: number): string {
  if (seconds < 1) return `${(seconds * 1000).toFixed(0)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m ${s}s`;
}

export default function StageTimingsCard({
  timings,
  totalDuration,
  currentStatus,
}: StageTimingsCardProps) {
  const { t } = useTranslation();

  // 没有任何耗时数据，直接不显示
  const hasStageData =
    timings && Object.keys(timings).length > 0;
  if (!hasStageData && totalDuration == null) {
    return null;
  }

  return (
    <div className="gold-card p-6">
      <div className="mb-5 flex items-center gap-2 border-b border-gold-400/15 pb-4">
        <Clock className="h-4 w-4 text-gold-400" />
        <h2 className="font-mono text-xs uppercase tracking-[0.2em] text-gold-300">
          [ {t("detail.timings.title")} ]
        </h2>
        <span className="ml-auto font-mono text-[10px] uppercase tracking-wider text-gray-600">
          {t("detail.timings.subtitle")}
        </span>
      </div>

      {hasStageData ? (
        <div className="divide-y divide-gold-400/10 border border-gold-400/10">
          {STAGES.map(({ key, icon: Icon, runningStatus }) => {
            const timing = timings?.[key];
            const isRunning = currentStatus === runningStatus;
            const isCompleted = timing?.duration !== undefined;

            return (
              <div
                key={key}
                className="flex items-center gap-3 bg-ink-300/60 px-4 py-2.5"
              >
                <Icon
                  className={`h-3.5 w-3.5 shrink-0 ${
                    isCompleted
                      ? "text-gold-400"
                      : isRunning
                        ? "text-gold-400/70"
                        : "text-gray-600"
                  }`}
                />
                <span className="flex-1 font-mono text-[11px] uppercase tracking-wider text-gray-300">
                  {t(`detail.timings.${key}`)}
                </span>
                {isCompleted ? (
                  <span className="font-mono text-sm font-medium text-gold-300">
                    {formatDuration(timing!.duration!)}
                  </span>
                ) : isRunning ? (
                  <span className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-gold-400/70">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    {t("detail.timings.running")}
                  </span>
                ) : (
                  <span className="font-mono text-[10px] uppercase tracking-wider text-gray-600">
                    {t("detail.timings.pending")}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="border border-gold-400/10 bg-ink-300/60 px-4 py-3 font-mono text-[11px] text-gray-500">
          {t("detail.timings.fallbackHint")}
        </div>
      )}

      {/* 端到端总计 */}
      {totalDuration != null && (
        <div className="mt-4 flex items-center justify-between border-t border-gold-400/25 pt-4">
          <span className="font-mono text-xs uppercase tracking-[0.2em] text-gold-200">
            &gt;&gt; {t("detail.timings.total")}
          </span>
          <span className="font-mono text-lg font-bold tracking-tight text-gold-300">
            {formatDuration(totalDuration)}
          </span>
        </div>
      )}
    </div>
  );
}
