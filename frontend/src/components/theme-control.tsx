"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

type Theme = "dark" | "light";
const KEY = "pmrf.theme";

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("light", theme === "light");
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export function ThemeControl() {
  const [theme, setTheme] = useState<Theme>("dark");

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const saved = window.localStorage.getItem(KEY);
      const prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
      const next: Theme = saved === "light" || (!saved && prefersLight) ? "light" : "dark";
      setTheme(next);
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    window.localStorage.setItem(KEY, next);
    applyTheme(next);
  }

  const Icon = theme === "dark" ? Moon : Sun;
  return (
    <button
      type="button"
      onClick={toggle}
      className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-muted-foreground transition-colors hover:text-foreground"
      title="切换亮色/暗色主题"
      aria-label="切换亮色/暗色主题"
    >
      <Icon className="size-3.5" aria-hidden="true" />
      {theme === "dark" ? "暗色" : "亮色"}
    </button>
  );
}
