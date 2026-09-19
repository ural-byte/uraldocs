"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { api, ApiError, type ChatMessage, type Conversation, type ConversationDetail, type Source, type UiConfig } from "@/lib/api";
import { copy, messageForError, type Language } from "@/lib/i18n";

function answerParts(text: string, sources: Source[]) {
  return text.split(/(\[c\d+\])/g).map((part, index) => {
    const source = sources.find((item) => `[${item.citation_id}]` === part);
    return source ? <a key={index} className="citation-link" href={`#source-${source.id}`}>{part}</a> : <span key={index}>{part}</span>;
  });
}

function SourceCard({ source, lang }: { source: Source; lang: Language }) {
  const t = copy[lang];
  const location = source.page_number !== null ? `${t.page} ${source.page_number}` :
    source.line_start === source.line_end ? `${t.line} ${source.line_start}` : `${t.lines} ${source.line_start}–${source.line_end}`;
  return <li className="source-card" id={`source-${source.id}`}>
    <div className="source-top"><span className="source-icon" aria-hidden="true">↗</span><div><strong>{source.filename}</strong><span>{location}</span></div></div>
    {source.deleted || source.excerpt === null ? <p className="deleted-note">{t.deletedSource}</p> :
      <details><summary>{t.excerpt}</summary><p className="source-excerpt">{source.excerpt}</p></details>}
  </li>;
}

function MessageCard({ message, lang }: { message: ChatMessage; lang: Language }) {
  const t = copy[lang];
  const label = message.role === "user" ? t.question : message.kind === "demo" ? t.demoAnswer :
    message.kind === "insufficient" ? t.insufficient : message.kind === "index_unavailable" ? t.indexUnavailable : t.aiAnswer;
  return <article className={`message-card ${message.role} ${message.kind}`}>
    <div className="message-label"><span className="message-dot" aria-hidden="true" />{label}</div>
    <p className="message-text">{message.kind === "answer" ? answerParts(message.text, message.sources) : message.text}</p>
    {message.sources.length > 0 && <section className="sources-block" aria-label={t.sources}>
      <h3>{t.sources} <span>{message.sources.length}</span></h3>
      <ul className="source-list">{message.sources.map((source) => <SourceCard source={source} lang={lang} key={source.id} />)}</ul>
    </section>}
  </article>;
}

