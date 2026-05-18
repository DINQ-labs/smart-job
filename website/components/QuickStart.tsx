import Link from "next/link";
import { getDictionary } from "@/lib/dictionary";
import type { Locale } from "@/lib/i18n";
import { ArrowIcon } from "./Icons";

export default function QuickStart({ locale }: { locale: Locale }) {
  const t = getDictionary(locale).quickstart;

  return (
    <section id="quickstart" className="scroll-mt border-b border-white/10">
      <div className="mx-auto max-w-6xl px-5 py-20 md:py-24">
        <div className="max-w-2xl">
          <h2 className="text-3xl font-bold tracking-tight md:text-4xl">{t.title}</h2>
          <p className="mt-3 text-slate-400">{t.subtitle}</p>
        </div>

        <ol className="mt-12 space-y-4">
          {t.steps.map((step, i) => (
            <li
              key={i}
              className="rounded-2xl border border-white/10 bg-ink-800 p-5 md:flex md:items-center md:gap-6"
            >
              <div className="flex items-start gap-4 md:w-2/5">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/15 font-mono text-sm font-semibold text-accent-soft">
                  {i + 1}
                </span>
                <div>
                  <h3 className="font-semibold">{step.title}</h3>
                  <p className="mt-1 text-sm text-slate-400">{step.desc}</p>
                </div>
              </div>
              {step.code && (
                <pre className="mt-4 flex-1 overflow-x-auto rounded-xl border border-white/10 bg-ink-900 p-4 font-mono text-xs leading-relaxed text-slate-300 md:mt-0">
                  {step.code}
                </pre>
              )}
            </li>
          ))}
        </ol>

        <Link
          href={`/${locale}/docs/architecture`}
          className="group mt-8 inline-flex items-center gap-2 text-sm font-semibold text-accent-soft transition hover:text-white"
        >
          {t.docsHint}
          <ArrowIcon className="h-4 w-4 transition group-hover:translate-x-0.5" />
        </Link>
      </div>
    </section>
  );
}
