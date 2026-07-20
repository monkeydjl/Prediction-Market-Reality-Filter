import { cn } from "@/lib/utils";

/** Shared form control styles (analyze / resolve / sports dashboards). */
export const inputClassName =
  "h-10 w-full rounded-md border border-border bg-secondary px-3 text-sm text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

export const inputSmClassName =
  "h-9 w-full rounded-md border border-border bg-secondary px-2 text-sm text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

export const selectClassName =
  "h-10 rounded-md border border-border bg-secondary px-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

export const selectSmClassName =
  "h-9 rounded-md border border-border bg-secondary px-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

/** Mobile-friendly table shell: horizontal scroll + touch overscroll. */
export const tableScrollClassName =
  "w-full overflow-x-auto overscroll-x-contain [-webkit-overflow-scrolling:touch]";

export function inputCls(extra?: string, size: "md" | "sm" = "md"): string {
  return cn(size === "sm" ? inputSmClassName : inputClassName, extra);
}

export function selectCls(extra?: string, size: "md" | "sm" = "md"): string {
  return cn(size === "sm" ? selectSmClassName : selectClassName, extra);
}
