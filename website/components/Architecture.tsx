import { getDictionary } from "@/lib/dictionary";
import type { Locale } from "@/lib/i18n";

export default function Architecture({ locale }: { locale: Locale }) {
  const t = getDictionary(locale).architecture;

  return (
    <section id="architecture" className="scroll-mt border-b border-white/10 bg-ink-900">
      <div className="mx-auto max-w-6xl px-5 py-20 md:py-24">
        <div className="max-w-2xl">
          <h2 className="text-3xl font-bold tracking-tight md:text-4xl">{t.title}</h2>
          <p className="mt-3 text-slate-400">{t.subtitle}</p>
        </div>

        <div className="mt-12 grid gap-6 lg:grid-cols-5">
          <div className="lg:col-span-3">
            <div className="overflow-hidden rounded-2xl border border-white/10">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-white/10 bg-white/5 text-slate-400">
                    <th className="px-4 py-3 font-medium">{t.colName}</th>
                    <th className="px-4 py-3 font-medium">{t.colPort}</th>
                    <th className="px-4 py-3 font-medium">{t.colRole}</th>
                  </tr>
                </thead>
                <tbody>
                  {t.components.map((c) => (
                    <tr key={c.name} className="border-b border-white/5 last:border-0">
                      <td className="whitespace-nowrap px-4 py-3 font-medium text-white">
                        {c.name}
                      </td>
                      <td className="px-4 py-3">
                        <span className="font-mono text-xs text-accent-soft">{c.port}</span>
                      </td>
                      <td className="px-4 py-3 text-slate-400">{c.role}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="lg:col-span-2">
            <div className="h-full rounded-2xl border border-white/10 bg-ink-800 p-6">
              <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-400">
                {t.flowTitle}
              </h3>
              <ol className="mt-4 space-y-3">
                {t.flow.map((step, i) => (
                  <li key={i} className="flex gap-3">
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-accent/30 bg-accent/10 text-xs font-semibold text-accent-soft">
                      {i + 1}
                    </span>
                    <span className="text-sm leading-relaxed text-slate-300">{step}</span>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
