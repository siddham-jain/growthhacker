import type { Metadata } from "next";
import { Providers } from "@/components/shell/providers";
import { devanagari, geistMono, instrumentSerif, inter, schibsted } from "./fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: "GrowthHacker",
  description: "What to publish on YouTube next, backed by evidence from your niche.",
};

// must match THEME_STORAGE_KEY in the vendored boardui theme toggle (a client module, so not importable here)
const THEME_STORAGE_KEY = "boardui:theme";

// applies the saved theme before first paint so dark mode doesn't flash light
const themeScript = `try{if(localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)})==="dark")document.documentElement.classList.add("dark")}catch(e){}`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  const fontVariables = [inter, schibsted, instrumentSerif, geistMono, devanagari].map((font) => font.variable).join(" ");
  return (
    <html lang="en" className={`${fontVariables} h-full`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-full">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
