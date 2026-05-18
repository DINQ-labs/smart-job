import Link from "next/link";
import { getDictionary } from "@/lib/dictionary";
import type { Locale } from "@/lib/i18n";
import { ArrowIcon } from "./Icons";

export default function Hero({ locale }: { locale: Locale }) {
  const t = getDictionary(locale);

  return (
    <section className="grid-bg relative overflow-hidden border-b border-white/10">
      <div className="mx-auto max-w-6xl px-5 pb-24 pt-20 text-center md:pt-28">
        <span className="inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-3.5 py-1.5 text-xs font-medium text-accent-soft">
          <span className="h-1.5 w-1.5 rounded-full bg-accent-soft" />
          {t.hero.badge}
        </span>

        <h1 className="mx-auto mt-7 max-w-3xl text-4xl font-bold leading-tight tracking-tight md:text-6xl">
          {t.hero.title}{" "}
          <span className="bg-gradient-to-r from-accent-soft to-accent bg-clip-text text-transparent">
            {t.hero.titleAccent}
          </span>
        </h1>

        <p className="mx-auto mt-6 max-w-2xl text-base leading-relaxed text-slate-400 md:text-lg">
          {t.hero.subtitle}
        </p>

        <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
          <Link
            href={`/${locale}#quickstart`}
            className="group inline-flex items-center gap-2 rounded-full bg-accent px-6 py-3 text-sm font-semibold text-white transition hover:bg-accent-soft"
          >
            {t.hero.ctaPrimary}
            <ArrowIcon className="h-4 w-4 transition group-hover:translate-x-0.5" />
          </Link>
          <Link
            href={`/${locale}/docs/architecture`}
            className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-6 py-3 text-sm font-semibold text-slate-200 transition hover:border-white/30 hover:text-white"
          >
            {t.hero.ctaSecondary}
          </Link>
        </div>

        <p className="mt-5 text-xs text-slate-500">{t.hero.note}</p>
      </div>
    </section>
  );
}
