import { useEffect, useState, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Plus,
  Trash2,
  ChevronRight,
  ImageIcon,
  Inbox,
  RefreshCw,
  Loader2,
  Clock,
  Search,
  X,
  List,
} from "lucide-react";
import { listTasks, deleteTask } from "../api/client";
import StatusBadge from "../components/StatusBadge";
import type { TaskInfo } from "../types";
import { computeStableProgress } from "../lib/progress";
import {
  useOssUploadQueue,
  type OssUploadJob,
} from "../ossUploadQueue";

/** 每个任务独立记录已达到的最大进度（模块级，跨页面挂载保持）：保证单调递增 */
const progressMaxRef: Record<string, number> = {};

/** 根据 task 计算稳定进度值：阶段区间映射 + 永不后退（与详情页 ProgressTracker 一致）。 */
function getStableProgress(task: TaskInfo): number {
  const prev = progressMaxRef[task.task_id] ?? 0;
  const next = computeStableProgress(task.status, task.progress, prev);
  progressMaxRef[task.task_id] = next;
  return next;
}

function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDuration(seconds: number): string {
  if (seconds < 1) return `${(seconds * 1000).toFixed(0)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m ${s}s`;
}

function jobHint(
  job: OssUploadJob,
  t: (key: string, opts?: Record<string, string | number>) => string
): string {
  if (job.phase === "extract") return t("upload.extractingZip");
  if (job.phase === "create") return t("upload.creatingTask");
  if (job.phase === "failed") return job.error || t("tasks.ossFailed");
  return t("upload.directProgress", { current: job.done, total: job.total });
}

