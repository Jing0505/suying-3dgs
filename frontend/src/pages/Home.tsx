import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  UploadCloud,
  Cpu,
  Box,
  Activity,
  ArrowRight,
} from "lucide-react";

export default function Home() {
  const { t } = useTranslation();

  const features = [
    {
      icon: UploadCloud,
      title: t("home.feature1Title"),
      desc: t("home.feature1Desc"),
    },
    {
      icon: Cpu,
      title: t("home.feature2Title"),
      desc: t("home.feature2Desc"),
    },
    {
      icon: Box,
      title: t("home.feature3Title"),
      desc: t("home.feature3Desc"),
    },
    {
      icon: Activity,
      title: t("home.feature4Title"),
      desc: t("home.feature4Desc"),
    },
  ];

  const steps = [
    { step: "1", label: t("home.pipelineStep1") },
    { step: "2", label: t("home.pipelineStep2") },
    { step: "3", label: t("home.pipelineStep3") },
    { step: "4", label: t("home.pipelineStep4") },
    { step: "5", label: t("home.pipelineStep5") },
  ];

  return (
    <div className="relative">
      {/* Hero */}
      <section className="relative mx-auto max-w-7xl px-4 pt-24 pb-20 sm:px-6 sm:pb-28 animate-fade-up">
        <div className="mx-auto max-w-3xl text-center">
          <h1 className="text-5xl font-bold tracking-tighter text-gold-300 sm:text-7xl">
            {t("home.title")}
          </h1>
          <p className="mt-5 text-xl font-medium text-gold-400/80 sm:text-2xl">
            {t("home.subtitle")}
          </p>
          <p className="mx-auto mt-6 max-w-2xl text-base leading-relaxed text-gray-400 sm:text-lg">
            {t("home.description")}
          </p>
          <div className="mt-12 flex flex-col items-center justify-center gap-4 sm:flex-row">
            <Link to="/upload" className="gold-btn group">
              {t("home.start")}
              <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
            </Link>
            <Link to="/tasks" className="gold-btn-outline">
              {t("home.viewTasks")}
            </Link>
          </div>
        </div>
      </section>

      {/* Features - brutalist 网格分隔，无圆角 */}
      <section className="relative mx-auto max-w-7xl px-4 pb-24 sm:px-6 animate-fade-up" style={{ animationDelay: "0.1s" }}>
        <div className="mb-10 flex items-center gap-3 border-b border-gold-400/20 pb-3">
          <h2 className="font-mono text-xs uppercase tracking-[0.3em] text-gold-300">
            [ {t("home.features")} ]
          </h2>
          <span className="font-mono text-[10px] uppercase tracking-wider text-gray-600">
            /// 04 MODULES
          </span>
        </div>
        <div className="grid grid-cols-1 gap-px border border-gold-400/15 bg-gold-400/15 md:grid-cols-2">
          {features.map(({ icon: Icon, title, desc }) => (
            <div
              key={title}
              className="group flex items-start gap-5 bg-ink-200 p-8 transition-colors hover:bg-ink-100"
            >
              <div className="flex h-10 w-10 shrink-0 items-center justify-center border border-gold-400/30 bg-gold-400/5 text-gold-400">
                <Icon className="h-4 w-4" />
              </div>
              <div className="min-w-0">
                <h3 className="font-mono text-sm uppercase tracking-wider text-gold-200">{title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-gray-400">{desc}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* 流程示意 - brutalist 方框数字 */}
      <section className="relative mx-auto max-w-5xl px-4 pb-32 sm:px-6 animate-fade-up" style={{ animationDelay: "0.2s" }}>
        <div className="gold-card p-8 sm:p-12">
          <div className="mb-10 flex items-center gap-3 border-b border-gold-400/15 pb-3">
            <h2 className="font-mono text-xs uppercase tracking-[0.3em] text-gold-300">
              [ {t("home.pipelineTitle")} ]
            </h2>
            <span className="font-mono text-[10px] uppercase tracking-wider text-gray-600">
              /// 05 STAGES
            </span>
          </div>
          <div className="flex flex-col items-center gap-4 sm:flex-row sm:justify-between">
            {steps.map((item, idx, arr) => (
              <div key={item.step} className="flex items-center gap-4">
                <div className="flex flex-col items-center gap-2.5">
                  <div className="flex h-14 w-14 items-center justify-center border border-gold-400/40 bg-ink-400 font-mono text-base font-bold text-gold-400">
                    {item.step}
                  </div>
                  <span className="font-mono text-[10px] uppercase tracking-wider text-gray-400">{item.label}</span>
                </div>
                {idx < arr.length - 1 && (
                  <ArrowRight className="hidden h-4 w-4 text-gold-400/30 sm:block" />
                )}
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
