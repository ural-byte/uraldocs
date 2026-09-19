import type { Metadata } from "next";
import "./style.css";

export const metadata: Metadata = { title: "UralDocs", description: "Внутренняя база знаний" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ru"><body>{children}</body></html>;
}
