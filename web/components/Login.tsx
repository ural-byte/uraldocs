"use client";

import { FormEvent, useState } from "react";
import { useSession } from "@/components/AppShell";
import { copy, type Language } from "@/lib/i18n";

export default function Login({ lang }: { lang: Language }) {
  const t = copy[lang];
  const { signIn } = useSession();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await signIn(username.trim(), password);
      setPassword("");
    } catch {
      setError(t.loginError);
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return <main className="login-main">
    <section className="login-story" aria-labelledby="login-title">
      <span className="eyebrow light">{t.loginEyebrow}</span>
      <h1 id="login-title">{t.loginTitle}</h1>
      <p>{t.loginIntro}</p>
      <div className="story-decoration" aria-hidden="true"><span>01</span><span>02</span><span>03</span></div>
    </section>
    <section className="login-card" aria-labelledby="login-form-title">
      <span className="eyebrow">UralDocs</span>
      <h2 id="login-form-title">{t.loginPanelTitle}</h2>
      <p className="muted">{t.loginPanelText}</p>
      <form onSubmit={submit} className="form-stack">
        <label htmlFor="login-username">{t.username}</label>
        <input id="login-username" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required autoFocus />
        <label htmlFor="login-password">{t.password}</label>
        <input id="login-password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required />
        {error && <p role="alert" className="notice error">{error}</p>}
        <button type="submit" className="button-primary button-wide" disabled={busy}>{busy ? t.signingIn : t.signIn}<span aria-hidden="true">→</span></button>
      </form>
    </section>
  </main>;
}
