"use client";

interface FeatureDisabledBannerProps {
  flag: string;
  title?: string;
  message?: string;
  testId?: string;
}

/** Amber banner when a Phase feature flag is off (HTTP 503). */
export function FeatureDisabledBanner({
  flag,
  title = "功能未启用",
  message,
  testId = "feature-disabled",
}: FeatureDisabledBannerProps) {
  return (
    <div
      data-testid={testId}
      className="rounded border border-warn/40 bg-warn/10 p-3 text-sm text-warn"
      role="status"
    >
      <p className="font-medium">{title}</p>
      <p className="mt-1">
        {message ?? (
          <>
            接口返回 503。请在后端设置{" "}
            <code className="rounded bg-muted px-1">{flag}</code>
            {" "}并重启服务。
          </>
        )}
      </p>
    </div>
  );
}

export function isServiceUnavailable(error: unknown): boolean {
  if (!error) return false;
  if (typeof error === "object" && error !== null && "status" in error) {
    return (error as { status?: number }).status === 503;
  }
  const msg = error instanceof Error ? error.message : String(error);
  return msg.includes("503") || /disabled|not enabled|service unavailable/i.test(msg);
}
