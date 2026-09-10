import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  createTask,
  createTaskFromOss,
  createTaskFromZip,
  extractZipImages,
  uploadImagesToOss,
} from "./api/client";
import i18n from "./i18n";
import type { TrainParams } from "./types";

export type OssJobPhase = "extract" | "transfer" | "create" | "failed";

export interface OssUploadJob {
  localId: string;
  reconstructionId: string;
  imageCount: number;
  done: number;
  total: number;
  phase: OssJobPhase;
  error?: string;
  createdAt: number;
}

interface EnqueuePayload {
  files: File[];
  zip: File | null;
  reconId: string;
  trainParams?: TrainParams;
}

interface OssUploadQueueValue {
  jobs: OssUploadJob[];
  enqueue: (payload: EnqueuePayload) => void;
  dismiss: (localId: string) => void;
  completedTick: number;
}

const OssUploadQueueContext = createContext<OssUploadQueueValue | null>(null);

function newLocalId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `oss-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function OssUploadQueueProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<OssUploadJob[]>([]);
  const [completedTick, setCompletedTick] = useState(0);

  const patch = useCallback((localId: string, partial: Partial<OssUploadJob>) => {
    setJobs((prev) =>
      prev.map((job) => (job.localId === localId ? { ...job, ...partial } : job))
    );
  }, []);

  const remove = useCallback((localId: string) => {
    setJobs((prev) => prev.filter((job) => job.localId !== localId));
  }, []);

  const runJob = useCallback(
    async (localId: string, payload: EnqueuePayload) => {
      const paramsToSend =
        payload.trainParams && Object.keys(payload.trainParams).length > 0
          ? payload.trainParams
          : undefined;
      try {
        let imageFiles = payload.files;
        if (payload.zip) {
          patch(localId, { phase: "extract" });
          imageFiles = await extractZipImages(payload.zip);
          if (imageFiles.length === 0) {
            patch(localId, {
              phase: "failed",
              error: i18n.t("upload.noImagesInZip"),
            });
            return;
          }
        }
        patch(localId, {
          phase: "transfer",
          imageCount: imageFiles.length,
          done: 0,
          total: imageFiles.length,
        });

        try {
          const uploaded = await uploadImagesToOss(
            imageFiles,
            payload.reconId || undefined,
            (done, total) => {
              patch(localId, {
                phase: "transfer",
                done,
                total,
                reconstructionId: payload.reconId || "",
              });
            }
          );
          patch(localId, {
            phase: "create",
            done: uploaded.oss_images.length,
            total: uploaded.oss_images.length,
            reconstructionId: uploaded.reconstruction_id,
            imageCount: uploaded.oss_images.length,
          });
          await createTaskFromOss(
            uploaded.oss_images,
            uploaded.reconstruction_id,
            paramsToSend,
            uploaded.total_bytes
          );
        } catch (ossErr) {
          const msg = ossErr instanceof Error ? ossErr.message : "";
          const ossUnavailable =
            msg.includes("OSS 未配置") || msg.includes("503");
          if (!ossUnavailable) throw ossErr;
          patch(localId, { phase: "create" });
          if (payload.zip) {
            await createTaskFromZip(
              payload.zip,
              payload.reconId || undefined,
              paramsToSend
            );
          } else {
            await createTask(
              imageFiles,
              payload.reconId || undefined,
              paramsToSend
            );
          }
        }
        remove(localId);
        setCompletedTick((n) => n + 1);
      } catch (err) {
        patch(localId, {
          phase: "failed",
          error: err instanceof Error ? err.message : i18n.t("tasks.ossFailed"),
        });
      }
    },
    [patch, remove]
  );

  const enqueue = useCallback(
    (payload: EnqueuePayload) => {
      const localId = newLocalId();
      const totalHint = payload.zip ? 1 : payload.files.length;
      const job: OssUploadJob = {
        localId,
        reconstructionId: payload.reconId.trim(),
        imageCount: payload.zip ? 0 : payload.files.length,
        done: 0,
        total: Math.max(1, totalHint),
        phase: payload.zip ? "extract" : "transfer",
        createdAt: Date.now() / 1000,
      };
      setJobs((prev) => [job, ...prev]);
      void runJob(localId, {
        files: [...payload.files],
        zip: payload.zip,
        reconId: payload.reconId,
        trainParams: payload.trainParams
          ? { ...payload.trainParams }
          : undefined,
      });
    },
    [runJob]
  );

  const dismiss = useCallback((localId: string) => {
    setJobs((prev) => prev.filter((job) => job.localId !== localId));
  }, []);

  const value = useMemo(
    () => ({ jobs, enqueue, dismiss, completedTick }),
    [jobs, enqueue, dismiss, completedTick]
  );

  return (
    <OssUploadQueueContext.Provider value={value}>
      {children}
    </OssUploadQueueContext.Provider>
  );
}

export function useOssUploadQueue(): OssUploadQueueValue {
  const ctx = useContext(OssUploadQueueContext);
  if (!ctx) {
    throw new Error("useOssUploadQueue must be used within OssUploadQueueProvider");
  }
  return ctx;
}
