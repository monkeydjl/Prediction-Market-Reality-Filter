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

  it("clears both credentials", () => {
    setOperatorApiKey("secret");
    setOperatorId("bob");
    clearOperatorCredentials();
    expect(getOperatorApiKey()).toBe("");
    expect(getOperatorId()).toBe("");
    expect(hasOperatorApiKey()).toBe(false);
  });

  it("masks key for UI", () => {
    expect(maskOperatorKey("")).toBe("");
    expect(maskOperatorKey("ab")).toBe("••••");
    expect(maskOperatorKey("abcdefgh")).toBe("a•••h");
    expect(maskOperatorKey("supersecretkey")).toBe("su…ey");
  });

  it("snapshot never includes raw key", () => {
    setOperatorApiKey("supersecretkey");
    setOperatorId("ops");
    const snap = getOperatorCredentialsSnapshot();
    expect(snap.hasKey).toBe(true);
    expect(snap.operatorId).toBe("ops");
    expect(snap.keyHint).not.toContain("supersecret");
    expect(JSON.stringify(snap)).not.toContain("supersecretkey");
  });

  it("buildOperatorAuthHeaders omits empties", () => {
    expect(buildOperatorAuthHeaders()).toEqual({});
    setOperatorApiKey("k");
    expect(buildOperatorAuthHeaders()).toEqual({ "X-API-Key": "k" });
    setOperatorId("op");
    expect(buildOperatorAuthHeaders()).toEqual({
      "X-API-Key": "k",
      "X-Operator": "op",
    });
  });

  it("applyOperatorAuthHeaders does not overwrite existing", () => {
    setOperatorApiKey("stored");
    setOperatorId("stored-op");
    const headers = new Headers({ "X-API-Key": "override" });
    applyOperatorAuthHeaders(headers);
    expect(headers.get("X-API-Key")).toBe("override");
    expect(headers.get("X-Operator")).toBe("stored-op");
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
});
