import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  CheckCircle2,
  Loader2,
  Circle,
  XCircle,
  Upload,
  Cpu,
  Brain,
  Layers,
  Download,
  Eye,
  CloudUpload,
} from "lucide-react";
import type { TaskInfo, TaskStatus } from "../types";
import { computeStableProgress, clampProgress } from "../lib/progress";

/**
 * 稳定进度：把 worker 上报的「阶段内进度」映射为「全局进度区间」，
 * 保证单调递增、永不后退、失败保持失败前位置。
 * 详见 lib/progress.ts。
 */
function useStableProgress(task: TaskInfo): number {
  const maxRef = useRef<number>(0);
  // 任务切换：若 task_id 变了则重置（同一组件被复用查看不同任务的场景）
  const lastIdRef = useRef<string>("");
  if (lastIdRef.current !== task.task_id) {
    lastIdRef.current = task.task_id;
    maxRef.current = 0;
  }
  const next = computeStableProgress(task.status, task.progress, maxRef.current);
  maxRef.current = next;
  return next;
}

/**
 * 平滑动画：把目标值在 duration 毫秒内用 easeOutCubic 缓动逼近，
 * 让进度条数字和宽度「一点点推进」，而不是瞬间跳变。
 * 每次目标变化从当前显示值继续插值，中途被打断也不突兀。
 */
