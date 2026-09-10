import { CheckCircle2 } from "lucide-react";
import { useTranslation } from "react-i18next";

interface StepperProps {
  currentStep: number;
  totalSteps: number;
}

export default function Stepper({ currentStep, totalSteps }: StepperProps) {
  const { t } = useTranslation();
  const STEP_LABELS = [
    t("upload.step1Title"),
    t("upload.step2Title"),
    t("upload.step3Title"),
    t("upload.step4Title"),
  ];
  return (
    <div className="flex flex-col">
      {Array.from({ length: totalSteps }, (_, i) => {
        const step = i + 1;
        const isCompleted = step < currentStep;
        const isCurrent = step === currentStep;
        const isLast = step === totalSteps;

        return (
          <div key={step} className="flex items-stretch">
            <div className="flex flex-col items-center">
              <div
                className={`flex h-9 w-9 shrink-0 items-center justify-center border font-mono text-sm font-bold transition-colors ${
                  isCompleted
                    ? "border-gold-400 bg-gold-400 text-ink-900"
                    : isCurrent
                      ? "border-gold-400 bg-gold-400/20 text-gold-300"
                      : "border-gold-400/20 bg-ink-400/50 text-gray-600"
                }`}
              >
                {isCompleted ? (
                  <CheckCircle2 className="h-4 w-4" />
                ) : (
                  `0${step}`
                )}
              </div>

              {!isLast && (
                <div
                  className={`my-1 w-0.5 flex-1 transition-colors ${
                    step < currentStep ? "bg-gold-400" : "bg-gold-400/15"
                  }`}
                  style={{ minHeight: "2rem" }}
                />
              )}
            </div>

            <div className="ml-3 flex flex-col justify-center">
              <span
                className={`font-mono text-[11px] uppercase tracking-wider ${
                  isCurrent
                    ? "font-medium text-gold-300"
                    : isCompleted
                      ? "text-gold-400/70"
                      : "text-gray-600"
                }`}
              >
                {STEP_LABELS[i]}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
