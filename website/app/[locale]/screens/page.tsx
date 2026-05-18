import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { isLocale, type Locale } from "@/lib/i18n";
import { getScreens } from "@/content/screens";

export function generateMetadata({ params }: { params: { locale: string } }): Metadata {
  if (!isLocale(params.locale)) return {};
  const s = getScreens(params.locale as Locale);
  return { title: `${s.title} — smart-job`, description: s.intro };
}

export default function ScreensPage({ params }: { params: { locale: string } }) {
  if (!isLocale(params.locale)) notFound();
  const locale = params.locale as Locale;
  const s = getScreens(locale);

  return (
    <div className="mx-auto max-w-6xl px-5 py-12 md:py-16">
      <Link
        href={`/${locale}`}
        className="text-sm text-slate-500 transition hover:text-slate-300"
      >
        ← {s.backHome}
      </Link>
      <h1 className="mt-3 text-3xl font-bold tracking-tight text-white md:text-4xl">{s.title}</h1>
      <p className="mt-3 max-w-3xl text-[15px] leading-7 text-slate-400">{s.intro}</p>

      <div className="mt-12 space-y-14">
        {s.groups.map((g) => (
          <section key={g.title}>
            <h2 className="text-xl font-semibold tracking-tight text-white">{g.title}</h2>
            <div className="mt-6 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {g.screens.map((sc) => (
                <a
                  key={sc.img}
                  href={sc.img}
                  target="_blank"
                  rel="noreferrer"
                  className="group block"
                >
                  <div className="overflow-hidden rounded-xl border border-white/10 bg-ink-800 transition group-hover:border-accent/40">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={sc.img}
                      alt={sc.label}
                      width={1440}
                      height={1293}
                      loading="lazy"
                      className="block w-full"
                    />
                  </div>
                  <div className="mt-2.5 flex items-center justify-between gap-2">
                    <span className="text-sm font-medium text-white">{sc.label}</span>
                    <span className="font-mono text-xs text-accent-soft">{sc.route}</span>
                  </div>
                </a>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
