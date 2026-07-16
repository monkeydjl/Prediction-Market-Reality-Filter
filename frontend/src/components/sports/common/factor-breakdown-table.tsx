import type { ContributionItem } from "@/lib/sports-api";

const FACTOR_NAME_ZH: Record<string, string> = {
  elo: "Elo 等级分",
  home_court: "主场优势",
  rest: "休息天数",
  form: "近期状态",
  starting_pitcher: "先发投手",
  goalie: "门将",
  odds: "赔率",
};

const DIRECTION_ZH: Record<string, string> = {
  support: "支持",
  oppose: "反对",
  neutral: "中立",
};

const OUTCOME_ZH: Record<string, string> = {
  home_win: "主胜",
  away_win: "客胜",
  draw: "平局",
};

interface FactorBreakdownTableProps {
  items: ContributionItem[];
}

export function FactorBreakdownTable({ items }: FactorBreakdownTableProps) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b">
          <th className="py-2 text-left">因子</th>
          <th className="py-2 text-left">方向</th>
          <th className="py-2 text-right">权重</th>
          <th className="py-2 text-left">详情</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item, idx) => {
          const factorName = FACTOR_NAME_ZH[item.factor] ?? item.factor;
          const direction = DIRECTION_ZH[item.direction] ?? item.direction;
          const outcome = item.predicted_outcome ? ` (${OUTCOME_ZH[item.predicted_outcome] ?? item.predicted_outcome})` : "";
          const weight = `${(item.weight * 100).toFixed(0)}%`;
          const detail = item.available ? (item.detail ?? "") : "不可用";
          return (
            <tr
              key={idx}
              className={`border-b ${item.available ? "" : "opacity-40"}`}
            >
              <td className="py-2">{factorName}</td>
              <td className="py-2">{direction}{outcome}</td>
              <td className="py-2 text-right font-mono">{weight}</td>
              <td className="py-2 text-muted-foreground">{detail}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
