import Link from "next/link";
import { getDictionary } from "@/lib/dictionary";
import type { Locale } from "@/lib/i18n";
import { ArrowIcon } from "./Icons";

export default function AdminPreview({ locale }: { locale: Locale }) {
  const t = getDictionary(locale).adminPreview;

  return (
    <section className="scroll-mt border-b border-white/10">
      <div className="mx-auto max-w-6xl px-5 py-24">
        <div className="max-w-2xl">
          <div className="mb-5 h-1 w-12 rounded-full bg-gradient-to-r from-accent to-violet-500" />
          <h2 className="text-3xl font-bold tracking-tight md:text-4xl">{t.title}</h2>
          <p className="mt-3 text-slate-400">{t.subtitle}</p>
        </div>

        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {t.shots.map((s) => (
            <figure key={s.img} className="group">
              <div className="glass-card glow-hover overflow-hidden rounded-2xl">
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
          className="glass-card glow-hover group mt-8 inline-flex items-center gap-2 rounded-full px-6 py-3 text-sm font-semibold text-slate-200"
        >
          {t.cta}
          <ArrowIcon className="h-4 w-4 transition group-hover:translate-x-0.5" />
        </Link>
      </div>
    </section>
  );
}
