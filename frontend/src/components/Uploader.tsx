import { useRef, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { UploadCloud, ImageIcon, FolderOpen, FileArchive, X, CheckCircle2 } from "lucide-react";

const IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"];

interface UploaderProps {
  onFilesChange: (files: File[]) => void;
  onZipChange: (zip: File | null) => void;
}

export default function Uploader({ onFilesChange, onZipChange }: UploaderProps) {
  const { t } = useTranslation();
  const [files, setFiles] = useState<File[]>([]);
  const [zip, setZip] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);

  const isImage = (name: string) =>
    IMAGE_EXTS.some((ext) => name.toLowerCase().endsWith(ext));

  const updateFiles = useCallback(
    (newFiles: File[]) => {
      const valid = newFiles.filter((f) => isImage(f.name));
      setFiles(valid);
      onFilesChange(valid);
      // 切换到图片模式时清除 zip
      if (valid.length > 0) {
        setZip(null);
        onZipChange(null);
      }
    },
    [onFilesChange, onZipChange]
  );

  const handleImageSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) updateFiles(Array.from(e.target.files));
  };

  const handleFolderSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const allFiles = Array.from(e.target.files);
      updateFiles(allFiles.filter((f) => isImage(f.name)));
    }
  };

  const handleZipSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const zipFile = e.target.files[0];
      setZip(zipFile);
      onZipChange(zipFile);
      // 切换到 zip 模式时清除图片
      setFiles([]);
      onFilesChange([]);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const dropped = Array.from(e.dataTransfer.files);
    const zipFile = dropped.find((f) => f.name.toLowerCase().endsWith(".zip"));
    if (zipFile) {
      setZip(zipFile);
      onZipChange(zipFile);
      setFiles([]);
      onFilesChange([]);
    } else {
      updateFiles(dropped);
    }
  };

  const clearAll = () => {
    setFiles([]);
    setZip(null);
    onFilesChange([]);
    onZipChange(null);
    if (imageInputRef.current) imageInputRef.current.value = "";
    if (folderInputRef.current) folderInputRef.current.value = "";
    if (zipInputRef.current) zipInputRef.current.value = "";
  };

  return (
    <div className="space-y-6">
      {/* 上传方式按钮 */}
      <div className="flex flex-wrap gap-3">
        <button
          onClick={() => imageInputRef.current?.click()}
          className="gold-btn-outline"
        >
          <ImageIcon className="h-4 w-4" />
          {t("upload.selectImages")}
        </button>
        <button
          onClick={() => folderInputRef.current?.click()}
          className="gold-btn-outline"
        >
          <FolderOpen className="h-4 w-4" />
          {t("upload.selectFolder")}
        </button>
        <button
          onClick={() => zipInputRef.current?.click()}
          className="gold-btn-outline"
        >
          <FileArchive className="h-4 w-4" />
          {t("upload.selectZip")}
        </button>
        {(files.length > 0 || zip) && (
          <button
            onClick={clearAll}
            className="ml-auto inline-flex items-center gap-1.5  px-4 py-2 text-sm text-red-300 transition-colors hover:bg-red-400/10"
          >
            <X className="h-4 w-4" />
            {t("upload.clear")}
          </button>
        )}

        <input
          ref={imageInputRef}
          type="file"
          accept={IMAGE_EXTS.join(",")}
          multiple
          className="hidden"
          onChange={handleImageSelect}
        />
        <input
          ref={folderInputRef}
          type="file"
          // @ts-expect-error webkitdirectory 非标准属性
          webkitdirectory=""
          directory=""
          multiple
          className="hidden"
          onChange={handleFolderSelect}
        />
        <input
          ref={zipInputRef}
          type="file"
          accept=".zip"
          className="hidden"
          onChange={handleZipSelect}
        />
      </div>

      {/* 拖拽区域 */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`relative flex min-h-[200px] cursor-pointer flex-col items-center justify-center border-2 border-dashed transition-all ${
          dragging
            ? "border-gold-400 bg-gold-400/10"
            : "border-gold-400/20 bg-ink-200/50 hover:border-gold-400/40"
        }`}
        onClick={() => imageInputRef.current?.click()}
      >
        <UploadCloud
          className={`mb-3 h-12 w-12 transition-colors ${
            dragging ? "text-gold-300" : "text-gold-400/50"
          }`}
        />
        <p className="text-sm text-gray-400">{t("upload.dropzone")}</p>
        <p className="mt-1 text-xs text-gray-500">{t("upload.supported")}</p>
      </div>

      {/* 已选文件列表 */}
      {(files.length > 0 || zip) && (
        <div className="gold-card p-4">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-gold-300">
            <CheckCircle2 className="h-4 w-4" />
            {t("upload.files")} ({zip ? 1 : files.length})
          </h3>
          {zip ? (
            <div className="flex items-center justify-between  bg-ink-400/50 px-3 py-2">
              <div className="flex items-center gap-2">
                <FileArchive className="h-4 w-4 text-gold-400" />
                <span className="text-sm text-gray-300">{zip.name}</span>
              </div>
              <span className="text-xs text-gray-500">
                {(zip.size / 1024 / 1024).toFixed(2)} MB
              </span>
            </div>
          ) : (
            <div className="max-h-48 space-y-1 overflow-y-auto">
              {files.slice(0, 50).map((file, idx) => (
                <div
                  key={idx}
                  className="flex items-center justify-between  bg-ink-400/50 px-3 py-1.5"
                >
                  <div className="flex items-center gap-2 overflow-hidden">
                    <ImageIcon className="h-3.5 w-3.5 shrink-0 text-gold-400" />
                    <span className="truncate text-sm text-gray-300">
                      {file.name}
                    </span>
                  </div>
                  <span className="ml-2 shrink-0 text-xs text-gray-500">
                    {(file.size / 1024).toFixed(0)} KB
                  </span>
                </div>
              ))}
              {files.length > 50 && (
                <p className="pt-1 text-center text-xs text-gray-500">
                  ...还有 {files.length - 50} 个文件
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
