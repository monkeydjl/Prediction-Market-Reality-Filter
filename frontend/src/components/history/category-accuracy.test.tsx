import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// next/dynamic 在测试中替换为同步占位组件，避免异步加载与 SSR 分支
vi.mock("next/dynamic", () => ({
  default: () => (props: { data?: unknown[] }) => (
    <div data-testid="category-chart" data-count={props.data?.length ?? 0} />
  ),
}));

import { CategoryAccuracy, toCategoryData, type CategoryDatum } from "./category-accuracy";

const data: CategoryDatum[] = [
  { category: "加密资产", brier: 0.12, skill: 0.4, count: 30, minSamples: 20, qualified: true },
  { category: "政治", brier: 0.25, skill: 0.1, count: 8, minSamples: 20, qualified: false },
];

describe("toCategoryData", () => {
  it("过滤无 skill_score 或样本数为 0 的条目，并按 skill 降序排列", () => {
    const byCat = {
      crypto: { brier_score: 0.12, skill_score: 0.4, grade: "GOOD", n: 30, segment_min_samples: 20, qualified: true },
      politics: { brier_score: 0.25, skill_score: 0.1, grade: "FAIR", n: 8, segment_min_samples: 20, qualified: false },
      no_skill: { brier_score: null, skill_score: null, grade: "no_data", n: 5 },
      zero_n: { brier_score: 0.3, skill_score: 0.2, grade: "GOOD", n: 0 },
    };
    const result = toCategoryData(byCat);
    expect(result).toHaveLength(2);
    // skill 降序：crypto(0.4) 在 politics(0.1) 之前
    expect(result[0].category).toBe("加密资产");
    expect(result[1].category).toBe("政治");
    expect(result[0]).toMatchObject({ skill: 0.4, count: 30, minSamples: 20, qualified: true });
  });

  it("未显式提供 qualified 时按 segment_min_samples 推断", () => {
    const byCat = {
      crypto: { brier_score: 0.12, skill_score: 0.4, grade: "GOOD", n: 10, segment_min_samples: 20 },
      macro: { brier_score: 0.2, skill_score: 0.3, grade: "GOOD", n: 30, segment_min_samples: 20 },
      // "prediction" 属于通用类目，会被 categoryLabel 映射为 "综合"
      prediction: { brier_score: 0.2, skill_score: 0.1, grade: "GOOD", n: 5 },
    };
    const result = toCategoryData(byCat);
    expect(result).toHaveLength(3);
    const byLabel = Object.fromEntries(result.map((d) => [d.category, d]));
    expect(byLabel["加密资产"].qualified).toBe(false); // 10 < 20
    expect(byLabel["宏观"].qualified).toBe(true); // 30 >= 20
    expect(byLabel["综合"].qualified).toBe(null); // 无 segment_min_samples
  });
});

describe("CategoryAccuracy", () => {
  it("无数据时显示空状态文案且不渲染图表", () => {
    render(<CategoryAccuracy data={[]} />);

    expect(screen.getByText(/暂无已结算事件/)).toBeInTheDocument();
    expect(screen.queryByTestId("category-chart")).not.toBeInTheDocument();
  });

  it("有数据时渲染标题、图表与各分类样本计数/合格状态", () => {
    render(<CategoryAccuracy data={data} />);

    expect(screen.getByText("各领域 skill 得分")).toBeInTheDocument();
    const chart = screen.getByTestId("category-chart");
    expect(chart.getAttribute("data-count")).toBe("2");

    // 进度列表中显示分类标签与计数
    expect(screen.getByText("加密资产")).toBeInTheDocument();
    expect(screen.getByText("政治")).toBeInTheDocument();
    expect(screen.getByText(/30\/20.*已合格/)).toBeInTheDocument();
    expect(screen.getByText(/8\/20.*未合格/)).toBeInTheDocument();
  });
});
