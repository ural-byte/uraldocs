import ChatWorkspace from "@/components/ChatWorkspace";
import type { Language } from "@/lib/i18n";

export default async function ChatHome({ params }: { params: Promise<{ lang: Language }> }) {
  const { lang } = await params;
  return <ChatWorkspace lang={lang} conversationId={null} />;
}
