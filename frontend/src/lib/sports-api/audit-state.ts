/**
 * The price-path audit's three zeroes (audit 53.180).
 *
 * `GET /sport-markets/links/{id}/audit` and `/matches/{id}/audit` answer with
 * `available: false` for two different facts, and both doors used to print the
 * same sentence for both:
 *
 *   the read ran, there is nothing to draw   -> 「尚无可用价格序列」
 *   the read failed, nothing is known        -> 「尚无可用价格序列」  ← a lie
 *
 * The backend now names the third one: `MarketSnapshotStore.audit_summary`
 * catches its own read failure, logs it, and carries `read_failed: true` plus
 * `flags: ["read_failed"]` on every branch (it could have answered 500 instead,
 * but this body already speaks in switches). `app/utils/failure_policy.READ_FAILED`
 * is the same word from the other side.
 *
 * This module is the one place both doors read that switch, so the inline trail
 * and the panel cannot drift apart — the same reason
 * `market_snapshot_store.is_timestamped` exists on the backend rather than being
 * repeated in two readers.
 *
 * Absent is *not* false. An older backend, or a body cached from one, does not
 * send the key at all; reading that absence as "the read succeeded" would
 * restore the lie silently, so it gets its own state and its own sentence.
 */

/** The flag / reason word the backend uses for the third zero. */
export const READ_FAILED_FLAG = "read_failed";

/** Which of the three zeroes a per-link audit entry is reporting. */
export type AuditZero = "read_failed" | "absent" | "unknown";

/**
 * Classify an audit entry's zero.
 *
 * `read_failed === true` wins even when `available` says otherwise: the flag is
 * a statement about the *query*, `available` a statement about the *result*,
 * and only the query can make the result unknowable.
 */
export function auditZero(entry: { read_failed?: boolean }): AuditZero {
  if (entry.read_failed === true) return "read_failed";
  if (entry.read_failed === false) return "absent";
  return "unknown";
}

/** One sentence per zero, shared by both doors so the wording cannot drift. */
export const AUDIT_ZERO_TEXT: Record<AuditZero, string> = {
  read_failed: "无法读取价格序列——这不是「尚无快照」",
  absent: "尚无可用价格序列",
  unknown: "价格序列状态未知",
};

/**
 * The flags worth a chip.
 *
 * `read_failed` is rendered as a state (a sentence plus its own test id), not
 * as a pill next to 「样本稀疏」/「高回撤」: those describe the data, this one
 * says there is no data to describe. Shipping it twice would let the two
 * renderings disagree — and the pill would show the raw English key, since the
 * panel does not translate flags.
 */
export function auditFlagChips(flags: readonly string[] | undefined): string[] {
  return (flags ?? []).filter((flag) => flag !== READ_FAILED_FLAG);
}

/**
 * Chinese labels for the flags `MarketSnapshotStore.audit_summary` emits.
 *
 * Kept here rather than in one component because both doors render flags and
 * they used to disagree: the trail translated six of them and the panel printed
 * every one raw. An unknown flag keeps its raw value — an invented label is
 * worse than an unfamiliar one (same rule as `calibration-buckets.ts`).
 */
export const AUDIT_FLAG_LABELS: Record<string, string> = {
  no_snapshots: "无快照",
  no_prices: "无价格",
  untimestamped_snapshots: "无时间戳",
  sparse: "样本稀疏",
  large_move: "大幅移动",
  high_drawdown: "高回撤",
};

/** Translate one flag, falling back to the key the backend actually sent. */
export function auditFlagLabel(flag: string): string {
  return AUDIT_FLAG_LABELS[flag] ?? flag;
}
