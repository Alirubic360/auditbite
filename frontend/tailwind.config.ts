import type { Config } from "tailwindcss";
export default {
  darkMode: "media",
  content: ["./src/**/*.{ts,tsx}"],
  theme: { extend: { colors: { good: { DEFAULT: "#16a34a", soft: "#dcfce7" }, bad: { DEFAULT: "#dc2626", soft: "#fee2e2" } } } },
  plugins: [],
} satisfies Config;
