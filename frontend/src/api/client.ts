import axios, { AxiosError } from "axios";
import JSZip from "jszip";
import type {
  AuthUser,
  CreateTaskResponse,
  HealthResponse,
  OssUploadPolicy,
  TaskInfo,
  TrainParams,
} from "../types";

const TOKEN_KEY = "suying_token";

const apiBase = (() => {
  const backend = (import.meta.env.VITE_BACKEND_URL as string | undefined)?.trim();
  if (backend) {
    return `${backend.replace(/\/$/, "")}/api`;
  }
  return "/api";
})();

const api = axios.create({
  baseURL: apiBase,
  timeout: 60000,
});

let authToken: string | null =
  typeof localStorage !== "undefined" ? localStorage.getItem(TOKEN_KEY) : null;

export function setAuthToken(token: string | null): void {
  authToken = token;
}

function statusFallback(status: number | undefined): string {
  if (status === 400) return "请求参数无效";
  if (status === 401) return "登录已过期，请重新登录";
  if (status === 403) return "没有权限执行此操作";
  if (status === 404) return "请求的资源不存在";
  if (status === 413) return "上传内容过大";
  if (status === 415) return "不支持的提交格式";
  if (status === 429) return "请求过于频繁，请稍后重试";
  if (status === 502 || status === 503 || status === 504) {
    return "后端无法连接，请确认 8010 服务已启动";
  }
  if (status === 500) return "服务器内部错误，请稍后重试";
  return "请求失败";
}

function extractDetail(err: unknown): string {
  const ax = err as AxiosError<{ detail?: unknown }>;
  const detail = ax.response?.data?.detail;
  if (typeof detail === "string" && detail.trim() && detail !== "Internal Server Error") {
    return detail;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((item) =>
        typeof item === "object" && item && "msg" in item
          ? String((item as { msg: string }).msg)
          : String(item)
      )
      .join("; ");
  }

  const status = ax.response?.status;
  const code = ax.code || "";
  const raw = `${code} ${ax.message || ""}`.toLowerCase();
  const body = ax.response?.data;
  const noJsonDetail =
    !ax.response ||
    body == null ||
    typeof body === "string" ||
    (typeof body === "object" && !("detail" in (body as object)));
  const timedOut =
    code === "ECONNABORTED" ||
    raw.includes("timeout of") ||
    raw.includes("ms exceeded");
  const disconnected =
    !ax.response ||
    timedOut ||
    code === "ERR_NETWORK" ||
    raw.includes("econnreset") ||
    raw.includes("econnrefused") ||
    raw.includes("network error") ||
    status === 502 ||
    status === 503 ||
    status === 504 ||
    (status === 500 && noJsonDetail);

  if (timedOut) {
    return "请求超时，请确认 8010 已启动";
  }
  if (disconnected) {
    return "后端无法连接，请确认 8010 服务已启动";
  }

  const axiosDefault = /request failed with status code/i.test(ax.message || "");
  if (status === 500 || axiosDefault) {
    return statusFallback(status);
  }
  const mapped = statusFallback(status);
  if (mapped !== "请求失败") return mapped;
  if (err instanceof Error && !axiosDefault && err.message.trim()) {
    return err.message;
  }
  return "请求失败";
}

