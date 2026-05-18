import { getDictionary } from "@/lib/dictionary";
import type { Locale } from "@/lib/i18n";
import { FeatureIcon } from "./Icons";

export default function Features({ locale }: { locale: Locale }) {
  const t = getDictionary(locale);

  return (
    <section id="features" className="scroll-mt border-b border-white/10">
      <div className="mx-auto max-w-6xl px-5 py-24">
        <div className="max-w-2xl">
          <div className="mb-5 h-1 w-12 rounded-full bg-gradient-to-r from-accent to-violet-500" />
          <h2 className="text-3xl font-bold tracking-tight md:text-4xl">{t.features.title}</h2>
          <p className="mt-3 text-slate-400">{t.features.subtitle}</p>
        </div>

        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {t.features.items.map((f) => (
            <div key={f.id} className="glass-card glow-hover group rounded-2xl p-6">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-accent/30 to-violet-500/15 text-accent-soft ring-1 ring-inset ring-white/10 transition group-hover:from-accent/50 group-hover:to-violet-500/25">
                <FeatureIcon id={f.id} className="h-6 w-6" />
              </div>
              <h3 className="mt-5 text-lg font-semibold">{f.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-400">{f.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
