/**
 * Conditional-calibration keys (P1-V5).
 *
 * `KernelLearningService.update_calibration_by_confidence` /
 * `update_calibration_by_stage` reuse the existing `KernelCalibration`
 * unique constraint instead of adding a bucket column, so a bucket row is
 * stored with a *composite* competition key:
 *
 *   `epl#c_high`      confidence bucket (learning_service._CONF_BUCKET_PREFIX)
 *   `epl#s_knockout`  stage bucket      (learning_service._STAGE_PREFIX)
 *
 * The calibration table used to print those strings verbatim, and the 赛事
 * filter compares `competition` for equality server-side — so picking `epl`
 * hid every `epl#c_*` row while 全部 showed them as unexplained keys. Parsing
 * the key here is what lets both the filter and the 分桶 column agree with
 * what the backend actually wrote.
 */

export const CONFIDENCE_BUCKET_PREFIX = "#c_";
export const STAGE_BUCKET_PREFIX = "#s_";

export type CalibrationBucketKind = "base" | "confidence" | "stage";

export interface ParsedCalibrationKey {
  /** Competition code with the bucket suffix removed. */
  base: string;
  kind: CalibrationBucketKind;
  /** Raw bucket token as written by the backend, or null for a base row. */
  bucket: string | null;
  /** Chinese label for the 分桶 column. */
  label: string;
}

const CONFIDENCE_LABELS: Record<string, string> = {
  low: "低",
  mid: "中",
  high: "高",
};

const STAGE_LABELS: Record<string, string> = {
  regular: "常规赛",
  knockout: "淘汰赛",
  unknown: "未知",
};

function parseWithPrefix(
  competition: string,
  prefix: string,
  kind: "confidence" | "stage",
  labels: Record<string, string>,
  prefixLabel: string,
): ParsedCalibrationKey | null {
  const at = competition.indexOf(prefix);
  if (at < 0) return null;
  const bucket = competition.slice(at + prefix.length);
  // An empty suffix is not a bucket. Report the raw key as a base row rather
  // than claim a bucket the backend never wrote.
  if (!bucket) return null;
  return {
    base: competition.slice(0, at),
    kind,
    bucket,
    // Unknown tokens keep their raw value: an invented label would be worse
    // than an unfamiliar one.
    label: `${prefixLabel}·${labels[bucket] ?? bucket}`,
  };
}

/** Split a calibration row's competition key into its base and bucket. */
export function parseCalibrationKey(competition: string): ParsedCalibrationKey {
  return (
    parseWithPrefix(
      competition,
      CONFIDENCE_BUCKET_PREFIX,
      "confidence",
      CONFIDENCE_LABELS,
      "置信度",
    ) ??
    parseWithPrefix(
      competition,
      STAGE_BUCKET_PREFIX,
      "stage",
      STAGE_LABELS,
      "阶段",
    ) ?? { base: competition, kind: "base", bucket: null, label: "基准" }
  );
}

/**
 * Does this calibration row belong to the selected competition?
 *
 * `""` means 全部. A base row matches by equality; a bucket row matches on its
 * base, which is the whole point — `epl#c_high` is an `epl` row.
 */
export function matchesCompetition(competition: string, selected: string): boolean {
  if (!selected) return true;
  return parseCalibrationKey(competition).base === selected;
}
