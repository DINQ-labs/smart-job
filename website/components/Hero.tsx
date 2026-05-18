import Link from "next/link";
import { getDictionary } from "@/lib/dictionary";
import type { Locale } from "@/lib/i18n";
import { ArrowIcon } from "./Icons";

export default function Hero({ locale }: { locale: Locale }) {
  const t = getDictionary(locale);

  return (
    <section className="relative overflow-hidden border-b border-white/10">
      {/* 极光光斑 */}
      <div className="pointer-events-none absolute inset-0 -z-10">
        <div className="aurora-blob absolute -top-32 left-1/2 h-[30rem] w-[30rem] -translate-x-1/2 rounded-full bg-indigo-500/25 blur-[130px]" />
        <div
          className="aurora-blob absolute -top-10 left-[10%] h-72 w-72 rounded-full bg-violet-500/20 blur-[120px]"
          style={{ animationDelay: "-5s" }}
        />
        <div
          className="aurora-blob absolute top-10 right-[8%] h-80 w-80 rounded-full bg-cyan-400/15 blur-[120px]"
          style={{ animationDelay: "-9s" }}
        />
      </div>
      {/* 网格 */}
      <div className="tech-grid pointer-events-none absolute inset-0 -z-10" />

      <div className="relative mx-auto max-w-6xl px-5 pb-28 pt-24 text-center md:pt-32">
        <span className="glass-card inline-flex items-center gap-2 rounded-full px-4 py-1.5 text-xs font-medium text-slate-200">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-soft opacity-75" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent-soft" />
          </span>
          {t.hero.badge}
        </span>

        <h1 className="mx-auto mt-8 max-w-4xl text-4xl font-bold leading-[1.08] tracking-tight md:text-7xl">
          {t.hero.title}{" "}
          <span className="bg-gradient-to-r from-indigo-300 via-violet-300 to-cyan-300 bg-clip-text text-transparent">
            {t.hero.titleAccent}
          </span>
        </h1>

        <p className="mx-auto mt-6 max-w-2xl text-base leading-relaxed text-slate-400 md:text-lg">
          {t.hero.subtitle}
        </p>

        <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
          <Link
            href={`/${locale}#quickstart`}
            className="group inline-flex items-center gap-2 rounded-full bg-gradient-to-r from-accent to-violet-500 px-6 py-3 text-sm font-semibold text-white shadow-glow transition hover:brightness-110"
          >
            {t.hero.ctaPrimary}
            <ArrowIcon className="h-4 w-4 transition group-hover:translate-x-0.5" />
          </Link>
          <Link
            href={`/${locale}/docs/architecture`}
            className="glass-card glow-hover inline-flex items-center gap-2 rounded-full px-6 py-3 text-sm font-semibold text-slate-200"
          >
            {t.hero.ctaSecondary}
          </Link>
        </div>

        <p className="mt-6 text-xs text-slate-500">{t.hero.note}</p>
      </div>
    </section>
  );
}