function useSmoothNumber(target: number, duration = 600): number {
  const [display, setDisplay] = useState(target);
  const displayRef = useRef(target);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    const from = displayRef.current;
    const to = target;
    if (Math.abs(from - to) < 0.1) return;
    const startAt = performance.now();
    const tick = (now: number) => {
      const k = Math.min(1, (now - startAt) / duration);
      const eased = 1 - Math.pow(1 - k, 3); // easeOutCubic
      const v = from + (to - from) * eased;
      displayRef.current = v;
      setDisplay(v);
      if (k < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [target, duration]);

  return display;
}

/**
 * 解析后端推送的 current_step。
 * 后端格式:
 *   - "i18n:step.uploading|89"  → t("step.uploading", { count: 89 })
 *   - "i18n:step.completed"     → t("step.completed")
 *   - "i18n:step.failed|<msg>"  → t("step.failed", { message: msg })
 *   - "i18n:step.training|done" → t("step.trainDone")（调试模式标记）
 *   - 旧任务历史中文原文 → 走 FALLBACK_MAP 翻译成当前语言
 *   - 其他原文                   → 原样返回
 */
const FALLBACK_MAP: Record<string, string> = {
  "图片上传完成": "step.uploadDone",
  "正在上传图片到 3090 服务器": "step.uploading",
  "正在上传": "step.uploading",
  "正在进行特征提取、特征匹配与三角化重建": "step.converting",
  "特征提取与重建完成": "step.convertDone",
  "正在进行 3DGS 模型训练（高斯泼溅）": "step.training",
  "模型训练完成": "step.trainDone",
  "正在渲染数据集": "step.rendering",
  "渲染完成": "step.renderDone",
  "正在查找并下载生成的 ply 模型": "step.downloading",
  "模型下载完成": "step.downloadDone",
  "3DGS 重建全部完成，模型可查看": "step.completed",
  "重建完成，模型可查看": "step.completed",
  "[调试模式] 3DGS 重建完成（模拟）": "step.debugCompleted",
};

function parseStep(raw: string, t: (key: string, opts?: any) => string): string {
  if (!raw) return raw;
  // 新格式：i18n: 前缀
  if (raw.startsWith("i18n:")) {
    const body = raw.slice(5);
    const [key, ...rest] = body.split("|");
    const arg = rest.join("|");
    if (arg === "done") {
      const doneKey = key.replace(/ing$/, "Done").replace(/^(step\.)uploading$/, "$1uploadDone");
      return t(doneKey) || raw;
    }
    if (key === "step.uploading") {
      return t(key, { count: arg || 0 });
    }
    if (key === "step.failed") {
      return t(key, { message: arg || "" });
    }
    return t(key) || raw;
  }
  // 旧格式：历史中文原文 → fallback 映射
  const fallbackKey = FALLBACK_MAP[raw];
  if (fallbackKey) {
    return t(fallbackKey);
  }
  // 失败消息带参数：重建失败：xxx
  if (raw.startsWith("重建失败：")) {
    return t("step.failed", { message: raw.slice(5) });
  }
  if (raw.startsWith("重建失败:")) {
    return t("step.failed", { message: raw.slice(5) });
  }
  return raw;
}

const STAGES: { key: TaskStatus; icon: typeof Upload; labelKey: string }[] = [
  { key: "uploading", icon: Upload, labelKey: "detail.stage.uploading" },
  { key: "converting", icon: Cpu, labelKey: "detail.stage.converting" },
  { key: "training", icon: Brain, labelKey: "detail.stage.training" },
  { key: "rendering", icon: Eye, labelKey: "detail.stage.rendering" },
  { key: "downloading", icon: Download, labelKey: "detail.stage.downloading" },
  { key: "oss_uploading", icon: CloudUpload, labelKey: "detail.stage.oss_uploading" },
];

const STAGE_ORDER: TaskStatus[] = [
  "pending",
  "uploading",
  "converting",
  "training",
  "rendering",
  "downloading",
  "oss_uploading",
  "completed",
];

function getStageState(
  stage: TaskStatus,
  current: TaskStatus
): "pending" | "active" | "done" | "failed" {
  if (current === "failed") {
    const currentIdx = STAGE_ORDER.indexOf(current);
    const stageIdx = STAGE_ORDER.indexOf(stage);
    return stageIdx < currentIdx ? "done" : "pending";
  }
  const currentIdx = STAGE_ORDER.indexOf(current);
  const stageIdx = STAGE_ORDER.indexOf(stage);
  if (stageIdx < currentIdx) return "done";
  if (stageIdx === currentIdx) return "active";
  return "pending";
}

export default function ProgressTracker({ task }: { task: TaskInfo }) {
  const { t } = useTranslation();
  const logRef = useRef<HTMLDivElement>(null);
  const stable = useStableProgress(task);
  const smooth = useSmoothNumber(stable, 600);
  // 显示用整数（如 8%），宽度用连续值（如 8.3%）保证平滑
  const progress = clampProgress(smooth);
  const progressWidth = Math.max(0, Math.min(100, smooth));

  // 自动滚动到最新日志
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [task.log_lines]);

  return (
    <div className="space-y-6">
      {/* 整体进度条 */}
      <div>
        <div className="mb-2 flex items-center justify-between border-b border-gold-400/15 pb-2">
          <span className="font-mono text-xs uppercase tracking-[0.2em] text-gold-300">
            [ {t("detail.progress")} ]
          </span>
          <span className="font-mono text-3xl font-bold tracking-tight text-gold-300">
            {progress}<span className="text-base text-gold-400/60">%</span>
          </span>
        </div>
        <div className="h-2 w-full bg-ink-400 border border-bronze-400/30">
          <div
            className="h-full bg-gold-400 transition-none"
            style={{ width: `${progressWidth}%` }}
          >
            {/* 科技蓝流光点缀：表示数据正在流动 */}
            <div className="h-full w-full opacity-60" style={{
              backgroundImage: "linear-gradient(90deg, transparent 0%, rgba(56,189,248,0.5) 50%, transparent 100%)",
              backgroundSize: "200% 100%",
              animation: "progress-shine 2s linear infinite",
            }} />
          </div>
        </div>
        <p className="mt-2 font-mono text-[11px] uppercase tracking-wider text-gray-400">
          &gt;&gt; {parseStep(task.current_step, t)}
        </p>
      </div>

      {/* 阶段时间线 */}
      <div>
        <h3 className="mb-3 font-mono text-xs uppercase tracking-[0.2em] text-gold-300">
          [ {t("detail.status")} ]
        </h3>
        <div className="grid grid-cols-1 gap-px bg-gold-400/15 sm:grid-cols-6">
          {STAGES.map(({ key, icon: Icon, labelKey }) => {
            const state = getStageState(key, task.status);
            return (
              <div
                key={key}
                className={`flex flex-col items-center gap-2 border p-4 transition-colors ${
                  state === "active"
                    ? "border-gold-400 bg-gold-400/10"
                    : state === "done"
                    ? "border-emerald-400/30 bg-emerald-400/5"
                    : "border-transparent bg-ink-300/60"
                }`}
              >
                {state === "active" ? (
                  <Loader2 className="h-5 w-5 animate-spin text-gold-400" />
                ) : state === "done" ? (
                  <CheckCircle2 className="h-5 w-5 text-emerald-400" />
                ) : (
                  <Icon className="h-5 w-5 text-gray-600" />
                )}
                <span
                  className={`text-center font-mono text-[10px] uppercase tracking-wider ${
                    state === "active"
                      ? "text-gold-300"
                      : state === "done"
                      ? "text-emerald-300"
                      : "text-gray-600"
                  }`}
                >
                  {t(labelKey)}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* 失败信息 */}
      {task.status === "failed" && task.error && (
        <div className="flex items-start gap-3 border border-red-500/50 bg-red-500/10 p-4">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
          <div className="min-w-0 flex-1">
            <p className="font-mono text-xs uppercase tracking-wider text-red-300">
              [ {t("detail.stage.failed")} ]
            </p>
            {(() => {
              // 后端 new 格式：「阶段X 阶段名」 具体错误描述
              // 旧格式：直接错误描述
              const m = task.error.match(/^「([^」]+)」\s*(.*)$/s);
              if (m) {
                return (
                  <div className="mt-2">
                    <span className="inline-flex items-center border border-gold-400/60 bg-gold-400/10 px-2 py-0.5 font-mono text-[11px] text-gold-200">
                      {m[1]}
                    </span>
                    <p className="mt-2 whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed text-red-300/90">
                      {m[2]}
                    </p>
                  </div>
                );
              }
              return (
                <p className="mt-2 whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed text-red-400/80">
                  {task.error}
                </p>
              );
            })()}
          </div>
        </div>
      )}

      {/* 实时日志 */}
      <div>
        <h3 className="mb-2 flex items-center gap-2 font-mono text-xs uppercase tracking-[0.2em] text-gold-300">
          <span className="h-1.5 w-1.5 bg-tech-400 animate-pulse" />
          [ {t("detail.logs")} ]
        </h3>
        <div
          ref={logRef}
          className="crt-scanlines h-64 overflow-y-auto border border-bronze-400/30 bg-ink-900 p-4 font-mono text-[11px] leading-relaxed"
        >
          {task.log_lines.length === 0 ? (
            <p className="text-warm-600">
              <span className="text-tech-400">$</span> 等待日志输出
              <span className="blink text-tech-400">_</span>
            </p>
          ) : (
            task.log_lines.map((line, idx) => (
              <div
                key={idx}
                className={`relative z-10 whitespace-pre-wrap break-all ${
                  line.includes("[ERROR]") || line.toLowerCase().includes("error")
                    ? "text-red-400"
                    : line.includes("iteration")
                    ? "text-gold-300"
                    : "text-gray-400"
                }`}
              >
                <span className="mr-2 select-none text-gray-700">
                  {String(idx + 1).padStart(4, "0")}
                </span>
                {line}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
