import Link from "next/link";
import { IngestConsole } from "@/components/sports/world-cup/ingest-console";
import { SectionErrorBoundary } from "@/components/section-error-boundary";

export default function WorldCupIngestPage() {
  return (
    <main id="main-content" className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">世界杯数据接入</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        供应商 Feed、本地数据文件与手工载荷的导入入口，附带事实库查询与赛事结算。所有写操作需要有效的操作员
        API Key，导入还需二次确认。
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
        <SectionErrorBoundary title="数据接入控制台">
          <IngestConsole />
        </SectionErrorBoundary>
      </div>
    </main>
  );
}
