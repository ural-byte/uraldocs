import Users from "@/components/Users";
import type { Language } from "@/lib/i18n";

export default async function UsersPage({ params }: { params: Promise<{ lang: Language }> }) {
  const { lang } = await params;
  return <Users lang={lang} />;
}
