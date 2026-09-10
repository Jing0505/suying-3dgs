import type { TaskStatus } from "../types";

/**
 * 全局进度区间映射（按真实耗时权重分配）。
 *
 * worker 上报的 progress 是「阶段内 0-100」（如拉图 42 张的完成度、训练迭代进度），
 * 如果直接当全局进度显示，会出现：拉图阶段就把进度推到 99%、之后长期卡住、
 * 刷新后掉回阶段下限等跳变。
 *
 * 这里把「阶段内进度」线性映射到「全局进度区间」：
 *   全局进度 = start + (阶段内进度 / 100) × (end - start)
 * 使进度条随阶段真实进展一点点推进、单调递增，阶段切换也不回退。
 */
export const STAGE_RANGE: Record<TaskStatus, [number, number]> = {
  pending: [0, 0],
  uploading: [0, 8], // 拉取图片（42 张逐张推进）
  converting: [8, 30], // COLMAP 特征提取+重建
  training: [30, 82], // 3DGS 训练（最耗时，占最大区间）
  rendering: [82, 90], // 渲染数据集
  downloading: [90, 94], // 定位 ply 模型
  oss_uploading: [94, 100], // 上传结果
  completed: [100, 100],
  failed: [0, 0], // 特殊处理：失败保持失败前位置（见 computeStableProgress）
};

/** 把「阶段内进度 0-100」映射为「全局进度 0-100」 */
export function stageProgressToGlobal(
  status: TaskStatus,
  stageProgress: number
): number {
  const [start, end] = STAGE_RANGE[status] ?? [0, 0];
  const p = Math.max(
    0,
    Math.min(100, Number.isFinite(stageProgress) ? stageProgress : 0)
  );
  return start + (p / 100) * (end - start);
}

/**
 * 计算稳定进度：全局映射 + 永不后退。
 *
 * - 正常阶段：取 max(之前最大值, 当前阶段映射值)，保证单调递增
 * - completed：固定 100
 * - failed：保持失败前的位置（不因后端存了 progress=100 而显示 100%）
 */
export function computeStableProgress(
  status: TaskStatus,
  progress: number,
  prevMax: number
): number {
  if (status === "failed") return prevMax;
  if (status === "completed") return 100;
  const mapped = stageProgressToGlobal(status, progress);
  return Math.max(prevMax, Math.min(100, Math.round(mapped * 10) / 10));
}

/** 限制到 0-100 的整数 */
export function clampProgress(n: number): number {
  return Math.max(0, Math.min(100, Math.round(n)));
}
