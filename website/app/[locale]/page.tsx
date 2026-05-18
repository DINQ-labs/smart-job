import { notFound } from "next/navigation";
import { isLocale, type Locale } from "@/lib/i18n";
import Hero from "@/components/Hero";
import Highlights from "@/components/Highlights";
import Features from "@/components/Features";
import Showcase from "@/components/Showcase";
import AdminPreview from "@/components/AdminPreview";
import Architecture from "@/components/Architecture";
import QuickStart from "@/components/QuickStart";
import DocsCta from "@/components/DocsCta";

export default function HomePage({ params }: { params: { locale: string } }) {
  if (!isLocale(params.locale)) notFound();
  const locale = params.locale as Locale;

  return (
    <>
      <Hero locale={locale} />
      <Highlights locale={locale} />
      <Features locale={locale} />
      <Showcase locale={locale} />
      <AdminPreview locale={locale} />
      <Architecture locale={locale} />
      <QuickStart locale={locale} />
      <DocsCta locale={locale} />
    </>
  );
}
