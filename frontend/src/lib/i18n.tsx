"use client";
// Customer app is English-only (Latin). Persian lives only in the platform-admin console (/admin).
import { ReactNode } from "react";

const en: Record<string, string> = {
  dashboard: "Dashboard", invoices: "Invoices", review: "Review", contracts: "Contracts", claims: "Claims", settings: "Settings",
  signout: "Sign out", recovered: "Recovered cash", found: "Discrepancies found", audited: "Invoices audited", spend: "Total spend audited",
  success: "Success rate (recovered / claimed)", topVendors: "Top 5 vendors by overcharge", monthly: "Recovery by month",
  approve: "Approve", reject: "Reject", upload: "Upload invoices", drop: "Drop PDF, JPG or PNG invoices here or click to browse", empty: "Nothing here yet",
};
export const I18nProvider = ({ children }: { children: ReactNode }) => <>{children}</>;
export const useI18n = () => ({ lang: "en", t: (k: string) => en[k] ?? k });
