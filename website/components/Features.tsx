import { getDictionary } from "@/lib/dictionary";
import type { Locale } from "@/lib/i18n";
import { FeatureIcon } from "./Icons";

export default function Features({ locale }: { locale: Locale }) {
  const t = getDictionary(locale);

  return (
    <section id="features" className="scroll-mt border-b border-white/10">
      <div className="mx-auto max-w-6xl px-5 py-20 md:py-24">
        <div className="max-w-2xl">
          <h2 className="text-3xl font-bold tracking-tight md:text-4xl">{t.features.title}</h2>
          <p className="mt-3 text-slate-400">{t.features.subtitle}</p>
        </div>

        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {t.features.items.map((f) => (
            <div
              key={f.id}
              className="group rounded-2xl border border-white/10 bg-ink-800 p-6 transition hover:border-accent/40 hover:bg-ink-700"
            >
              <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-accent/25 bg-accent/10 text-accent-soft">
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
