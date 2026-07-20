import type { ContributionItem } from "@/lib/sports-api";
import { ScrollableTable } from "@/components/ui/scrollable-table";

const FACTOR_NAME_ZH: Record<string, string> = {
  elo: "Elo 等级分",
  home_court: "主场优势",
  rest: "休息天数",
  form: "近期状态",
  starting_pitcher: "先发投手",
  goalie: "门将",
  odds: "赔率",
  injury: "伤病影响",
  h2h: "历史交锋",
  situational: "情境调整",
  pace: "节奏",
  net_rating: "净效率 (ORtg−DRtg)",
  pitcher: "先发投手",
  park: "球场因素",
  bullpen: "牛棚",
  travel: "旅行/时区",
  weather: "天气",
  xg: "期望进球 (xG)",
  market_value: "球队身价",
  possession: "控球/射门",
  attack_share: "进攻份额",
  referee: "裁判倾向",
  platoon: "左右打对决",
  altitude: "海拔/高原",
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
    <ScrollableTable aria-label="因子贡献分解" testId="factor-breakdown-scroll">
      <table className="w-full min-w-[28rem] text-sm" data-testid="factor-breakdown-table">
        <caption className="sr-only">各因子方向、权重与可用性详情</caption>
        <thead>
          <tr className="border-b">
            <th scope="col" className="py-2 text-left">
              因子
            </th>
            <th scope="col" className="py-2 text-left">
              方向
            </th>
            <th scope="col" className="py-2 text-right">
              权重
            </th>
            <th scope="col" className="py-2 text-left">
              详情
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, idx) => {
            const factorName = FACTOR_NAME_ZH[item.factor] ?? item.factor;
            const direction = DIRECTION_ZH[item.direction] ?? item.direction;
            const outcome = item.predicted_outcome
              ? ` (${OUTCOME_ZH[item.predicted_outcome] ?? item.predicted_outcome})`
              : "";
            const weight = `${(item.weight * 100).toFixed(0)}%`;
            const detail = item.available ? (item.detail ?? "") : "不可用";
            return (
              <tr
                key={`${item.factor}-${idx}`}
                className={`border-b ${item.available ? "" : "opacity-40"}`}
              >
                <td className="py-2">{factorName}</td>
                <td className="py-2">
                  {direction}
                  {outcome}
                </td>
                <td className="py-2 text-right font-mono tabular-nums">{weight}</td>
                <td className="py-2 text-muted-foreground">{detail}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </ScrollableTable>
  );
}
