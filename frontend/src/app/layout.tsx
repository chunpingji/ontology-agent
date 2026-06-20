import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SLPRA Platform — 临床药物智能辅助生产平台",
  description: "Clinical Drug Intelligent Assisted Production Platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh">
      <body className="min-h-screen bg-gray-50 text-gray-900 antialiased">
        {children}
      </body>
    </html>
  );
}