export default function Tasks() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { jobs, dismiss, completedTick } = useOssUploadQueue();
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [keyword, setKeyword] = useState("");

  const fetchTasks = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setLoading(true);
    setError("");
    try {
      const data = await listTasks();
      setTasks(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      if (!opts?.silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTasks();
  }, [fetchTasks]);

  useEffect(() => {
    if (completedTick > 0) {
      void fetchTasks({ silent: true });
    }
  }, [completedTick, fetchTasks]);

  const handleDelete = async (taskId: string, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm(t("tasks.confirmDelete"))) return;
    try {
      await deleteTask(taskId);
      setTasks((prev) => prev.filter((t) => t.task_id !== taskId));
    } catch (err) {
      alert(err instanceof Error ? err.message : "删除失败");
    }
  };

  // 按任务编号或重建编号过滤（不区分大小写，匹配子串）
  const trimmed = keyword.trim().toLowerCase();
  const filteredTasks = trimmed
    ? tasks.filter(
        (task) =>
          task.task_id.toLowerCase().includes(trimmed) ||
          task.reconstruction_id.toLowerCase().includes(trimmed),
      )
    : tasks;
  const filteredJobs = trimmed
    ? jobs.filter((job) =>
        (job.reconstructionId || "").toLowerCase().includes(trimmed),
      )
    : jobs;
  const isSearching = trimmed.length > 0;
  const hasList = filteredJobs.length > 0 || filteredTasks.length > 0;
  const showEmpty = !loading && tasks.length === 0 && jobs.length === 0;

  const inFlightJobs = jobs.filter((j) => j.phase !== "failed").length;
  const failedJobs = jobs.filter((j) => j.phase === "failed").length;

  // 队列统计（拉模式：pending=等待 3090 领取，running=处理中）
  const queueStats = {
    pending: tasks.filter((t) => t.status === "pending").length,
    running:
      tasks.filter(
        (t) => !["pending", "completed", "failed"].includes(t.status),
      ).length + inFlightJobs,
    completed: tasks.filter((t) => t.status === "completed").length,
    failed: tasks.filter((t) => t.status === "failed").length + failedJobs,
  };
  const STAT_CARDS: {
    key: "pending" | "running" | "completed" | "failed";
    labelKey: string;
    color: string;
    border: string;
  }[] = [
    {
      key: "pending",
      labelKey: "tasks.queue.pending",
      color: "text-gray-300",
      border: "border-gray-500/40",
    },
    {
      key: "running",
      labelKey: "tasks.queue.running",
      color: "text-gold-300",
      border: "border-gold-400/50",
    },
    {
      key: "completed",
      labelKey: "tasks.queue.completed",
      color: "text-emerald-300",
      border: "border-emerald-400/50",
    },
    {
      key: "failed",
      labelKey: "tasks.queue.failed",
      color: "text-red-300",
      border: "border-red-500/50",
    },
  ];

  return (
    <div className="mx-auto max-w-5xl px-4 py-12 sm:px-6">
      {/* 标题栏 */}
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-gold-300">{t("tasks.title")}</h1>
          <p className="mt-1 text-sm text-gray-400">{t("tasks.subtitle")}</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => fetchTasks()}
            className="gold-btn-outline"
            disabled={loading}
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
          </button>
          <button
            onClick={() => navigate("/upload")}
            className="gold-btn"
          >
            <Plus className="h-4 w-4" />
            {t("tasks.newTask")}
          </button>
        </div>
      </div>

      {/* 错误提示 */}
      {error && (
        <div className="mb-4  border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* 队列统计面板 */}
      {!loading && (tasks.length > 0 || jobs.length > 0) && (
        <div className="mb-6 grid grid-cols-2 gap-px border border-bronze-400/30 bg-bronze-400/20 sm:grid-cols-4">
          {STAT_CARDS.map(({ key, labelKey, color, border }) => (
            <div
              key={key}
              className={`flex flex-col items-center gap-1 bg-ink-300/80 px-4 py-3 ${border}`}
            >
              <span className={`font-mono text-2xl font-bold ${color}`}>
                {queueStats[key]}
              </span>
              <span className="font-mono text-[10px] uppercase tracking-wider text-gray-500">
                {t(labelKey)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 搜索框 - brutalist 终端风格 */}
      {!loading && (tasks.length > 0 || jobs.length > 0) && (
        <div className="mb-6 border border-bronze-400/30 bg-ink-300/60">
          <div className="flex items-center">
            <span className="flex h-10 items-center border-r border-bronze-400/30 px-3 text-bronze-400">
              <List className="h-4 w-4" />
            </span>
            <Search className="ml-3 h-4 w-4 text-bronze-400" />
            <input
              type="text"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder={t("tasks.searchPlaceholder")}
              className="h-10 flex-1 bg-transparent px-3 font-mono text-sm text-warm placeholder:text-gray-600 focus:outline-none"
            />
            {isSearching && (
              <span className="flex items-center gap-2 border-l border-bronze-400/30 px-3">
                <span className="font-mono text-[10px] uppercase tracking-wider text-gold-400/70">
                  {t("tasks.searchResults", {
                    count: filteredTasks.length + filteredJobs.length,
                  })}
                </span>
                <button
                  onClick={() => setKeyword("")}
                  className="text-gray-500 transition-colors hover:text-red-400"
                  title={t("tasks.clearSearch")}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </span>
            )}
          </div>
        </div>
      )}

      {/* 任务列表 */}
      {loading && jobs.length === 0 ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-8 w-8 animate-spin text-gold-400" />
        </div>
      ) : showEmpty ? (
        <div className="flex flex-col items-center justify-center gap-4 py-20">
          <Inbox className="h-16 w-16 text-gray-600" />
          <p className="text-gray-500">{t("tasks.empty")}</p>
          <Link to="/upload" className="gold-btn">
            <Plus className="h-4 w-4" />
            {t("tasks.newTask")}
          </Link>
        </div>
      ) : isSearching && !hasList ? (
        <div className="flex flex-col items-center justify-center gap-3 py-20">
          <Search className="h-12 w-12 text-bronze-400/50" />
          <p className="font-mono text-sm text-gray-500">
            {t("tasks.searchEmpty", { keyword })}
          </p>
          <button
            onClick={() => setKeyword("")}
            className="gold-btn-outline"
          >
            <X className="h-4 w-4" />
            {t("tasks.clearSearch")}
          </button>
        </div>
      ) : (
        <div className="gold-card overflow-hidden">
          {/* 表头 - brutalist mono */}
          <div className="grid grid-cols-[2fr_1.5fr_0.5fr_0.8fr_1fr_0.8fr_1.2fr_0.5fr] items-center gap-4 border-b border-bronze-400/30 bg-ink-400/60 px-5 py-3 font-mono text-[10px] uppercase tracking-wider text-gray-500">
            <span>{t("detail.reconId")}</span>
            <span>{t("detail.taskId")}</span>
            <span className="text-center">IMG</span>
            <span>{t("detail.status")}</span>
            <span className="hidden sm:block">{t("detail.progress")}</span>
            <span className="text-center">{t("detail.timings.total")}</span>
            <span className="hidden md:block">{t("detail.created")}</span>
            <span className="text-right">{t("tasks.delete")}</span>
          </div>

          {filteredJobs.map((job) => {
            const pct =
              job.total > 0
                ? Math.min(100, Math.round((job.done / job.total) * 100))
                : 0;
            return (
              <div
                key={job.localId}
                className="grid grid-cols-[2fr_1.5fr_0.5fr_0.8fr_1fr_0.8fr_1.2fr_0.5fr] items-center gap-4 border-b border-bronze-400/10 bg-gold-400/5 px-5 py-3.5"
              >
                <span className="min-w-0">
                  <span className="block truncate font-mono text-sm font-semibold text-gold-300">
                    {job.reconstructionId || t("upload.confirmReconIdAuto")}
                  </span>
                  <span className="mt-0.5 block font-mono text-[9px] text-gray-400 sm:hidden">
                    {jobHint(job, t)}
                  </span>
                </span>
                <span className="truncate font-mono text-xs text-gray-500">
                  {t("tasks.ossPendingId")}
                </span>
                <span className="text-center font-mono text-xs text-warm-600">
                  {job.imageCount || "—"}
                </span>
                <StatusBadge
                  status={job.phase === "failed" ? "failed" : "uploading"}
                />
                <div className="hidden sm:flex flex-col gap-1">
                  <div className="h-1 w-full bg-ink-400 border border-bronze-400/20">
                    <div
                      className="h-full bg-gold-400 transition-all duration-300"
                      style={{ width: `${job.phase === "failed" ? 0 : pct}%` }}
                    />
                  </div>
                  <span className="font-mono text-[9px] text-gray-400">
                    {jobHint(job, t)}
                  </span>
                </div>
                <span className="text-center font-mono text-xs text-gold-400/80">
                  N/A
                </span>
                <span className="hidden md:block font-mono text-[10px] text-warm-600">
                  {formatDate(job.createdAt)}
                </span>
                <span className="flex justify-end">
                  {job.phase === "failed" ? (
                    <button
                      type="button"
                      onClick={() => dismiss(job.localId)}
                      className="p-1.5 text-gray-500 transition-colors hover:bg-red-400/10 hover:text-red-400"
                      title={t("tasks.ossDismiss")}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  ) : (
                    <span className="p-1.5 text-gray-700">
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-gold-400" />
                    </span>
                  )}
                </span>
              </div>
            );
          })}

          {filteredTasks.map((task, idx) => {
            const stableProgress = getStableProgress(task);
            return (
            <Link
              key={task.task_id}
              to={`/tasks/${task.task_id}`}
              className="group grid grid-cols-[2fr_1.5fr_0.5fr_0.8fr_1fr_0.8fr_1.2fr_0.5fr] items-center gap-4 border-b border-bronze-400/10 px-5 py-3.5 transition-colors hover:bg-gold-400/5 animate-fade-up"
              style={{ animationDelay: `${idx * 0.03}s` }}
            >
              {/* 重建编号 */}
              <span className="truncate font-mono text-sm font-semibold text-gold-300">
                {task.reconstruction_id}
              </span>

              {/* 任务编号 */}
              <span className="truncate font-mono text-xs text-gray-500">
                {task.task_id}
              </span>

              {/* 图片数 */}
              <span className="text-center font-mono text-xs text-warm-600">
                {task.image_count}
              </span>

              {/* 状态 */}
              <StatusBadge status={task.status} />

              {/* 进度 */}
              <div className="hidden sm:flex flex-col gap-1">
                <div className="h-1 w-full bg-ink-400 border border-bronze-400/20">
                  <div
                    className="h-full bg-gold-400 transition-all duration-500"
                    style={{ width: `${stableProgress}%` }}
                  />
                </div>
                <span className="font-mono text-[9px] text-gray-600">{stableProgress}%</span>
              </div>

              {/* 耗时 */}
              <span className="text-center font-mono text-xs text-gold-400/80">
                {task.total_duration != null
                  ? formatDuration(task.total_duration)
                  : "N/A"}
              </span>

              {/* 创建时间 */}
              <span className="hidden md:block font-mono text-[10px] text-warm-600">
                {formatDate(task.created_at)}
              </span>

              {/* 操作 */}
              <span className="flex justify-end">
                <button
                  onClick={(e) => handleDelete(task.task_id, e)}
                  className="p-1.5 text-gray-500 transition-colors hover:bg-red-400/10 hover:text-red-400"
                  title={t("tasks.delete")}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </span>
            </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
