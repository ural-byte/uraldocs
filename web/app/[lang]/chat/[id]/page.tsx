import ChatWorkspace from "@/components/ChatWorkspace";
import type { Language } from "@/lib/i18n";

export default async function ConversationPage({ params }: { params: Promise<{ lang: Language; id: string }> }) {
  const { lang, id } = await params;
  return <ChatWorkspace key={id} lang={lang} conversationId={Number(id)} />;
}
