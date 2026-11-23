"use client";
import { createContext, useContext, useEffect, useState, ReactNode } from "react";

const dict: Record<string, Record<string, string>> = {
  en: { dashboard: "Dashboard", invoices: "Invoices", review: "Review", contracts: "Contracts", claims: "Claims", settings: "Settings",
        signout: "Sign out", recovered: "Recovered cash", found: "Discrepancies found", audited: "Invoices audited", spend: "Total spend audited",
        success: "Success rate (recovered / claimed)", topVendors: "Top 5 vendors by overcharge", monthly: "Recovery by month",
        approve: "Approve", reject: "Reject", upload: "Upload invoices", drop: "Drop PDF, JPG or PNG invoices here or click to browse", empty: "Nothing here yet" },
  fa: { dashboard: "داشبورد", invoices: "فاکتورها", review: "بازبینی", contracts: "قراردادها", claims: "ادعاها", settings: "تنظیمات",
        signout: "خروج", recovered: "وجه بازیابی‌شده", found: "مغایرت‌های یافت‌شده", audited: "فاکتورهای حسابرسی‌شده", spend: "مجموع هزینه حسابرسی‌شده",
        success: "نرخ موفقیت (بازیابی ÷ ادعا)", topVendors: "۵ تأمین‌کننده با بیشترین اضافه‌دریافت", monthly: "بازیابی ماهانه",
        approve: "تأیید", reject: "رد", upload: "بارگذاری فاکتور", drop: "فایل PDF، JPG یا PNG را اینجا رها کنید یا کلیک کنید", empty: "هنوز چیزی نیست" },
};
const Ctx = createContext<{ lang: string; setLang: (l: string) => void; t: (k: string) => string }>({ lang: "en", setLang: () => {}, t: (k) => k });

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState("en");
  useEffect(() => { const l = localStorage.getItem("ab_lang"); if (l && dict[l]) setLangState(l); }, []);
  useEffect(() => { document.documentElement.lang = lang; document.documentElement.dir = lang === "fa" || lang === "ar" ? "rtl" : "ltr"; }, [lang]);
  const setLang = (l: string) => { localStorage.setItem("ab_lang", l); setLangState(l); };
  return <Ctx.Provider value={{ lang, setLang, t: (k) => dict[lang]?.[k] ?? dict.en[k] ?? k }}>{children}</Ctx.Provider>;
}
export const useI18n = () => useContext(Ctx);
