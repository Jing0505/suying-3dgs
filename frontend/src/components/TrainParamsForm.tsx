import { useTranslation } from "react-i18next";
import { Info, RotateCcw } from "lucide-react";
import type { TrainParams } from "../types";

interface TrainParamsFormProps {
  value: TrainParams;
  onChange: (params: TrainParams) => void;
}

const DEFAULTS: Record<string, number | boolean> = {
  iterations: 30000,
  resolution: -1,
  sh_degree: 3,
  white_background: false,
  eval: false,
  densify_grad_threshold: 0.0002,
  densification_interval: 100,
  opacity_reset_interval: 3000,
  densify_from_iter: 500,
  densify_until_iter: 15000,
  lambda_dssim: 0.2,
  percent_dense: 0.01,
  random_background: false,
  antialiasing: false,
};

const BOOL_PARAMS = new Set([
  "white_background",
  "eval",
  "random_background",
  "antialiasing",
]);

const GROUPS = [
  {
    key: "groupBasic",
    params: ["iterations", "resolution", "sh_degree", "white_background", "eval"],
  },
  {
    key: "groupAdvanced",
    params: [
      "densify_grad_threshold",
      "densification_interval",
      "opacity_reset_interval",
      "densify_from_iter",
      "densify_until_iter",
    ],
  },
  {
    key: "groupQuality",
    params: ["lambda_dssim", "percent_dense"],
  },
  {
    key: "groupVisual",
    params: ["random_background", "antialiasing"],
  },
];

export default function TrainParamsForm({ value, onChange }: TrainParamsFormProps) {
  const { t } = useTranslation();

  const handleResetOne = (paramKey: string) => {
    const next = { ...value };
    delete next[paramKey as keyof TrainParams];
    onChange(next);
  };

  const handleNumberChange = (paramKey: string, raw: string) => {
    if (raw === "") {
      handleResetOne(paramKey);
      return;
    }
    const num = Number(raw);
    if (Number.isNaN(num)) return;
    const defaultVal = DEFAULTS[paramKey];
    if (num === defaultVal) {
      handleResetOne(paramKey);
    } else {
      onChange({ ...value, [paramKey]: num });
    }
  };

  const handleBoolChange = (paramKey: string, checked: boolean) => {
    const defaultVal = DEFAULTS[paramKey];
    if (checked === defaultVal) {
      handleResetOne(paramKey);
    } else {
      onChange({ ...value, [paramKey]: checked });
    }
  };

  return (
    <div className="space-y-6">
      {GROUPS.map((group) => (
        <div key={group.key}>
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wider text-gold-400/70">
            {t(`upload.params.${group.key}`)}
          </h4>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {group.params.map((paramKey) => {
              const isBool = BOOL_PARAMS.has(paramKey);
              const userValue = value[paramKey as keyof TrainParams];
              const isCustom = userValue !== undefined;
              const currentValue = isCustom ? userValue : DEFAULTS[paramKey];

              return (
                <div
                  key={paramKey}
                  className={` border p-3 transition ${
                    isCustom
                      ? "border-gold-400/40 bg-ink-400/50"
                      : "border-ink-400/60 bg-ink-400/20"
                  }`}
                >
                  {/* 标签行 */}
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5">
                      <label className="text-sm font-medium text-gray-200">
                        {t(`upload.params.${paramKey}.label`)}
                      </label>
                      <span className="group relative inline-flex">
                        <Info className="h-3.5 w-3.5 cursor-help text-gray-600 hover:text-gold-400" />
                        <span className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-1 w-56 -translate-x-1/2  border border-gold-400/20 bg-ink-900 px-3 py-2 text-xs text-gray-300 opacity-0 shadow-xl transition-opacity duration-150 group-hover:opacity-100">
                          {t(`upload.params.${paramKey}.desc`)}
                        </span>
                      </span>
                    </div>
                    {isCustom && (
                      <button
                        type="button"
                        onClick={() => handleResetOne(paramKey)}
                        className="text-gray-600 transition hover:text-gold-400"
                        title={t("upload.params.resetOne")}
                      >
                        <RotateCcw className="h-3 w-3" />
                      </button>
                    )}
                  </div>

                  {/* 输入区域：始终显示 */}
                  {isBool ? (
                    <div className="flex items-center gap-3">
                      <button
                        type="button"
                        role="switch"
                        aria-checked={Boolean(currentValue)}
                        onClick={() =>
                          handleBoolChange(paramKey, !currentValue)
                        }
                        className={`relative h-5 w-9 transition ${
                          currentValue ? "bg-gold-400/80" : "bg-ink-400/80"
                        }`}
                      >
                        <span
                          className={`absolute top-0.5 h-4 w-4 bg-white transition-all ${
                            currentValue ? "left-4" : "left-0.5"
                          }`}
                        />
                      </button>
                      <span className="font-mono text-xs text-gray-400">
                        {currentValue ? "True" : "False"}
                      </span>
                      {!isCustom && (
                        <span className="ml-auto text-[10px] text-gray-600">
                          {t("upload.params.default")}
                        </span>
                      )}
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <input
                        type="number"
                        value={String(currentValue ?? "")}
                        onChange={(e) =>
                          handleNumberChange(paramKey, e.target.value)
                        }
                        className={`w-full  border px-3 py-1.5 font-mono text-sm outline-none transition ${
                          isCustom
                            ? "border-gold-400/40 bg-ink-900/60 text-gold-200 focus:border-gold-400/60"
                            : "border-ink-400/60 bg-ink-900/30 text-gray-400 focus:border-gold-400/40"
                        }`}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