export default function ChatWorkspace({ lang, conversationId }: { lang: Language; conversationId: number | null }) {
  const t = copy[lang];
  const router = useRouter();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [config, setConfig] = useState<UiConfig | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(conversationId !== null && Number.isSafeInteger(conversationId) && conversationId > 0);
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [pending, setPending] = useState<Record<number, boolean>>({});
  const [askErrors, setAskErrors] = useState<Record<number, string>>({});
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    api.conversations().then((items) => { if (active) setConversations(items); })
      .catch((reason) => { if (active) setError(messageForError(reason, lang)); })
      .finally(() => { if (active) setListLoading(false); });
    api.config().then((value) => { if (active) setConfig(value); })
      .catch((reason) => { if (active) setError(messageForError(reason, lang)); });
    return () => { active = false; };
  }, [lang]);

  useEffect(() => {
    if (conversationId === null || !Number.isSafeInteger(conversationId) || conversationId < 1) return;
    const controller = new AbortController();
    api.conversation(conversationId, controller.signal)
      .then((item) => { if (!controller.signal.aborted) setDetail(item); })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason instanceof ApiError && reason.status === 404 ? t.conversationMissing : messageForError(reason, lang));
      })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [conversationId, lang, t.conversationMissing]);

  async function createConversation() {
    setError("");
    setBusy(true);
    try {
      const created = await api.createConversation(t.newConversation);
      setConversations((items) => [created, ...items]);
      router.push(`/${lang}/chat/${created.id}`);
    } catch (reason) {
      setError(messageForError(reason, lang));
    } finally {
      setBusy(false);
    }
  }

  async function deleteConversation() {
    if (conversationId === null || !window.confirm(t.confirmDeleteConversation)) return;
    setError("");
    setBusy(true);
    try {
      await api.deleteConversation(conversationId);
      setConversations((items) => items.filter((item) => item.id !== conversationId));
      setDetail(null);
      router.replace(`/${lang}/chat`);
    } catch (reason) {
      setError(messageForError(reason, lang));
    } finally {
      setBusy(false);
    }
  }

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const id = conversationId;
    if (id === null || pending[id]) return;
    const question = (drafts[id] ?? "").trim();
    if (!question) return;
    setAskErrors((items) => ({ ...items, [id]: "" }));
    setPending((items) => ({ ...items, [id]: true }));
    try {
      const pair = await api.ask(id, question);
      setDetail((current) => {
        if (!current || current.id !== id || current.messages.some((item) => item.id === pair.user.id)) return current;
        return { ...current, messages: [...current.messages, pair.user, pair.assistant] };
      });
      setDrafts((items) => ({ ...items, [id]: "" }));
      setConversations((items) => items.map((item) => item.id === id ? { ...item, updated_at: pair.assistant.created_at } : item));
    } catch (reason) {
      const message = reason instanceof ApiError && reason.status === 504 ? t.providerTimeout :
        reason instanceof ApiError && reason.status === 502 ? t.providerError : messageForError(reason, lang);
      setAskErrors((items) => ({ ...items, [id]: message }));
    } finally {
      setPending((items) => ({ ...items, [id]: false }));
    }
  }

  const draft = conversationId === null ? "" : drafts[conversationId] ?? "";
  const askError = conversationId === null ? "" : askErrors[conversationId] ?? "";
  const sending = conversationId !== null && Boolean(pending[conversationId]);

  const invalidId = conversationId !== null && (!Number.isSafeInteger(conversationId) || conversationId < 1);

  return <div className="page-container chat-page">
    <header className="page-header"><div><span className="eyebrow">UralDocs / {t.chat}</span><h1>{t.chatTitle}</h1><p>{t.chatIntro}</p></div><div className="header-actions"><span className="mode-pill">{config?.kb_mode === "demo" ? t.modeDemo : config?.kb_mode === "real_ai" ? t.modeReal : t.loading}</span></div></header>
    {(error || invalidId) && <div className="notice error" role="alert">{invalidId ? t.conversationMissing : error}</div>}
    <div className="chat-layout">
      <aside className="conversation-panel surface" aria-label={t.conversations}>
        <button className="button-primary button-wide" onClick={() => void createConversation()} disabled={busy}>{t.newConversation}<span aria-hidden="true">＋</span></button>
        <div className="conversation-heading"><h2>{t.conversations}</h2><span className="count-pill">{conversations.length}</span></div>
        {listLoading ? <p className="muted">{t.loading}</p> : conversations.length === 0 ? <p className="empty-copy">{t.noConversations}</p> :
          <nav aria-label={t.conversations}><ul className="conversation-list">{conversations.map((item) => <li key={item.id}>
            <Link href={`/${lang}/chat/${item.id}`} className={item.id === conversationId ? "conversation-link active" : "conversation-link"} aria-current={item.id === conversationId ? "page" : undefined}><span aria-hidden="true">◻</span><span>{item.title}</span><span aria-hidden="true">›</span></Link>
          </li>)}</ul></nav>}
      </aside>
      <section className="conversation-main surface" aria-labelledby="current-conversation-title">
        {conversationId === null ? <div className="conversation-empty"><span className="empty-symbol" aria-hidden="true">✳</span><h2 id="current-conversation-title">{t.selectConversation}</h2><p>{t.selectConversationText}</p><button className="button-primary" onClick={() => void createConversation()} disabled={busy}>{t.newConversation}</button></div> :
          detailLoading ? <p className="muted" aria-live="polite">{t.loading}</p> : !detail ? <div className="conversation-empty"><h2 id="current-conversation-title">{t.conversationMissing}</h2><Link href={`/${lang}/chat`}>{t.backToChat}</Link></div> : <>
            <div className="conversation-toolbar"><div><span className="eyebrow">{t.chat}</span><h2 id="current-conversation-title">{detail.title}</h2></div><button className="button-link danger" onClick={() => void deleteConversation()} disabled={busy}>{t.deleteConversation}</button></div>
            <div className="messages" aria-live="polite">{detail.messages.length === 0 ? <div className="messages-empty"><span aria-hidden="true">✳</span><p>{t.noMessages}</p></div> : detail.messages.map((message) => <MessageCard key={message.id} message={message} lang={lang} />)}
              {sending && <div className="pending-answer" role="status"><span className="pulse" aria-hidden="true" />{t.sending}</div>}
            </div>
            <form onSubmit={ask} className="question-form">
              <label htmlFor="question-input">{t.question}</label>
              <textarea id="question-input" value={draft} onChange={(event) => setDrafts((items) => ({ ...items, [conversationId]: event.target.value }))} placeholder={t.questionPlaceholder} maxLength={config?.chat_max_question_chars ?? 10000} rows={3} disabled={sending} required />
              <div className="question-footer"><span className="field-note">{t.questionHint} · {draft.length}/{config?.chat_max_question_chars ?? 10000}</span><button type="submit" className="button-primary" disabled={sending || !draft.trim()}>{sending ? t.sending : askError ? t.retry : t.send}<span aria-hidden="true">→</span></button></div>
              {askError && <p className="notice error" role="alert">{askError}</p>}
            </form>
          </>}
      </section>
    </div>
  </div>;
}
