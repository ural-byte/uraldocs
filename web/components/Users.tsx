"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, type User } from "@/lib/api";
import { copy, messageForError, type Language } from "@/lib/i18n";

export default function Users({ lang }: { lang: Language }) {
  const t = copy[lang];
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [resetId, setResetId] = useState<number | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    api.users().then((rows) => { if (active) setUsers(rows); })
      .catch((reason) => { if (active) setError(messageForError(reason, lang)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [lang]);

  async function reload() {
    setUsers(await api.users());
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await api.createUser(username.trim(), password);
      setUsername("");
      setNotice(t.operationDone);
      await reload();
    } catch (reason) {
      setError(messageForError(reason, lang));
    } finally {
      setPassword("");
      setBusy(false);
    }
  }

  async function disable(user: User) {
    if (!window.confirm(t.confirmDisable)) return;
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await api.disableUser(user.id);
      setNotice(t.operationDone);
      await reload();
    } catch (reason) {
      setError(messageForError(reason, lang));
    } finally {
      setBusy(false);
    }
  }

  async function reset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (resetId === null) return;
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await api.resetPassword(resetId, newPassword);
      setResetId(null);
      setNotice(t.operationDone);
      await reload();
    } catch (reason) {
      setError(messageForError(reason, lang));
    } finally {
      setNewPassword("");
      setBusy(false);
    }
  }

  function chooseReset(id: number | null) {
    setResetId(id);
    setNewPassword("");
    setError("");
  }

  return <div className="page-container">
    <header className="page-header"><div><span className="eyebrow">UralDocs / {t.users}</span><h1>{t.usersTitle}</h1><p>{t.usersIntro}</p></div></header>
    {error && <div className="notice error" role="alert">{error}</div>}
    {notice && <div className="notice success" role="status">{notice}</div>}
    <div className="content-grid users-grid">
      <section className="surface user-create" aria-labelledby="create-user-title">
        <div className="section-heading"><span className="section-number">01</span><h2 id="create-user-title">{t.createUser}</h2></div>
        <form className="form-stack" onSubmit={create}>
          <label htmlFor="new-username">{t.username}</label>
          <input id="new-username" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="off" pattern="[a-zA-Z0-9_.-]+" maxLength={100} required />
          <label htmlFor="new-user-password">{t.password}</label>
          <input id="new-user-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" minLength={12} required />
          <p className="field-note">{t.passwordHint}</p>
          <button className="button-primary" type="submit" disabled={busy}>{busy ? t.creating : t.create}<span aria-hidden="true">＋</span></button>
        </form>
      </section>
      <section className="surface user-library" aria-labelledby="users-list-title">
        <div className="section-heading"><span className="section-number">02</span><h2 id="users-list-title">{t.usersList}</h2><span className="count-pill">{users.length}</span></div>
        {loading ? <p className="muted" aria-live="polite">{t.loading}</p> : users.length === 0 ? <p className="empty-copy">{t.noUsers}</p> :
          <ul className="user-list">{users.map((user) => <li key={user.id} className="user-item">
            <div className="user-line"><div className="avatar" aria-hidden="true">{user.username.slice(0, 1).toUpperCase()}</div><div><h3>{user.username}</h3><span className={user.is_active ? "status-chip ready" : "status-chip failed"}>{user.is_active ? t.active : t.disabled}</span></div></div>
            <div className="item-actions"><button className="button-link" type="button" onClick={() => chooseReset(user.id)} disabled={busy}>{t.resetPassword}</button><button className="button-link danger" type="button" onClick={() => void disable(user)} disabled={busy || !user.is_active}>{t.disable}</button></div>
            {resetId === user.id && <form className="reset-form" onSubmit={reset}>
              <label htmlFor={`reset-${user.id}`}>{t.newPassword}</label>
              <input id={`reset-${user.id}`} type="password" autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} minLength={12} required />
              <p className="field-note">{t.resetHint}</p>
              <div className="form-actions"><button type="submit" className="button-primary" disabled={busy}>{t.savePassword}</button><button type="button" className="button-secondary" onClick={() => chooseReset(null)}>{t.cancel}</button></div>
            </form>}
          </li>)}</ul>}
      </section>
    </div>
  </div>;
}
