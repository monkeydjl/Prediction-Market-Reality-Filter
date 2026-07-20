import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { tableScrollClassName } from "@/lib/ui-classes";

interface ScrollableTableProps {
  children: ReactNode;
  /** Accessible name for the scroll region / table container */
  "aria-label"?: string;
  className?: string;
  testId?: string;
}

/**
 * Wraps wide tables for mobile: horizontal scroll without clipping page layout.
 */
export function ScrollableTable({
  children,
  "aria-label": ariaLabel,
  className,
  testId,
}: ScrollableTableProps) {
  return (
    <div
      role="region"
      aria-label={ariaLabel}
      tabIndex={0}
      data-testid={testId}
      className={cn(tableScrollClassName, className)}
    >
      {children}
    </div>
  );
}