api.interceptors.request.use((config) => {
  if (authToken) {
    config.headers.Authorization = `Bearer ${authToken}`;
  }
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (err: AxiosError) => {
    if (err.response?.status === 401) {
      const url = err.config?.url || "";
      if (!url.includes("/auth/login")) {
        localStorage.removeItem(TOKEN_KEY);
        authToken = null;
        if (!window.location.pathname.startsWith("/login")) {
          window.location.assign("/login");
        }
      }
    }
    return Promise.reject(new Error(extractDetail(err)));
  }
);

const IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"];

function isImageName(name: string): boolean {
  const lower = name.toLowerCase();
  return IMAGE_EXTS.some((ext) => lower.endsWith(ext));
}

function contentTypeFor(name: string): string {
  const lower = name.toLowerCase();
  if (lower.endsWith(".png")) return "image/png";
  if (lower.endsWith(".bmp")) return "image/bmp";
  if (lower.endsWith(".tif") || lower.endsWith(".tiff")) return "image/tiff";
  return "image/jpeg";
}

function safeFileName(name: string): string {
  return name.replace(/[/\\]/g, "").split("/").pop() || name;
}

/** 健康检查 */
export async function checkHealth(): Promise<HealthResponse> {
  const { data } = await api.get<HealthResponse>("/health", { timeout: 8000 });
  return data;
}

export async function login(
  username: string,
  password: string
): Promise<{ token: string; user: AuthUser }> {
  const { data } = await api.post<{ token: string; user: AuthUser }>(
    "/auth/login",
    { username, password }
  );
  return data;
}

export async function fetchMe(): Promise<AuthUser> {
  const { data } = await api.get<AuthUser>("/auth/me");
  return data;
}

export async function listUsers(): Promise<AuthUser[]> {
  const { data } = await api.get<{ users: AuthUser[] }>("/admin/users");
  return data.users;
}

export async function createUser(
  username: string,
  password: string
): Promise<AuthUser> {
  const { data } = await api.post<AuthUser>("/admin/users", {
    username,
    password,
    role: "user",
  });
  return data;
}

export async function patchUser(
  userId: string,
  disabled: boolean
): Promise<AuthUser> {
  const { data } = await api.patch<AuthUser>(`/admin/users/${userId}`, {
    disabled,
  });
  return data;
}

/** 获取所有任务 */
export async function listTasks(): Promise<TaskInfo[]> {
  const { data } = await api.get<{ tasks: TaskInfo[]; total: number }>("/tasks");
  return data.tasks;
}

/** 获取单个任务详情 */
export async function getTask(taskId: string): Promise<TaskInfo> {
  const { data } = await api.get<TaskInfo>(`/tasks/${taskId}`);
  return data;
}

export async function requestUploadPolicy(
  files: File[],
  reconstructionId?: string
): Promise<OssUploadPolicy> {
  const { data } = await api.post<OssUploadPolicy>(
    "/oss/upload-policy",
    {
      reconstruction_id: reconstructionId || undefined,
      files: files.map((f) => ({ name: f.name, size: f.size })),
    },
    { timeout: 30000 }
  );
  return data;
}

async function postFileToOss(
  file: File,
  policy: OssUploadPolicy
): Promise<string> {
  const key = `${policy.dir}${safeFileName(file.name)}`;
  const form = new FormData();
  form.append("key", key);
  form.append("policy", policy.policy);
  form.append("OSSAccessKeyId", policy.access_key_id);
  form.append("signature", policy.signature);
  form.append("success_action_status", "200");
  form.append("Content-Type", file.type || contentTypeFor(file.name));
  form.append("file", file);

  const res = await fetch(policy.host, { method: "POST", body: form });
  if (!res.ok) {
    throw new Error(`OSS 上传失败（HTTP ${res.status}）: ${file.name}`);
  }
  return `${policy.host.replace(/\/$/, "")}/${key}`;
}

async function mapPool<T, R>(
  items: T[],
  concurrency: number,
  worker: (item: T, index: number) => Promise<R>
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let cursor = 0;
  async function run(): Promise<void> {
    while (cursor < items.length) {
      const idx = cursor++;
      results[idx] = await worker(items[idx], idx);
    }
  }
  const runners = Array.from(
    { length: Math.min(concurrency, items.length) },
    () => run()
  );
  await Promise.all(runners);
  return results;
}

export async function uploadImagesToOss(
  files: File[],
  reconstructionId: string | undefined,
  onProgress?: (done: number, total: number) => void
): Promise<{ reconstruction_id: string; oss_images: string[]; total_bytes: number }> {
  let policy = await requestUploadPolicy(files, reconstructionId);
  let refreshing: Promise<OssUploadPolicy> | null = null;
  const refreshSkewSec = 120;

  async function ensurePolicy(): Promise<OssUploadPolicy> {
    const now = Date.now() / 1000;
    if (now < policy.expire - refreshSkewSec) {
      return policy;
    }
    if (!refreshing) {
      refreshing = requestUploadPolicy(files, policy.reconstruction_id)
        .then((next) => {
          policy = next;
          refreshing = null;
          return next;
        })
        .catch((err) => {
          refreshing = null;
          throw err;
        });
    }
    return refreshing;
  }

  let done = 0;
  const urls = await mapPool(files, 4, async (file) => {
    const current = await ensurePolicy();
    const url = await postFileToOss(file, current);
    done += 1;
    onProgress?.(done, files.length);
    return url;
  });
  return {
    reconstruction_id: policy.reconstruction_id,
    oss_images: urls,
    total_bytes: files.reduce((sum, f) => sum + f.size, 0),
  };
}

export async function extractZipImages(zipFile: File): Promise<File[]> {
  const zip = await JSZip.loadAsync(zipFile);
  const images: File[] = [];
  const entries = Object.values(zip.files);
  for (const entry of entries) {
    if (entry.dir) continue;
    const name = safeFileName(entry.name);
    if (!isImageName(name)) continue;
    const blob = await entry.async("blob");
    images.push(
      new File([blob], name, { type: contentTypeFor(name) })
    );
  }
  return images;
}

/** 创建重建任务（OSS 地址 JSON） */
export async function createTaskFromOss(
  ossImages: string[],
  reconstructionId?: string,
  trainParams?: TrainParams,
  totalBytes?: number
): Promise<CreateTaskResponse> {
  const { data } = await api.post<CreateTaskResponse>(
    "/tasks",
    {
      reconstruction_id: reconstructionId || undefined,
      oss_images: ossImages,
      train_params:
        trainParams && Object.keys(trainParams).length > 0
          ? trainParams
          : undefined,
      total_bytes: totalBytes,
    },
    { timeout: 120000 }
  );
  return data;
}

/** 本地兜底：OSS 未配置时把图片打到后端 */
export async function createTask(
  files: File[],
  reconstructionId?: string,
  trainParams?: TrainParams
): Promise<CreateTaskResponse> {
  const formData = new FormData();
  files.forEach((file) => {
    formData.append("images", file);
  });
  if (reconstructionId) {
    formData.append("reconstruction_id", reconstructionId);
  }
  if (trainParams && Object.keys(trainParams).length > 0) {
    formData.append("train_params", JSON.stringify(trainParams));
  }
  const { data } = await api.post<CreateTaskResponse>("/tasks", formData, {
    timeout: 300000,
  });
  return data;
}

export async function createTaskFromZip(
  zipFile: File,
  reconstructionId?: string,
  trainParams?: TrainParams
): Promise<CreateTaskResponse> {
  const formData = new FormData();
  formData.append("zip_file", zipFile);
  if (reconstructionId) {
    formData.append("reconstruction_id", reconstructionId);
  }
  if (trainParams && Object.keys(trainParams).length > 0) {
    formData.append("train_params", JSON.stringify(trainParams));
  }
  const { data } = await api.post<CreateTaskResponse>("/tasks", formData, {
    timeout: 300000,
  });
  return data;
}

/** 删除任务 */
export async function deleteTask(taskId: string): Promise<void> {
  await api.delete(`/tasks/${taskId}`);
}

/** 获取模型下载 URL（查看器无法带 Header，附带 token） */
export function getModelUrl(taskId: string): string {
  const token = authToken || localStorage.getItem(TOKEN_KEY);
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  return `/api/tasks/${taskId}/model${qs}`;
}

/** 获取静态模型 URL（供在线查看器使用） */
export function getStaticModelUrl(reconstructionId: string): string {
  return `/static/models/${reconstructionId}.ply`;
}

/** 创建 WebSocket 连接订阅任务进度 */
export function createTaskWebSocket(taskId: string): WebSocket {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const token = authToken || localStorage.getItem(TOKEN_KEY);
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  return new WebSocket(
    `${protocol}//${window.location.host}/ws/tasks/${taskId}${qs}`
  );
}
