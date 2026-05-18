import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { isLocale, locales, type Locale } from "@/lib/i18n";
import { getDictionary } from "@/lib/dictionary";
import { docSlugs, getDoc, type DocSlug } from "@/content/docs";
import DocBody, { headings } from "@/components/DocBody";
import { ArrowIcon } from "@/components/Icons";

export function generateStaticParams() {
  return locales.flatMap((locale) => docSlugs.map((slug) => ({ locale, slug })));
}

function parse(params: { locale: string; slug: string }): { locale: Locale; slug: DocSlug } | null {
  if (!isLocale(params.locale)) return null;
  if (!(docSlugs as string[]).includes(params.slug)) return null;
  return { locale: params.locale as Locale, slug: params.slug as DocSlug };
}

export function generateMetadata({
  params,
}: {
  params: { locale: string; slug: string };
}): Metadata {
  const parsed = parse(params);
  if (!parsed) return {};
  const doc = getDoc(parsed.locale, parsed.slug);
  return { title: `${doc.title} — smart-job`, description: doc.intro };
}

export default function DocPage({ params }: { params: { locale: string; slug: string } }) {
  const parsed = parse(params);
  if (!parsed) notFound();
  const { locale, slug } = parsed;

  const doc = getDoc(locale, slug);
  const toc = headings(doc.blocks);
  const t = getDictionary(locale).docsNav;

  const idx = docSlugs.indexOf(slug);
  const prev = idx > 0 ? docSlugs[idx - 1] : null;
  const next = idx < docSlugs.length - 1 ? docSlugs[idx + 1] : null;

  return (
    <div className="lg:grid lg:grid-cols-[1fr_180px] lg:gap-10">
      <article className="min-w-0">
        <Link
          href={`/${locale}`}
          className="text-sm text-slate-500 transition hover:text-slate-300"
        >
          ← {t.backHome}
        </Link>
        <h1 className="mt-3 text-3xl font-bold tracking-tight text-white">{doc.title}</h1>
        <p className="mt-3 text-[15px] leading-7 text-slate-400">{doc.intro}</p>

        <div className="mt-10">
          <DocBody blocks={doc.blocks} />
        </div>

        <div className="mt-14 flex gap-4 border-t border-white/10 pt-6">
          {prev && (
            <Link
              href={`/${locale}/docs/${prev}`}
              className="flex-1 rounded-xl border border-white/10 bg-ink-800 p-4 transition hover:border-accent/40"
            >
              <div className="text-xs text-slate-500">←</div>
              <div className="mt-1 font-medium text-white">
                {t.items[docSlugs.indexOf(prev)].label}
              </div>
            </Link>
          )}
          {next && (
            <Link
              href={`/${locale}/docs/${next}`}
              className="flex-1 rounded-xl border border-white/10 bg-ink-800 p-4 text-right transition hover:border-accent/40"
            >
              <div className="flex items-center justify-end gap-1 text-xs text-slate-500">
                <ArrowIcon className="h-3.5 w-3.5" />
              </div>
              <div className="mt-1 font-medium text-white">
                {t.items[docSlugs.indexOf(next)].label}
              </div>
            </Link>
          )}
        </div>
      </article>

      <aside className="mt-12 hidden lg:mt-0 lg:block">
        <div className="sticky top-24">
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            {t.onThisPage}
          </div>
          <ul className="mt-3 space-y-2 border-l border-white/10">
            {toc.map((h) => (
              <li key={h.id}>
                <a
                  href={`#${h.id}`}
                  className="-ml-px block border-l border-transparent pl-3 text-sm text-slate-400 transition hover:border-accent hover:text-white"
                >
                  {h.text}
                </a>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>
  );
}
