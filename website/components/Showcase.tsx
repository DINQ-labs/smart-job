import { getDictionary } from "@/lib/dictionary";
import type { Locale } from "@/lib/i18n";

export default function Showcase({ locale }: { locale: Locale }) {
  const t = getDictionary(locale).showcase;

  return (
    <section id="showcase" className="scroll-mt border-b border-white/10 bg-ink-900">
      <div className="mx-auto max-w-6xl px-5 py-20 md:py-24">
        <div className="max-w-2xl">
          <h2 className="text-3xl font-bold tracking-tight md:text-4xl">{t.title}</h2>
          <p className="mt-3 text-slate-400">{t.subtitle}</p>
        </div>

        <div className="mt-12 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          {t.shots.map((s) => (
            <figure key={s.img} className="group">
              <div className="overflow-hidden rounded-2xl border border-white/10 bg-ink-800 transition group-hover:border-accent/40">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={s.img}
                  alt={s.title}
                  width={370}
                  height={920}
                  loading="lazy"
                  className="block w-full"
                />
              </div>
              <figcaption className="mt-3">
                <div className="text-sm font-semibold">{s.title}</div>
                <div className="mt-1 text-xs leading-relaxed text-slate-400">{s.desc}</div>
              </figcaption>
            </figure>
          ))}
        </div>
      </div>
    </section>
  );
}
