import type { Metadata } from "next";
import "./globals.css";
import { AppNav } from "@/components/app-nav";
import { SWRProvider } from "@/components/providers/swr-provider";
import { ScrollToTop } from "@/components/scroll-to-top";

export const metadata: Metadata = {
  title: "Probability Watch — 事件情报与概率变化分析",
  description:
    "监控未来事件发生概率的变化，比对新闻、官方信息与交叉验证证据，决定是否值得继续人工跟踪，并复盘历史判断准确率。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh" className="dark h-full antialiased">
      <body className="min-h-full">
        <SWRProvider>
          <div className="min-h-screen">
            <AppNav />
            {children}
          </div>
          <ScrollToTop />
        </SWRProvider>
      </body>
    </html>
  );
}
