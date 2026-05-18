import { getDictionary } from "@/lib/dictionary";
import type { Locale } from "@/lib/i18n";

export default function Pitch({ locale }: { locale: Locale }) {
  const t = getDictionary(locale).pitch;

  return (
    <section id="pitch" className="scroll-mt border-b border-white/10">
      <div className="mx-auto max-w-6xl px-5 py-24">
        <div className="max-w-3xl">
          <div className="mb-5 h-1 w-12 rounded-full bg-gradient-to-r from-accent to-violet-500" />
          <h2 className="text-3xl font-bold tracking-tight md:text-4xl">{t.title}</h2>
          <p className="mt-3 text-slate-400">{t.subtitle}</p>
        </div>

        <div className="mt-12 grid gap-5 lg:grid-cols-3">
          {t.pillars.map((p) => (
            <div key={p.name} className="glass-card glow-hover flex flex-col rounded-2xl p-6">
              <div className="flex items-center gap-3">
                <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-accent/30 to-violet-500/15 text-xl ring-1 ring-inset ring-white/10">
                  {p.icon}
                </span>
                <h3 className="text-lg font-semibold">{p.name}</h3>
              </div>
              <p className="mt-3 text-sm leading-relaxed text-accent-soft">{p.tagline}</p>
              <ul className="mt-5 space-y-2.5 border-t border-white/10 pt-5">
                {p.points.map((pt) => (
                  <li key={pt.name} className="text-sm leading-relaxed text-slate-400">
                    <span className="font-semibold text-white">{pt.name}</span> {pt.desc}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
