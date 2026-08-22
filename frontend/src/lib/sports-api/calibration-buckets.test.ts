import { describe, expect, it } from "vitest";
import {
  parseCalibrationKey,
  matchesCompetition,
  CONFIDENCE_BUCKET_PREFIX,
  STAGE_BUCKET_PREFIX,
} from "./calibration-buckets";

describe("parseCalibrationKey", () => {
  it("前缀与后端 learning_service 的常量一致", () => {
    // 这两个字面量若与后端漂移，分桶列会把所有行都显示成「基准」。
    expect(CONFIDENCE_BUCKET_PREFIX).toBe("#c_");
    expect(STAGE_BUCKET_PREFIX).toBe("#s_");
  });

  it("普通赛事键是基准行", () => {
    expect(parseCalibrationKey("epl")).toEqual({
      base: "epl",
      kind: "base",
      bucket: null,
      label: "基准",
    });
  });

  it("置信度分桶键解析出 base 与中文标签", () => {
    expect(parseCalibrationKey("epl#c_high")).toEqual({
      base: "epl",
      kind: "confidence",
      bucket: "high",
      label: "置信度·高",
    });
    expect(parseCalibrationKey("nba#c_low").label).toBe("置信度·低");
    expect(parseCalibrationKey("nba#c_mid").label).toBe("置信度·中");
  });

  it("阶段分桶键解析出 base 与中文标签", () => {
    expect(parseCalibrationKey("wc#s_knockout")).toEqual({
      base: "wc",
      kind: "stage",
      bucket: "knockout",
      label: "阶段·淘汰赛",
    });
    expect(parseCalibrationKey("wc#s_regular").label).toBe("阶段·常规赛");
    expect(parseCalibrationKey("wc#s_unknown").label).toBe("阶段·未知");
  });

  it("未知分桶 token 保留原值，不臆造标签", () => {
    const parsed = parseCalibrationKey("epl#c_weird");
    expect(parsed.bucket).toBe("weird");
    expect(parsed.label).toBe("置信度·weird");
  });

  it("空后缀不算分桶：整串当作基准行，不谎报 base", () => {
    expect(parseCalibrationKey("epl#c_")).toEqual({
      base: "epl#c_",
      kind: "base",
      bucket: null,
      label: "基准",
    });
  });
});

describe("matchesCompetition", () => {
  it("全部（空串）匹配任何行", () => {
    expect(matchesCompetition("epl#c_high", "")).toBe(true);
    expect(matchesCompetition("nba", "")).toBe(true);
  });

  it("选中赛事时同时命中它的分桶行——这正是服务端等值过滤漏掉的部分", () => {
    expect(matchesCompetition("epl", "epl")).toBe(true);
    expect(matchesCompetition("epl#c_high", "epl")).toBe(true);
    expect(matchesCompetition("epl#s_regular", "epl")).toBe(true);
  });

  it("不跨赛事误收：前缀相同的别的赛事不算命中", () => {
    expect(matchesCompetition("nba#c_high", "epl")).toBe(false);
    // `epl2` 的 base 是 `epl2`，不是 `epl`——不能用 startsWith 实现。
    expect(matchesCompetition("epl2#c_high", "epl")).toBe(false);
    expect(matchesCompetition("epl2", "epl")).toBe(false);
  });
});
