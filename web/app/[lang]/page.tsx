import Login from "@/components/Login";
import type { Language } from "@/lib/i18n";

export default async function LocaleHome({ params }: { params: Promise<{ lang: Language }> }) {
  const { lang } = await params;
  return <Login lang={lang} />;
}
