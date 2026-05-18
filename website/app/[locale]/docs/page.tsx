import { redirect } from "next/navigation";

export default function DocsIndex({ params }: { params: { locale: string } }) {
  redirect(`/${params.locale}/docs/architecture`);
}
