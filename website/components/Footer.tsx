import Link from "next/link";
import { getDictionary } from "@/lib/dictionary";
import { GITHUB_URL, type Locale } from "@/lib/i18n";
import { docSlugs } from "@/content/docs";
import { Logo } from "./Icons";

export default function Footer({ locale }: { locale: Locale }) {
  const t = getDictionary(locale);

  return (
    <footer className="relative bg-ink-900">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/50 to-transparent" />
      <div className="mx-auto max-w-6xl px-5 py-12">
        <div className="flex flex-col gap-8 md:flex-row md:justify-between">
          <div className="max-w-sm">
            <div className="flex items-center gap-2.5">
              <Logo className="h-7 w-7" />
              <span className="font-semibold">smart-job</span>
            </div>
            <p className="mt-3 text-sm text-slate-400">{t.footer.tagline}</p>
          </div>

          <div className="flex gap-14">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                {t.docsNav.overview}
              </div>
              <ul className="mt-3 space-y-2">
                {docSlugs.map((slug, i) => (
                  <li key={slug}>
                    <Link
                      href={`/${locale}/docs/${slug}`}
                      className="text-sm text-slate-400 transition hover:text-white"
                    >
                      {t.docsNav.items[i].label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                {t.footer.project}
              </div>
              <ul className="mt-3 space-y-2">
                <li>
                  <Link
                    href={`/${locale}/roadmap`}
                    className="text-sm text-slate-400 transition hover:text-white"
                  >
                    {t.nav.roadmap}
                  </Link>
                </li>
                <li>
                  <a
                    href={GITHUB_URL}
                    target="_blank"
                    rel="noreferrer"
                    className="text-sm text-slate-400 transition hover:text-white"
                  >
                    DINQ-labs/smart-job
                  </a>
                </li>
              </ul>
            </div>
          </div>
        </div>

        <p className="mt-10 border-t border-white/10 pt-6 text-xs leading-relaxed text-slate-500">
          {t.footer.disclaimer}
        </p>
        <p className="mt-2 text-xs text-slate-600">
          MIT License · {t.footer.built}
        </p>
        <p className="mt-2 text-xs text-slate-600">
          {t.footer.funding.prefix}{" "}
          <a
            href="https://dinq.me"
            target="_blank"
            rel="noreferrer"
            className="text-slate-400 underline decoration-slate-700 underline-offset-2 transition hover:text-white"
          >
            dinq.me
          </a>
          {t.footer.funding.suffix && ` ${t.footer.funding.suffix}`}
        </p>
      </div>
    </footer>
  );
}
