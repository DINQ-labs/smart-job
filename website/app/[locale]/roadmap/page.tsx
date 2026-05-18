import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { getDictionary } from "@/lib/dictionary";
import { GITHUB_URL, isLocale, type Locale } from "@/lib/i18n";
import { ArrowIcon } from "@/components/Icons";

export function generateMetadata({ params }: { params: { locale: string } }): Metadata {
  if (!isLocale(params.locale)) return {};
  const t = getDictionary(params.locale as Locale).roadmap;
  return { title: `${t.title} — smart-job`, description: t.subtitle };
}

const STATUS: Record<string, { dot: string; badge: string }> = {
  shipped: { dot: "bg-emerald-400", badge: "bg-emerald-400/15 text-emerald-300 ring-emerald-400/30" },
  now: { dot: "bg-accent", badge: "bg-accent/20 text-accent-soft ring-accent/40" },
  ongoing: { dot: "bg-violet-400", badge: "bg-violet-500/15 text-violet-300 ring-violet-400/30" },
};

export default function RoadmapPage({ params }: { params: { locale: string } }) {
  if (!isLocale(params.locale)) notFound();
  const locale = params.locale as Locale;
  const dict = getDictionary(locale);
  const t = dict.roadmap;

  return (
    <div className="relative overflow-hidden">
      <div className="pointer-events-none absolute inset-x-0 top-0 -z-10">
        <div className="aurora-blob mx-auto h-72 w-[44rem] max-w-full rounded-full bg-accent/12 blur-[130px]" />
      </div>
      <div className="tech-grid pointer-events-none absolute inset-0 -z-10" />

      <div className="mx-auto max-w-3xl px-5 py-12 md:py-16">
        <Link
          href={`/${locale}`}
          className="text-sm text-slate-500 transition hover:text-slate-300"
        >
          ← {dict.docsNav.backHome}
        </Link>

        <div className="mt-5 h-1 w-12 rounded-full bg-gradient-to-r from-accent to-violet-500" />
        <h1 className="mt-5 text-3xl font-bold tracking-tight text-white md:text-5xl">
          {t.title}
        </h1>
        <p className="mt-4 text-[15px] leading-7 text-slate-400">{t.subtitle}</p>

        {/* 时间线 */}
        <div className="relative mt-14 pl-8">
          <div className="absolute left-[7px] top-2 bottom-2 w-0.5 rounded-full bg-gradient-to-b from-emerald-400/50 via-accent/50 to-violet-400/40" />
          <div className="space-y-6">
            {t.milestones.map((m) => {
              const s = STATUS[m.status] ?? STATUS.ongoing;
              return (
                <div key={m.title} className="relative">
                  <span
                    className={`absolute -left-8 top-7 h-4 w-4 rounded-full ring-4 ring-ink-900 ${s.dot}`}
                  />
                  <div className="glass-card glow-hover rounded-2xl p-6">
                    <span
                      className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset ${s.badge}`}
                    >
                      {m.badge}
                    </span>
                    <h2 className="mt-3 text-xl font-semibold tracking-tight text-white">
                      {m.title}
                    </h2>
                    <p className="mt-2 text-sm leading-relaxed text-slate-400">{m.desc}</p>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* 为什么 */}
        <div className="mt-10 rounded-2xl border border-white/10 bg-white/[0.03] p-6 md:p-7">
          <h2 className="text-lg font-semibold tracking-tight text-white">{t.why.title}</h2>
          <p className="mt-3 text-sm leading-relaxed text-slate-400">{t.why.body}</p>
        </div>

        {/* 参与 */}
        <div className="mt-14">
          <div className="h-1 w-12 rounded-full bg-gradient-to-r from-accent to-cyan-400" />
          <h2 className="mt-5 text-2xl font-bold tracking-tight text-white md:text-3xl">
            {t.cta.title}
          </h2>
          <p className="mt-3 text-[15px] leading-7 text-slate-400">{t.cta.intro}</p>

          <div className="mt-6 grid gap-4 sm:grid-cols-2">
            {t.cta.roles.map((r) => (
              <div key={r.role} className="glass-card rounded-2xl p-6">
                <h3 className="text-sm font-semibold text-accent-soft">{r.role}</h3>
                <p className="mt-2 text-sm leading-relaxed text-slate-400">{r.desc}</p>
              </div>
            ))}
          </div>

          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            className="group mt-7 inline-flex items-center gap-2 rounded-full bg-gradient-to-r from-accent to-violet-500 px-6 py-3 text-sm font-semibold text-white shadow-glow transition hover:brightness-110"
          >
            {t.cta.button}
            <ArrowIcon className="h-4 w-4 transition group-hover:translate-x-0.5" />
          </a>
        </div>
      </div>
    </div>
  );
}
