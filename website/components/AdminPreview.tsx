import Link from "next/link";
import { getDictionary } from "@/lib/dictionary";
import type { Locale } from "@/lib/i18n";
import { ArrowIcon } from "./Icons";

export default function AdminPreview({ locale }: { locale: Locale }) {
  const t = getDictionary(locale).adminPreview;

  return (
    <section className="scroll-mt border-b border-white/10">
      <div className="mx-auto max-w-6xl px-5 py-20 md:py-24">
        <div className="max-w-2xl">
          <h2 className="text-3xl font-bold tracking-tight md:text-4xl">{t.title}</h2>
          <p className="mt-3 text-slate-400">{t.subtitle}</p>
        </div>

        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {t.shots.map((s) => (
            <figure key={s.img} className="group">
              <div className="overflow-hidden rounded-2xl border border-white/10 bg-ink-800 transition group-hover:border-accent/40">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={s.img}
                  alt={s.title}
                  width={1440}
                  height={1293}
                  loading="lazy"
                  className="block w-full"
                />
              </div>
              <figcaption className="mt-3 text-sm font-semibold">{s.title}</figcaption>
            </figure>
          ))}
        </div>

        <Link
          href={`/${locale}/screens`}
          className="group mt-8 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-6 py-3 text-sm font-semibold text-slate-200 transition hover:border-white/30 hover:text-white"
        >
          {t.cta}
          <ArrowIcon className="h-4 w-4 transition group-hover:translate-x-0.5" />
        </Link>
      </div>
    </section>
  );
}
