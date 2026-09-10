import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Rocket,
  Loader2,
  CheckCircle2,
  AlertCircle,
  ChevronRight,
  ChevronLeft,
  FileText,
  Hash,
  Settings2,
  Sliders,
  RotateCcw,
} from "lucide-react";
import Uploader from "../components/Uploader";
import Stepper from "../components/Stepper";
import TrainParamsForm from "../components/TrainParamsForm";
import { checkHealth } from "../api/client";
import { useOssUploadQueue } from "../ossUploadQueue";
import type { TrainParams } from "../types";

const TOTAL_STEPS = 4;

export default function Upload() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { enqueue } = useOssUploadQueue();
  const redirectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [currentStep, setCurrentStep] = useState(1);
  const [files, setFiles] = useState<File[]>([]);
  const [zip, setZip] = useState<File | null>(null);
  const [reconId, setReconId] = useState("");
  const [trainParams, setTrainParams] = useState<TrainParams>({});
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [uploaderKey, setUploaderKey] = useState(0);

  const hasFiles = files.length > 0 || zip !== null;
  const waitingRedirect = Boolean(notice);
  const canProceedStep1 = hasFiles && !submitting && !waitingRedirect;
  const customParamCount = Object.keys(trainParams).length;

  const goNext = () => {
    if (currentStep < TOTAL_STEPS) setCurrentStep((s) => s + 1);
  };

  const goPrev = () => {
    if (currentStep > 1) setCurrentStep((s) => s - 1);
  };

  useEffect(() => {
    return () => {
      if (redirectTimer.current != null) {
        clearTimeout(redirectTimer.current);
      }
    };
  }, []);

  const handleSubmit = async () => {
    if (!hasFiles || submitting || waitingRedirect) return;
    setSubmitting(true);
    setError("");
    setNotice("");
    try {
      try {
        await checkHealth();
      } catch {
        throw new Error("后端无法连接，请确认 8010 服务已启动");
      }

      enqueue({
        files: [...files],
        zip,
        reconId,
        trainParams: customParamCount > 0 ? { ...trainParams } : undefined,
      });
      setCurrentStep(1);
      setFiles([]);
      setZip(null);
      setReconId("");
      setTrainParams({});
      setUploaderKey((n) => n + 1);
      setNotice(t("upload.queuedNotice"));
      redirectTimer.current = setTimeout(() => {
        navigate("/tasks");
      }, 3000);
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : t("upload.submitFailed");
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
      {/* 顶部标题 */}
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-bold tracking-tight text-gold-300">
          {t("upload.title")}
        </h1>
        <p className="mt-2 text-sm text-gray-400">{t("upload.subtitle")}</p>
      </div>

      {/* 左侧步骤指示 + 右侧内容 */}
      <div className="flex gap-8">
        {/* 左侧：竖向步骤指示器 */}
        <div className="hidden shrink-0 pt-6 lg:block">
          <Stepper currentStep={currentStep} totalSteps={TOTAL_STEPS} />
        </div>

        {/* 右侧：步骤内容 */}
        <div className="gold-card flex flex-1 flex-col p-6 sm:p-8">
        {/* 步骤内容区：flex-1 让它占满中间空间 */}
        <div className="flex-1">
        {/* Step 1: 选择文件 */}
        {currentStep === 1 && (
          <div>
            <div className="mb-6">
              <h2 className="text-lg font-semibold text-gold-200">
                {t("upload.step1Title")}
              </h2>
              <p className="mt-1 text-sm text-gray-400">
                {t("upload.step1Desc")}
              </p>
            </div>
            <Uploader
              key={uploaderKey}
              onFilesChange={setFiles}
              onZipChange={setZip}
            />
          </div>
        )}

        {/* Step 2: 配置编号 */}
        {currentStep === 2 && (
          <div>
            <div className="mb-6">
              <h2 className="text-lg font-semibold text-gold-200">
                {t("upload.step2Title")}
              </h2>
              <p className="mt-1 text-sm text-gray-400">
                {t("upload.step2Desc")}
              </p>
            </div>

            {/* 文件确认摘要 */}
            <div className="mb-6 flex items-center gap-3  border border-gold-400/15 bg-ink-400/50 p-4">
              <FileText className="h-5 w-5 shrink-0 text-gold-400" />
              <div className="text-sm text-gray-300">
                {zip
                  ? t("upload.confirmFiles", { count: 1 })
                  : t("upload.confirmFiles", { count: files.length })}
              </div>
              <button
                onClick={() => setCurrentStep(1)}
                className="ml-auto text-xs text-gold-400/70 transition-colors hover:text-gold-300"
              >
                {t("upload.goUpload")}
              </button>
            </div>

            {/* 编号输入 */}
            <div>
              <label className="mb-2 flex items-center gap-1.5 text-sm font-medium text-gold-300">
                <Hash className="h-4 w-4" />
                {t("upload.reconIdLabel")}
              </label>
              <input
                type="text"
                value={reconId}
                onChange={(e) => setReconId(e.target.value)}
                placeholder={t("upload.reconIdPlaceholder")}
                className="w-full  border border-gold-400/20 bg-ink-400/50 px-4 py-2.5 text-sm text-gray-200 placeholder-gray-600 outline-none transition-all focus:border-gold-400/50 focus:shadow-gold"
              />
            </div>
          </div>
        )}

        {/* Step 3: 参数配置 */}
        {currentStep === 3 && (
          <div>
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="flex items-center gap-2 text-lg font-semibold text-gold-200">
                  <Sliders className="h-5 w-5" />
                  {t("upload.step3Title")}
                </h2>
                <p className="mt-1 text-sm text-gray-400">
                  {t("upload.step3Desc")}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setTrainParams({})}
                className="flex items-center gap-1.5  border border-gold-400/20 px-3 py-1.5 text-xs text-gray-400 transition hover:border-gold-400/40 hover:text-gold-300"
              >
                <RotateCcw className="h-3 w-3" />
                {t("upload.params.resetAll")}
              </button>
            </div>
            <TrainParamsForm value={trainParams} onChange={setTrainParams} />
          </div>
        )}

        {/* Step 4: 确认提交 */}
        {currentStep === 4 && (
          <div>
            <div className="mb-6">
              <h2 className="text-lg font-semibold text-gold-200">
                {t("upload.step4Title")}
              </h2>
              <p className="mt-1 text-sm text-gray-400">
                {t("upload.step4Desc")}
              </p>
            </div>

            {/* 上半部分：基础信息，左右布局占满宽度 */}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="flex flex-col items-center justify-center  bg-ink-400/50 p-4 text-center">
                <FileText className="mb-2 h-6 w-6 text-gold-400" />
                <span className="text-xs text-gray-500">{t("upload.files")}</span>
                <span className="mt-1 text-lg font-bold text-gold-300">
                  {zip ? 1 : files.length}
                </span>
              </div>
              <div className="flex flex-col items-center justify-center  bg-ink-400/50 p-4 text-center">
                <Hash className="mb-2 h-6 w-6 text-gold-400" />
                <span className="text-xs text-gray-500">{t("upload.confirmReconId")}</span>
                <span className="mt-1 truncate text-lg font-bold text-gold-300 w-full">
                  {reconId || t("upload.confirmReconIdAuto")}
                </span>
              </div>
              <div className="flex flex-col items-center justify-center  bg-ink-400/50 p-4 text-center">
                <Settings2 className="mb-2 h-6 w-6 text-gold-400" />
                <span className="text-xs text-gray-500">{t("upload.step3Title")}</span>
                <span className="mt-1 text-lg font-bold text-gold-300">
                  {customParamCount > 0
                    ? `${customParamCount}`
                    : t("upload.params.useDefault")}
                </span>
              </div>
            </div>

            {/* 下半部分：自定义参数列表 */}
            {customParamCount > 0 && (
              <div className="mt-5 overflow-hidden  border border-gold-400/15 bg-ink-400/30 p-4">
                <h4 className="mb-3 text-xs font-semibold uppercase tracking-wider text-gold-400/70">
                  {t("upload.params.custom")}
                </h4>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-5">
                  {Object.entries(trainParams).map(([key, value]) => {
                    const paramLabel = t(`upload.params.${key}.label`);
                    return (
                      <div key={key} className="flex items-center justify-between bg-ink-900/40 px-3 py-2 text-sm">
                        <span className="text-gray-400">{paramLabel}</span>
                        <span className="font-mono font-medium text-gold-300">
                          {String(value)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}

        {/* 已入队提示 */}
        {notice && (
          <div className="mt-4 flex items-start gap-3 border border-emerald-400/30 bg-emerald-400/10 p-4">
            <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-400" />
            <div className="text-sm">
              <p className="text-emerald-300">{notice}</p>
              <Link
                to="/tasks"
                className="mt-1 inline-block text-xs text-gold-400 hover:text-gold-300"
              >
                {t("nav.tasks")}
              </Link>
            </div>
          </div>
        )}

        {/* 错误提示 */}
        {error && (
          <div className="mt-4 flex items-start gap-3  border border-red-400/30 bg-red-400/10 p-4">
            <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-red-400" />
            <div className="text-sm text-red-300">{error}</div>
          </div>
        )}
        </div>

        {/* 底部操作按钮 - 固定在卡片底部 */}
        <div className="mt-6 flex items-center justify-between border-t border-gold-400/10 pt-6">
          {currentStep > 1 ? (
            <button
              onClick={goPrev}
              disabled={submitting || waitingRedirect}
              className="gold-btn-outline"
            >
              <ChevronLeft className="h-4 w-4" />
              {t("upload.prev")}
            </button>
          ) : (
            <div />
          )}

          {currentStep < TOTAL_STEPS ? (
            <button
              onClick={goNext}
              disabled={currentStep === 1 && !canProceedStep1}
              className="gold-btn"
            >
              {t("upload.next")}
              <ChevronRight className="h-4 w-4" />
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={!hasFiles || submitting || waitingRedirect}
              className="gold-btn"
            >
              {submitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t("upload.uploading")}
                </>
              ) : (
                <>
                  <Rocket className="h-4 w-4" />
                  {t("upload.confirmSubmit")}
                </>
              )}
            </button>
          )}
        </div>
        </div>
      </div>
    </div>
  );
}
