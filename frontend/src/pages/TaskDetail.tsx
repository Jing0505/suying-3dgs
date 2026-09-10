import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  Hash,
  Calendar,
  Images,
  Loader2,
  AlertCircle,
} from "lucide-react";
import { getTask, createTaskWebSocket } from "../api/client";
import StatusBadge from "../components/StatusBadge";
import ProgressTracker from "../components/ProgressTracker";
import ModelViewer from "../components/ModelViewer";
import StageTimingsCard from "../components/StageTimingsCard";
import MetricsCard from "../components/MetricsCard";
import GpuMemoryCard from "../components/GpuMemoryCard";
import type { TaskInfo } from "../types";

function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString("zh-CN");
}

export default function TaskDetail() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [task, setTask] = useState<TaskInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [wsConnected, setWsConnected] = useState(false);
  const [wsFailed, setWsFailed] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();
  const pollTimer = useRef<ReturnType<typeof setInterval>>();

  // 初始加载
  const fetchTask = useCallback(async () => {
    if (!taskId) return;
    try {
      const data = await getTask(taskId);
      setTask(data);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "任务不存在");
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    fetchTask();
  }, [fetchTask]);

  // WebSocket 实时订阅（失败时降级为 HTTP 轮询）
  useEffect(() => {
    if (!taskId || !task) return;
    // 任务已完成或失败，不需要轮询
    if (task.status === "completed" || task.status === "failed") return;

    // HTTP 轮询降级
    const startPolling = () => {
      if (pollTimer.current) return;
      pollTimer.current = setInterval(async () => {
        try {
          const data = await getTask(taskId);
          setTask(data);
          if (data.status === "completed" || data.status === "failed") {
            if (pollTimer.current) {
              clearInterval(pollTimer.current);
              pollTimer.current = undefined;
            }
          }
        } catch {
          /* 忽略轮询错误 */
        }
      }, 3000);
    };

    if (wsFailed) {
      startPolling();
      return () => {
        if (pollTimer.current) {
          clearInterval(pollTimer.current);
          pollTimer.current = undefined;
        }
      };
    }

    const connect = () => {
      const ws = createTaskWebSocket(taskId);
      wsRef.current = ws;

      ws.onopen = () => {
        setWsConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === "ping") return;
          setTask(data as TaskInfo);
        } catch {
          /* 忽略解析错误 */
        }
      };

      ws.onclose = () => {
        setWsConnected(false);
        // 自动重连（5 秒后）
        reconnectTimer.current = setTimeout(connect, 5000);
      };

      ws.onerror = () => {
        setWsFailed(true);
        ws.close();
      };
    };

    connect();

    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (pollTimer.current) {
        clearInterval(pollTimer.current);
        pollTimer.current = undefined;
      }
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [taskId, task?.status, wsFailed]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32">
        <Loader2 className="h-8 w-8 animate-spin text-gold-400" />
      </div>
    );
  }

  if (error || !task) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-12">
        <div className="flex flex-col items-center gap-4 py-20 text-center">
          <AlertCircle className="h-12 w-12 text-red-400/60" />
          <p className="text-red-300">{error || "任务不存在"}</p>
          <button onClick={() => navigate("/tasks")} className="gold-btn-outline">
            <ArrowLeft className="h-4 w-4" />
            {t("detail.back")}
          </button>
        </div>
      </div>
    );
  }

  const isCompleted = task.status === "completed";

  return (
    <div className="mx-auto max-w-5xl px-4 py-12 sm:px-6">
      {/* 顶部信息栏 */}
      <div className="mb-6">
        <button
          onClick={() => navigate("/tasks")}
          className="mb-4 inline-flex items-center gap-1.5 text-sm text-gray-400 transition-colors hover:text-gold-300"
        >
          <ArrowLeft className="h-4 w-4" />
          {t("detail.back")}
        </button>

        <div className="gold-card p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-gold-300">
                {task.reconstruction_id}
              </h1>
              <div className="mt-2 flex items-center gap-3">
                <StatusBadge status={task.status} />
                {wsConnected && (
                  <span className="flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-tech-400">
                    <span className="h-1.5 w-1.5 animate-pulse bg-tech-400" />
                    实时连接
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* 元信息 */}
          <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div className="flex items-center gap-2">
              <Hash className="h-4 w-4 text-gold-400/60" />
              <div>
                <p className="text-xs text-gray-500">{t("detail.taskId")}</p>
                <p className="text-sm font-mono text-gold-300">{task.task_id}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Images className="h-4 w-4 text-gold-400/60" />
              <div>
                <p className="text-xs text-gray-500">{t("detail.images")}</p>
                <p className="text-sm text-gray-300">{task.image_count}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Calendar className="h-4 w-4 text-gold-400/60" />
              <div>
                <p className="text-xs text-gray-500">{t("detail.created")}</p>
                <p className="text-sm text-gray-300">
                  {formatDate(task.created_at)}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 进度追踪 */}
      <div className="gold-card mb-6 p-6">
        <ProgressTracker task={task} />
      </div>

      {/* 阶段耗时 */}
      <div className="mb-6">
        <StageTimingsCard
          timings={task.stage_timings}
          totalDuration={task.total_duration}
          currentStatus={task.status}
        />
      </div>

      {/* 重建质量指标 */}
      <div className="mb-6">
        <MetricsCard metrics={task.metrics} />
      </div>

      {/* GPU 显存占用（训练阶段采样） */}
      <div className="mb-6">
        <GpuMemoryCard gpu={task.gpu_memory} />
      </div>

      {/* 3D 模型查看 */}
      {isCompleted && (
        <div className="gold-card p-6">
          <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold text-gold-200">
            {t("detail.model")}
          </h2>
          <ModelViewer
            taskId={task.task_id}
            reconstructionId={task.reconstruction_id}
            ossUrl={task.oss_url}
          />
        </div>
      )}
    </div>
  );
}
