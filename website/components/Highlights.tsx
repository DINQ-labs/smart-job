import { getDictionary } from "@/lib/dictionary";
import type { Locale } from "@/lib/i18n";

export default function Highlights({ locale }: { locale: Locale }) {
  const t = getDictionary(locale);

  return (
    <section className="border-b border-white/10 bg-ink-900">
      <div className="mx-auto grid max-w-6xl grid-cols-2 gap-px overflow-hidden md:grid-cols-4">
        {t.highlights.map((h) => (
          <div key={h.label} className="px-5 py-8 text-center">
            <div className="text-3xl font-bold text-white md:text-4xl">{h.value}</div>
            <div className="mt-1.5 text-sm text-slate-400">{h.label}</div>
          </div>
        ))}
      </div>
    </section>
  );
}
