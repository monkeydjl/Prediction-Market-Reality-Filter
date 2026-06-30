import type { Metadata } from "next";
import "./globals.css";
import { SWRProvider } from "@/components/providers/swr-provider";
import { ScrollToTop } from "@/components/scroll-to-top";

export const metadata: Metadata = {
  title: "Probability Watch — 事件情报与概率变化分析",
  description:
    "监控未来事件发生概率的变化，比对新闻、官方信息与交叉验证证据，决定是否值得继续人工跟踪，并复盘历史判断准确率。",
};

const THEME_INIT_SCRIPT = `
(() => {
  try {
    const saved = window.localStorage.getItem("pmrf.theme");
    const prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
    const theme = saved === "light" || (!saved && prefersLight) ? "light" : "dark";
    document.documentElement.classList.toggle("light", theme === "light");
    document.documentElement.classList.toggle("dark", theme === "dark");
  } catch {
    document.documentElement.classList.add("dark");
  }
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="zh"
      className="h-full antialiased"
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-full">
        <SWRProvider>
          {children}
          <ScrollToTop />
        </SWRProvider>
      </body>
    </html>
  );
}
