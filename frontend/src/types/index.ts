/** 重建任务状态枚举 */
export type TaskStatus =
  | "pending"
  | "uploading"
  | "converting"
  | "training"
  | "rendering"
  | "downloading"
  | "oss_uploading"
  | "completed"
  | "failed";

/** 单个阶段的时间记录 */
export interface StageTiming {
  start?: number;
  end?: number;
  duration?: number;
}

/** 阶段耗时映射，键为 upload/convert/train/render/download */
export type StageTimings = Record<string, StageTiming>;

/** 重建质量指标（PSNR / SSIM / LPIPS） */
export interface ReconstructionMetrics {
  psnr?: number | null;
  ssim?: number | null;
  lpips?: number | null;
  /** 指标来源（results.json 文件路径，含运行键后缀） */
  source?: string;
  /** 是否启用了 eval 模式（评测集分割） */
  eval_mode?: boolean;
}

/** 训练参数（仅包含用户自定义的参数） */
export interface TrainParams {
  // 基础
  iterations?: number;
  resolution?: number;
  sh_degree?: number;
  white_background?: boolean;
  eval?: boolean;
  // 进阶（致密化）
  densify_grad_threshold?: number;
  densification_interval?: number;
  opacity_reset_interval?: number;
  densify_from_iter?: number;
  densify_until_iter?: number;
  // 质量
  lambda_dssim?: number;
  percent_dense?: number;
  // 视觉
  random_background?: boolean;
  antialiasing?: boolean;
}

/** 重建任务完整信息 */
export interface TaskInfo {
  task_id: string;
  reconstruction_id: string;
  status: TaskStatus;
  progress: number;
  current_step: string;
  log_lines: string[];
  created_at: number;
  updated_at: number;
  image_count: number;
  model_path?: string | null;
  remote_output_folder?: string | null;
  error?: string | null;
  /** 各阶段耗时记录 */
  stage_timings?: StageTimings;
  /** 端到端总耗时（秒），仅当所有阶段完成时才有值 */
  total_duration?: number | null;
  /** 训练参数（仅包含用户自定义的参数） */
  train_params?: TrainParams;
  /** 重建质量指标（PSNR / SSIM / LPIPS） */
  metrics?: ReconstructionMetrics;
  /** GPU 显存占用快照（worker 训练阶段采样，单位 GB） */
  gpu_memory?: {
    used_gb?: number;
    total_gb?: number;
    percent?: number;
    peak_gb?: number;
  } | null;
  /** 阿里云 OSS 公开访问 URL */
  oss_url?: string | null;
  /** 上传图片的 OSS 公开 URL 列表（拉模式：worker 从 OSS 拉取） */
  oss_images?: string[];
  /** 领取任务的 worker 标识 */
  claimed_by?: string | null;
  /** 被 worker 领取（开始执行）的时间戳 */
  claimed_at?: number | null;
  /** 任务实际开始执行时间戳 */
  started_at?: number | null;
  /** worker 最近心跳时间戳 */
  heartbeat_at?: number | null;
  /** 领取尝试次数 */
  attempts?: number;
  /** 任务结束时间戳 */
  finished_at?: number | null;
  /** 创建者用户 ID */
  owner_id?: string | null;
}

export interface AuthUser {
  id: string;
  username: string;
  role: "admin" | "user";
  created_at: number;
  disabled: boolean;
}

export interface OssUploadPolicy {
  reconstruction_id: string;
  host: string;
  bucket: string;
  dir: string;
  expire: number;
  access_key_id: string;
  policy: string;
  signature: string;
  max_file_size: number;
  file_count: number;
  total_bytes: number;
}

/** 创建任务响应 */
export interface CreateTaskResponse {
  task_id: string;
  reconstruction_id: string;
  image_count: number;
  status: TaskStatus;
  message: string;
}

/** 健康检查响应 */
export interface HealthResponse {
  status: string;
  debug_mode: boolean;
  storage_dir: string;
  oss_enabled?: boolean;
}
