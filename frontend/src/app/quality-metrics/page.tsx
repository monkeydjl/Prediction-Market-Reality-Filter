"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

/**
 * Legacy route. The quality slice report now lives inside `/quality`.
 * Static export cannot emit server redirects, so the shell redirects on the client.
 */
export default function QualityMetricsRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/quality");
  }, [router]);

  return (
    <main id="main-content" className="mx-auto max-w-6xl px-4 py-6">
      <p className="text-sm text-muted-foreground">
        质量切片报告已并入质量运营，正在跳转…
      </p>
      <Link href="/quality" className="mt-2 inline-block text-sm text-primary underline">
        若未自动跳转，请点此前往质量运营
      </Link>
    </main>
  );
}
