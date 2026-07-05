import type { QualityReportSlice } from "@/lib/api";

const GRADE_TONE: Record<string, string> = {
  EXCELLENT: "text-pos",
  GOOD: "text-pos",
  ACCEPTABLE: "text-muted-foreground",
  POOR: "text-neg",
  RANDOM_LEVEL: "text-neg",
  no_data: "text-muted-foreground",
};

const GRADE_LABEL: Record<string, string> = {
  EXCELLENT: "优秀",
  GOOD: "良好",
  ACCEPTABLE: "可接受",
  POOR: "差",
  RANDOM_LEVEL: "随机水平",
  no_data: "无数据",
};

const SLICE_LABEL: Record<string, string> = {
  prediction_market: "预测市场",
  unknown: "未知",
  "<missing>": "缺失",
};

function fmtAcc(acc: number | null): string {
  if (acc === null) return "—";
  return `${(acc * 100).toFixed(1)}%`;
}

function fmtBrier(b: number | null): string {
  if (b === null) return "—";
  return b.toFixed(4);
}

function sliceLabel(k: string): string {
  return SLICE_LABEL[k] ?? k;
}

export function ReportSliceTable({
  title,
  subtitle,
  slices,
}: {
  title: string;
  subtitle: string;
  slices: Record<string, QualityReportSlice>;
}) {
  const keys = Object.keys(slices).sort((a, b) => slices[b].n - slices[a].n);
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div>
        <h2 className="text-base font-semibold">{title}</h2>
        <p className="mt-0.5 font-mono text-xs text-muted-foreground">{subtitle}</p>
      </div>
      {keys.length === 0 ? (
        <p className="text-sm text-muted-foreground">无数据</p>
      ) : (
        <div className="max-h-72 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1.5 text-left font-medium">切片</th>
                <th className="py-1.5 text-right font-medium">n</th>
                <th className="py-1.5 text-right font-medium">准确率</th>
                <th className="py-1.5 text-right font-medium">T/F/N</th>
                <th className="py-1.5 text-right font-medium">Brier</th>
                <th className="py-1.5 text-left font-medium">等级</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => {
                const s = slices[k];
                const tfn = `${s.direction_correct_true}/${s.direction_correct_false}/${s.direction_correct_none}`;
                const grade = s.brier.grade ?? "no_data";
                const tone = GRADE_TONE[grade] ?? "";
                return (
                  <tr key={k} className="border-t border-border">
                    <td className="py-1.5 font-mono">{sliceLabel(k)}</td>
                    <td className="py-1.5 text-right font-mono tabular-nums">{s.n}</td>
                    <td className="py-1.5 text-right font-mono tabular-nums">{fmtAcc(s.direction_accuracy)}</td>
                    <td className="py-1.5 text-right font-mono tabular-nums text-muted-foreground">{tfn}</td>
                    <td className="py-1.5 text-right font-mono tabular-nums">{fmtBrier(s.brier.brier_score)}</td>
                    <td className={`py-1.5 font-mono ${tone}`}>{GRADE_LABEL[grade] ?? grade}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
