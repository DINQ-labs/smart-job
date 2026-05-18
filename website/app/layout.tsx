import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "smart-job — 多平台求职/招聘自动化系统",
  description:
    "smart-job 是一套多平台（BOSS直聘 / LinkedIn / Indeed）求职与招聘自动化系统：浏览器扩展 + 对话式 Agent + 任务编排。",
  icons: { icon: "/favicon.svg" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh">
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
