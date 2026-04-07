import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "EvalMatrix - AI 驱动的产品智能评测系统",
  description: "面向 AI 全场景的自动化智能评测平台，支持多模型对比、Judge 自动评分、人工复核",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
      </head>
      <body className="antialiased">{children}</body>
    </html>
  );
}
