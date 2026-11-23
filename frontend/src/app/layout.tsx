import "./globals.css";
import type { Metadata } from "next";
import { I18nProvider } from "@/lib/i18n";
import { ToastProvider } from "@/components/ui";

export const metadata: Metadata = { title: "AuditBite — restaurant supplier invoice audit", description: "Find and recover supplier billing variances." };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (<html lang="en" dir="ltr"><body><I18nProvider><ToastProvider>{children}</ToastProvider></I18nProvider></body></html>);
}
