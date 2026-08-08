import Link from "next/link";
import { EngineConsole } from "@/components/sports/world-cup/engine-console";
import { SectionErrorBoundary } from "@/components/section-error-boundary";

export default function WorldCupEnginePage() {
  return (
    <main id="main-content" className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">世界杯引擎控制台</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        批量预测、引擎切换、AI 优化与自动调优的运营入口。所有写操作需要有效的操作员
        API Key，覆盖全量预测的批量操作还需二次确认。
      </p>
      <p className="mt-2 text-sm">
        <Link
          href="/sports/world-cup"
          className="text-primary hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          ← 返回世界杯看板
        </Link>
      </p>
      <div className="mt-6">
        <SectionErrorBoundary title="引擎控制台">
          <EngineConsole />
        </SectionErrorBoundary>
      </div>
    </main>
  );
}
