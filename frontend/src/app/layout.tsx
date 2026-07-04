import type { Metadata } from "next";
// Self-hosted fonts (vendored via @fontsource — build-time bundled woff2, zero runtime CDN).
// Latin UI face + CJK/zh-CN face; consumed through --font-sans (globals.css). FR-028 / SC-009.
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@fontsource/noto-sans-sc/400.css";
import "@fontsource/noto-sans-sc/500.css";
import "@fontsource/noto-sans-sc/700.css";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "SLPRA Platform — 临床药物智能辅助生产平台",
  description: "Clinical Drug Intelligent Assisted Production Platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh">
      <body className="min-h-screen bg-background text-foreground antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
