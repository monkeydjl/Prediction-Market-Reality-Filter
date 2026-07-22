import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  applyOperatorAuthHeaders,
  buildOperatorAuthHeaders,
  clearOperatorCredentials,
  getOperatorApiKey,
  getOperatorCredentialsSnapshot,
  getOperatorId,
  hasOperatorApiKey,
  maskOperatorKey,
  OPERATOR_CREDENTIALS_EVENT,
  OPERATOR_KEY_OPEN_EVENT,
  requestOpenOperatorKey,
  setOperatorApiKey,
  setOperatorId,
} from "./operator-credentials";

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  window.sessionStorage.clear();
  vi.restoreAllMocks();
});

describe("operator-credentials", () => {
  it("stores trimmed key and id in sessionStorage only", () => {
    setOperatorApiKey("  secret-key  ");
    setOperatorId("  alice  ");
    expect(getOperatorApiKey()).toBe("secret-key");
    expect(getOperatorId()).toBe("alice");
    expect(window.sessionStorage.getItem("pmrf.operatorApiKey")).toBe("secret-key");
    expect(window.localStorage.getItem("pmrf.operatorApiKey")).toBeNull();
    expect(hasOperatorApiKey()).toBe(true);
  });

  it("clears credentials", () => {
    setOperatorApiKey("secret");
    setOperatorId("ops");
    clearOperatorCredentials();
    expect(getOperatorApiKey()).toBe("");
    expect(getOperatorId()).toBe("");
    expect(hasOperatorApiKey()).toBe(false);
  });

  it("masks keys for UI", () => {
    expect(maskOperatorKey("")).toBe("");
    expect(maskOperatorKey("ab")).toBe("••••");
    expect(maskOperatorKey("abcdefgh")).toBe("a•••h");
    expect(maskOperatorKey("supersecretkey")).toBe("su…ey");
  });

  it("snapshot never includes raw secret", () => {
    setOperatorApiKey("supersecretkey");
    setOperatorId("ops");
    const snap = getOperatorCredentialsSnapshot();
    expect(snap.hasKey).toBe(true);
    expect(snap.operatorId).toBe("ops");
    expect(snap.keyHint).not.toContain("supersecretkey");
  });

  it("builds and applies auth headers", () => {
    setOperatorApiKey("k");
    setOperatorId("ops");
    const headers = buildOperatorAuthHeaders();
    expect(headers["X-API-Key"]).toBe("k");
    expect(headers["X-Operator"]).toBe("ops");
    const h = new Headers();
    applyOperatorAuthHeaders(h);
    expect(h.get("X-API-Key")).toBe("k");
  });

  it("emits change event on set/clear", () => {
    const spy = vi.fn();
    window.addEventListener(OPERATOR_CREDENTIALS_EVENT, spy);
    setOperatorApiKey("x");
    setOperatorId("y");
    clearOperatorCredentials();
    window.removeEventListener(OPERATOR_CREDENTIALS_EVENT, spy);
    expect(spy).toHaveBeenCalled();
  });

  it("requestOpenOperatorKey dispatches open event", () => {
    const spy = vi.fn();
    window.addEventListener(OPERATOR_KEY_OPEN_EVENT, spy);
    requestOpenOperatorKey({ setHash: false });
    window.removeEventListener(OPERATOR_KEY_OPEN_EVENT, spy);
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
