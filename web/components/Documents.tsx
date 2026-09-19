"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, type Document, type UiConfig } from "@/lib/api";
import { copy, messageForError, messageForUploadError, type Language } from "@/lib/i18n";

function formatBytes(bytes: number) {
  return `${Math.round(bytes / 1024 / 1024)} MB`;
}

export default function Documents({ lang }: { lang: Language }) {
  const t = copy[lang];
  const [documents, setDocuments] = useState<Document[]>([]);
  const [config, setConfig] = useState<UiConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);
  const listRequest = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    if (listRequest.current) return;
    const controller = new AbortController();
    listRequest.current = controller;
    if (mounted.current) setRefreshing(true);
    try {
      const rows = await api.documents(controller.signal);
      if (mounted.current) {
        setDocuments(rows);
        setError("");
      }
    } catch (reason) {
      if (mounted.current && !controller.signal.aborted) setError(messageForError(reason, lang));
    } finally {
      if (listRequest.current === controller) listRequest.current = null;
      if (mounted.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [lang]);

  useEffect(() => {
    mounted.current = true;
    api.config().then((value) => { if (mounted.current) setConfig(value); })
      .catch((reason) => { if (mounted.current) setError(messageForError(reason, lang)); });
    const firstLoad = window.setTimeout(() => void refresh(), 0);
    return () => {
      window.clearTimeout(firstLoad);
      mounted.current = false;
      listRequest.current?.abort();
      listRequest.current = null;
    };
  }, [lang, refresh]);

  const hasPending = documents.some((document) => document.status === "pending");
  useEffect(() => {
    if (!hasPending) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && !listRequest.current) void refresh();
    }, 4000);
    function visible() {
      if (document.visibilityState === "visible" && !listRequest.current) void refresh();
    }
    document.addEventListener("visibilitychange", visible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [hasPending, refresh]);

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setError("");
    setNotice("");
    if (!/\.(pdf|txt|md|markdown)$/i.test(file.name)) {
      setError(t.unsupportedFile);
      return;
    }
    if (config && file.size > config.max_upload_bytes) {
      setError(t.fileTooLarge);
      return;
    }
    setBusy(true);
    try {
      await api.uploadDocument(file);
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      setNotice(t.operationDone);
      await refresh();
    } catch (reason) {
      setError(messageForUploadError(reason, lang, file));
    } finally {
      setBusy(false);
    }
  }

  async function action(task: () => Promise<unknown>, confirmText?: string) {
    if (confirmText && !window.confirm(confirmText)) return;
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await task();
      setNotice(t.operationDone);
      await refresh();
    } catch (reason) {
      setError(messageForError(reason, lang));
    } finally {
      setBusy(false);
    }
  }

  return <div className="page-container">
    <header className="page-header">
      <div><span className="eyebrow">UralDocs / {t.documents}</span><h1>{t.documentsTitle}</h1><p>{t.documentsIntro}</p></div>
      <div className="header-actions"><button className="button-secondary" onClick={() => void refresh()} disabled={refreshing}>{refreshing ? t.refreshing : t.refresh}</button><button className="button-secondary" onClick={() => void action(api.reindexAll)} disabled={busy || documents.length === 0}>{t.reindexAll}</button></div>
    </header>
    {error && <div className="notice error" role="alert">{error}</div>}
    {notice && <div className="notice success" role="status">{notice}</div>}
    <div className="content-grid documents-grid">
      <section className="surface upload-panel" aria-labelledby="upload-title">
        <div className="section-heading"><span className="section-number">01</span><h2 id="upload-title">{t.uploadTitle}</h2></div>
        <p className="muted">{t.uploadHint}</p>
        <form onSubmit={upload} className="form-stack">
          <label htmlFor="document-file">{t.chooseFile}</label>
          <input ref={fileInput} id="document-file" type="file" accept=".pdf,.txt,.md,.markdown,application/pdf,text/plain,text/markdown" onChange={(event) => setFile(event.target.files?.[0] ?? null)} required />
          {config && <p className="field-note">{t.maxSize}: {formatBytes(config.max_upload_bytes)}</p>}
          <button type="submit" className="button-primary" disabled={busy || !file}>{busy ? t.uploading : t.upload}<span aria-hidden="true">↑</span></button>
        </form>
      </section>
      <section className="surface library-panel" aria-labelledby="documents-list-title">
        <div className="section-heading"><span className="section-number">02</span><h2 id="documents-list-title">{t.documentsList}</h2><span className="count-pill">{documents.length}</span></div>
        {hasPending && <p className="polling-note" role="status"><span className="pulse" aria-hidden="true" />{t.polling}</p>}
        {loading ? <p className="muted" aria-live="polite">{t.loading}</p> : documents.length === 0 ? <p className="empty-copy">{t.noDocuments}</p> :
          <ul className="document-list">
            {documents.map((item) => <li key={item.id} className="document-item">
              <div className="document-main">
                <div className="file-glyph" aria-hidden="true">{item.file_type.toUpperCase()}</div>
                <div className="document-copy"><h3>{item.filename}</h3><div className="document-meta"><span className={`status-chip ${item.status}`}>{item.status === "pending" ? t.pending : item.status === "ready" ? t.ready : t.failed}</span><span>{t.generation} {item.generation}</span><span>{item.index_current ? t.currentIndex : t.staleIndex}</span></div></div>
              </div>
              {item.error && <details className="error-detail"><summary>{t.technicalDetail}</summary><p>{item.error}</p></details>}
              <div className="item-actions"><button className="button-link" disabled={busy || item.status === "pending"} onClick={() => void action(() => api.reindexDocument(item.id))}>{t.reindexOne}</button><button className="button-link danger" disabled={busy} onClick={() => void action(() => api.deleteDocument(item.id), t.confirmDeleteDocument)}>{t.deleteDocument}</button></div>
            </li>)}
          </ul>}
      </section>
    </div>
  </div>;
}
