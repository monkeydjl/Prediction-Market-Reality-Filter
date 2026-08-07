import { EdgeDiscrepanciesTable } from "@/components/sports/edges/edgediscrepanciestable";
import { EdgeHistoryExplorer } from "@/components/sports/edges/edge-history-explorer";
import { DomainScopeBanner } from "@/components/common/domain-scope-banner";

export default function SportEdgesPage() {
  return (
    <main id="main-content" className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">体育 Edge 偏离</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        展示 Kernel 模型概率与体育市场隐含概率之间的最新偏离，按调整 Edge
        排序。需开启 PHASE7_EDGE_DETECTOR_ENABLED 与市场桥接。
      </p>
      <div className="mt-4">
        <DomainScopeBanner domain="sport" />
      </div>
      <div className="mt-6 space-y-8">
        <section aria-labelledby="edge-latest-heading">
          <h2 id="edge-latest-heading" className="text-base font-semibold">
            最新偏离
          </h2>
          <div className="mt-3">
            <EdgeDiscrepanciesTable />
          </div>
        </section>
        <section aria-labelledby="edge-history-heading">
          <h2 id="edge-history-heading" className="text-base font-semibold">
            单场 Edge 历史
          </h2>
          <div className="mt-3">
            <EdgeHistoryExplorer />
          </div>
        </section>
      </div>
    </main>
  );
}
