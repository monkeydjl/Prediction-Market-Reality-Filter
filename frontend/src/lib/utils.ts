import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function pct(v: number | null | undefined): string {
  return `${Number(v ?? 0).toFixed(1)}%`;
}

export function signed(n: number | null | undefined): string {
  const v = Number(n ?? 0);
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}`;
}

export function deltaTone(change: number): "up" | "down" | "flat" {
  return change > 0 ? "up" : change < 0 ? "down" : "flat";
}

export function levelTone(level?: string): "high" | "medium" | "low" {
  const l = String(level ?? "").toUpperCase();
  return l === "HIGH" ? "high" : l === "MEDIUM" ? "medium" : "low";
}
