import type { Metadata } from "next";
import { notFound } from "next/navigation";
import AppShell from "@/components/AppShell";
import { isLanguage } from "@/lib/i18n";
import "../style.css";

export const metadata: Metadata = { title: "UralDocs", description: "Внутренняя база знаний / Internal knowledge base" };

export default async function LocaleLayout({
  children,
  params,
}: Readonly<{ children: React.ReactNode; params: Promise<{ lang: string }> }>) {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();
  return <html lang={lang}><body><AppShell lang={lang}>{children}</AppShell></body></html>;
}
