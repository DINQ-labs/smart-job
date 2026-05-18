import { notFound } from "next/navigation";
import { isLocale, type Locale } from "@/lib/i18n";
import DocsSidebar from "@/components/DocsSidebar";

export default function DocsLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: { locale: string };
}) {
  if (!isLocale(params.locale)) notFound();
  const locale = params.locale as Locale;

  return (
    <div className="mx-auto max-w-6xl px-5 py-12">
      <div className="gap-10 lg:grid lg:grid-cols-[200px_1fr]">
        <aside className="mb-8 lg:mb-0">
          <div className="lg:sticky lg:top-24">
            <DocsSidebar locale={locale} />
          </div>
        </aside>
        {children}
      </div>
    </div>
  );
}
