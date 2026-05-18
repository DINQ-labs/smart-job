import Link from "next/link";
import { getDictionary } from "@/lib/dictionary";
import type { Locale } from "@/lib/i18n";
import { ArrowIcon } from "./Icons";

export default function DocsCta({ locale }: { locale: Locale }) {
  const t = getDictionary(locale).docsCta;

  return (
    <section className="bg-ink-900">
      <div className="mx-auto max-w-6xl px-5 py-20 md:py-24">
        <div className="max-w-2xl">
          <h2 className="text-3xl font-bold tracking-tight md:text-4xl">{t.title}</h2>
          <p className="mt-3 text-slate-400">{t.subtitle}</p>
        </div>

        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {t.cards.map((card) => (
            <Link
              key={card.slug}
              href={`/${locale}/docs/${card.slug}`}
              className="group flex flex-col rounded-2xl border border-white/10 bg-ink-800 p-6 transition hover:border-accent/40 hover:bg-ink-700"
            >
              <h3 className="text-lg font-semibold">{card.title}</h3>
              <p className="mt-2 flex-1 text-sm leading-relaxed text-slate-400">{card.desc}</p>
              <ArrowIcon className="mt-4 h-4 w-4 text-accent-soft transition group-hover:translate-x-0.5" />
            </Link>
          ))}
        </div>
      </div>
    </section>
  );
}
