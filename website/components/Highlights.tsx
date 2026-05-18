import { getDictionary } from "@/lib/dictionary";
import type { Locale } from "@/lib/i18n";

export default function Highlights({ locale }: { locale: Locale }) {
  const t = getDictionary(locale);

  return (
    <section className="border-b border-white/10">
      <div className="mx-auto grid max-w-6xl grid-cols-2 gap-px overflow-hidden bg-white/[0.06] md:grid-cols-4">
        {t.highlights.map((h) => (
          <div
            key={h.label}
            className="bg-ink-900 px-5 py-10 text-center transition hover:bg-ink-800"
          >
            <div className="bg-gradient-to-br from-white via-slate-200 to-slate-500 bg-clip-text text-4xl font-bold tracking-tight text-transparent md:text-5xl">
              {h.value}
            </div>
            <div className="mt-2 text-sm text-slate-400">{h.label}</div>
          </div>
        ))}
      </div>
    </section>
  );
}
