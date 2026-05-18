"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { locales, localeNames, type Locale } from "@/lib/i18n";
import { GlobeIcon } from "./Icons";

export default function LangSwitch({ locale }: { locale: Locale }) {
  const pathname = usePathname() || `/${locale}`;
  const segments = pathname.split("/");

  return (
    <div className="flex items-center gap-1 rounded-full border border-white/10 bg-white/5 p-0.5">
      <GlobeIcon className="ml-1.5 h-4 w-4 text-slate-400" />
      {locales.map((l) => {
        const next = [...segments];
        next[1] = l;
        const href = next.join("/") || `/${l}`;
        const active = l === locale;
        return (
          <Link
            key={l}
            href={href}
            className={`rounded-full px-2.5 py-1 text-xs font-medium transition ${
              active ? "bg-accent text-white" : "text-slate-400 hover:text-white"
            }`}
          >
            {localeNames[l]}
          </Link>
        );
      })}
    </div>
  );
}
