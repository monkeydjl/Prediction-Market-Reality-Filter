import { SectionErrorBoundary } from "@/components/section-error-boundary";
import { DailyIntelDigestDashboard } from "@/components/dashboard/daily-intel-digest-dashboard";

export default function DigestPage() {
  return (
    <main id="main-content" className="mx-auto max-w-6xl px-4 py-6">
      <SectionErrorBoundary title="每日情报摘要">
        <DailyIntelDigestDashboard />
      </SectionErrorBoundary>
    </main>
  );
}
