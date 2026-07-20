import { EdgeDiscrepanciesTable } from "@/components/sports/edges/edgediscrepanciestable";
import { DomainScopeBanner } from "@/components/common/domain-scope-banner";

export default function SportEdgesPage() {
  return (
    <main className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">体育 Edge 偏离</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        展示 Kernel 模型概率与体育市场隐含概率之间的最新偏离，按调整 Edge
        排序。需开启 PHASE7_EDGE_DETECTOR_ENABLED 与市场桥接。
      </p>
      <div className="mt-4">
        <DomainScopeBanner domain="sport" />
      </div>
      <div className="mt-6">
        <EdgeDiscrepanciesTable />
      </div>
    </main>
  );
}
