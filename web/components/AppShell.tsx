"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { api, ApiError, type User } from "@/lib/api";
import { copy, type Language, messageForError } from "@/lib/i18n";

type SessionState = { status: "loading" | "guest" | "ready"; user: User | null };
type SessionContextValue = SessionState & {
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<SessionContextValue | null>(null);

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) throw new Error("SessionContext is unavailable");
  return context;
}

function LanguageLinks({ lang }: { lang: Language }) {
  const path = usePathname();
  const router = useRouter();
  const t = copy[lang];
  function destination(target: Language) {
    return path.replace(/^\/(ru|en)(?=\/|$)/, `/${target}`);
  }
  function switchLanguage(event: React.MouseEvent<HTMLAnchorElement>, target: Language) {
    event.preventDefault();
    router.push(`${destination(target)}${window.location.search}${window.location.hash}`);
  }
  return <nav className="language-links" aria-label={t.language}>
    <a href={destination("ru")} lang="ru" aria-current={lang === "ru" ? "page" : undefined} onClick={(event) => switchLanguage(event, "ru")}>RU</a>
    <span aria-hidden="true">/</span>
    <a href={destination("en")} lang="en" aria-current={lang === "en" ? "page" : undefined} onClick={(event) => switchLanguage(event, "en")}>EN</a>
  </nav>;
}

export default function AppShell({ lang, children }: { lang: Language; children: React.ReactNode }) {
  const t = copy[lang];
  const router = useRouter();
  const path = usePathname();
  const home = path === `/${lang}`;
  const adminRoute = path.startsWith(`/${lang}/documents`) || path.startsWith(`/${lang}/users`);
  const [session, setSession] = useState<SessionState>({ status: "loading", user: null });
  const [shellError, setShellError] = useState("");
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    let active = true;
    api.me().then((user) => {
      if (active) setSession({ status: "ready", user });
    }).catch(() => {
      if (active) setSession({ status: "guest", user: null });
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (session.status === "guest" && !home) router.replace(`/${lang}`);
    if (session.status === "ready" && home) router.replace(`/${lang}/chat`);
  }, [home, lang, router, session.status]);

  async function signIn(username: string, password: string) {
    const user = await api.login(username, password);
    setSession({ status: "ready", user });
    router.replace(`/${lang}/chat`);
  }

  async function signOut() {
    setShellError("");
    setSigningOut(true);
    try {
      await api.logout();
      setSession({ status: "guest", user: null });
      router.replace(`/${lang}`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setSession({ status: "guest", user: null });
        router.replace(`/${lang}`);
      } else {
        setShellError(messageForError(error, lang));
      }
    } finally {
      setSigningOut(false);
    }
  }

  const context: SessionContextValue = { ...session, signIn, signOut };
  const nav = [
    { href: `/${lang}/chat`, label: t.chat, icon: "◈" },
    ...(session.user?.role === "admin" ? [
      { href: `/${lang}/documents`, label: t.documents, icon: "▤" },
      { href: `/${lang}/users`, label: t.users, icon: "◎" },
    ] : []),
  ];

  return <SessionContext.Provider value={context}>
    {home ? <div className="login-frame">
      <header className="public-header">
        <Link href={`/${lang}`} className="wordmark"><span className="logo-mark">U</span><span>UralDocs</span></Link>
        <LanguageLinks lang={lang} />
      </header>
      {session.status === "loading" || session.status === "ready" ? <main className="loading-screen" aria-live="polite">{t.loading}</main> : children}
    </div> : <div className="app-frame">
      <aside className="sidebar">
        <div className="sidebar-top">
          <Link href={`/${lang}/chat`} className="wordmark"><span className="logo-mark">U</span><span>UralDocs</span></Link>
          <p className="sidebar-tag">{t.brandTag}</p>
        </div>
        <nav className="primary-nav" aria-label={t.brandTag}>
          {nav.map((item) => <Link key={item.href} href={item.href} className={path.startsWith(item.href) ? "nav-link active" : "nav-link"} aria-current={path.startsWith(item.href) ? "page" : undefined}>
            <span aria-hidden="true" className="nav-icon">{item.icon}</span>{item.label}
          </Link>)}
        </nav>
        <div className="sidebar-footer">
          <div className="account-block"><small>{t.account}</small><strong>{session.user?.username}</strong><span>{session.user?.role === "admin" ? t.admin : t.user}</span></div>
          <div className="sidebar-actions"><LanguageLinks lang={lang} /><button className="button-quiet" onClick={signOut} disabled={signingOut}>{signingOut ? t.signingOut : t.signOut}</button></div>
          {shellError && <p className="error-text" role="alert">{shellError}</p>}
        </div>
      </aside>
      <div className="mobile-bar">
        <Link href={`/${lang}/chat`} className="wordmark"><span className="logo-mark">U</span><span>UralDocs</span></Link>
        <div className="mobile-actions"><LanguageLinks lang={lang} /><button className="button-quiet" onClick={signOut} disabled={signingOut}>{t.signOut}</button></div>
      </div>
      <nav className="mobile-nav" aria-label={t.brandTag}>
        {nav.map((item) => <Link key={item.href} href={item.href} className={path.startsWith(item.href) ? "active" : ""} aria-current={path.startsWith(item.href) ? "page" : undefined}>{item.label}</Link>)}
      </nav>
      <main className="workspace" id="content">
        {session.status !== "ready" ? <div className="loading-screen" aria-live="polite">{t.loading}</div> :
          adminRoute && session.user?.role !== "admin" ? <div className="surface access-denied"><h1>{t.accessDenied}</h1><Link href={`/${lang}/chat`}>{t.backToChat}</Link></div> : children}
      </main>
    </div>}
  </SessionContext.Provider>;
}
