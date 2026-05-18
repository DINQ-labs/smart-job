"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { docSlugs } from "@/content/docs";
import { getDictionary } from "@/lib/dictionary";
import type { Locale } from "@/lib/i18n";

export default function DocsSidebar({ locale }: { locale: Locale }) {
  const pathname = usePathname() || "";
  const t = getDictionary(locale).docsNav;

  return (
    <nav>
      <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        {t.overview}
      </div>
      <ul className="mt-3 space-y-1">
        {docSlugs.map((slug, i) => {
          const href = `/${locale}/docs/${slug}`;
          const active = pathname === href;
          return (
            <li key={slug}>
              <Link
                href={href}
                className={`block rounded-lg px-3 py-2 text-sm transition ${
                  active
                    ? "bg-accent/15 font-medium text-accent-soft"
                    : "text-slate-400 hover:bg-white/5 hover:text-white"
                }`}
              >
                {t.items[i].label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
