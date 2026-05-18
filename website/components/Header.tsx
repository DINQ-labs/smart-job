import Link from "next/link";
import { getDictionary } from "@/lib/dictionary";
import { GITHUB_URL, type Locale } from "@/lib/i18n";
import { GithubIcon, Logo } from "./Icons";
import LangSwitch from "./LangSwitch";

export default function Header({ locale }: { locale: Locale }) {
  const t = getDictionary(locale);
  const nav = [
    { href: `/${locale}#features`, label: t.nav.features },
    { href: `/${locale}#showcase`, label: t.nav.showcase },
    { href: `/${locale}#architecture`, label: t.nav.architecture },
    { href: `/${locale}#quickstart`, label: t.nav.quickstart },
    { href: `/${locale}/docs/architecture`, label: t.nav.docs },
  ];

  return (
    <header className="sticky top-0 z-50 bg-ink-900/70 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-5">
        <Link href={`/${locale}`} className="flex items-center gap-2.5">
          <Logo className="h-8 w-8" />
          <span className="text-lg font-semibold tracking-tight">smart-job</span>
        </Link>

        <nav className="hidden items-center gap-7 md:flex">
          {nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="text-sm text-slate-300 transition hover:text-white"
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="flex items-center gap-3">
          <LangSwitch locale={locale} />
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            className="flex h-9 w-9 items-center justify-center rounded-full border border-white/10 bg-white/5 text-slate-300 transition hover:border-accent/40 hover:text-white"
            aria-label="GitHub"
          >
            <GithubIcon className="h-5 w-5" />
          </a>
        </div>
      </div>
      <div className="h-px w-full bg-gradient-to-r from-transparent via-accent/40 to-transparent" />
    </header>
  );
}
