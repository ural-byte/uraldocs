import Documents from "@/components/Documents";
import type { Language } from "@/lib/i18n";

export default async function DocumentsPage({ params }: { params: Promise<{ lang: Language }> }) {
  const { lang } = await params;
  return <Documents lang={lang} />;
}
