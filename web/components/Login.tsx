"use client";

import { FormEvent, useEffect, useState } from "react";

type User = { id: number; username: string; role: "admin" | "user" };
const copy = {
  ru: { title: "Вход в UralDocs", username: "Логин", password: "Пароль", login: "Войти", logout: "Выйти", welcome: "Вы вошли как", error: "Не удалось войти", role: "Роль" },
  en: { title: "Sign in to UralDocs", username: "Username", password: "Password", login: "Sign in", logout: "Sign out", welcome: "Signed in as", error: "Sign in failed", role: "Role" },
};

export default function Login({ language }: { language: "ru" | "en" }) {
  const t = copy[language];
  const [user, setUser] = useState<User | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/auth/me", { cache: "no-store" }).then((response) => response.ok ? response.json() : null).then(setUser).catch(() => setUser(null));
  }, []);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username, password }),
      });
      if (!response.ok) throw new Error();
      setUser(await response.json());
      setPassword("");
    } catch { setError(t.error); }
  }

  async function signOut() {
    const response = await fetch("/api/auth/logout", { method: "POST" });
    if (response.ok) setUser(null);
  }

  return <main className="shell">
    <nav><strong>UralDocs</strong><span><a href="/ru" lang="ru">RU</a> / <a href="/en" lang="en">EN</a></span></nav>
    <section className="card">
      <h1>{t.title}</h1>
      {user ? <><p>{t.welcome} <strong>{user.username}</strong></p><p>{t.role}: {user.role}</p><button onClick={signOut}>{t.logout}</button></> :
        <form onSubmit={signIn}>
          <label>{t.username}<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
          <label>{t.password}<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
          {error && <p role="alert" className="error">{error}</p>}
          <button type="submit">{t.login}</button>
        </form>}
    </section>
  </main>;
}
