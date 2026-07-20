"use client";

import { useCallback, useEffect, useState } from "react";
import { KeyRound, ShieldOff } from "lucide-react";
import {
  clearOperatorCredentials,
  getOperatorApiKey,
  getOperatorCredentialsSnapshot,
  getOperatorId,
  setOperatorApiKey,
  setOperatorId,
  OPERATOR_CREDENTIALS_EVENT,
} from "@/lib/operator-credentials";

export function OperatorKeyControl() {
  const [value, setValue] = useState("");
  const [operatorId, setOperatorIdValue] = useState("");
  const [editing, setEditing] = useState(false);
  const [hasKey, setHasKey] = useState(false);
  const [keyHint, setKeyHint] = useState("");
  const [storedOperatorId, setStoredOperatorId] = useState("");

  const syncFromStorage = useCallback(() => {
    const snap = getOperatorCredentialsSnapshot();
    setHasKey(snap.hasKey);
    setKeyHint(snap.keyHint);
    setStoredOperatorId(snap.operatorId);
    if (!editing) {
      setValue(getOperatorApiKey());
      setOperatorIdValue(getOperatorId());
    }
  }, [editing]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      syncFromStorage();
    }, 0);
    const onChange = () => syncFromStorage();
    window.addEventListener(OPERATOR_CREDENTIALS_EVENT, onChange);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener(OPERATOR_CREDENTIALS_EVENT, onChange);
    };
  }, [syncFromStorage]);

  function save() {
    setOperatorApiKey(value);
    setOperatorId(operatorId);
    setEditing(false);
    syncFromStorage();
  }

  function clearAuth() {
    clearOperatorCredentials();
    setValue("");
    setOperatorIdValue("");
    setEditing(false);
    syncFromStorage();
  }

  if (!editing) {
    return (
      <div className="inline-flex items-center gap-1">
        <button
          type="button"
          onClick={() => setEditing(true)}
          aria-label={hasKey ? "编辑写接口 API key" : "配置写接口 API key"}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-muted-foreground transition-colors hover:text-foreground"
          title={
            hasKey
              ? `已授权${keyHint ? `（${keyHint}）` : ""}${storedOperatorId ? ` · ${storedOperatorId}` : ""} · 仅存于本标签页 sessionStorage`
              : "配置写接口 API key（sessionStorage，关闭标签后清除）"
          }
        >
          <KeyRound className="size-3.5" aria-hidden="true" />
          {hasKey ? "已授权" : "授权"}
        </button>
        {hasKey && (
          <button
            type="button"
            onClick={clearAuth}
            aria-label="清除写接口授权"
            className="inline-flex h-8 items-center gap-1 rounded-md border border-border bg-secondary px-2 text-xs text-muted-foreground transition-colors hover:text-neg"
            title="立即清除本标签页中的 API key 与 Operator ID"
          >
            <ShieldOff className="size-3.5" aria-hidden="true" />
            清除
          </button>
        )}
      </div>
    );
  }

  return (
    <form
      className="flex flex-wrap items-center gap-1.5"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <input
        type="password"
        name="operator-api-key"
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="h-8 w-32 rounded-md border border-border bg-secondary px-2 text-xs text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
        placeholder="API key"
        aria-label="写接口 API key"
        autoFocus
      />
      <input
        type="text"
        name="operator-id"
        autoComplete="off"
        spellCheck={false}
        value={operatorId}
        onChange={(e) => setOperatorIdValue(e.target.value)}
        className="h-8 w-28 rounded-md border border-border bg-secondary px-2 text-xs text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
        placeholder="Operator"
        aria-label="Operator"
      />
      <button
        type="submit"
        aria-label="保存写接口 API key"
        className="h-8 rounded-md border border-primary bg-primary/15 px-2 text-xs font-medium text-primary"
      >
        保存
      </button>
      <button
        type="button"
        onClick={() => {
          setValue(getOperatorApiKey());
          setOperatorIdValue(getOperatorId());
          setEditing(false);
        }}
        aria-label="取消编辑写接口 API key"
        className="h-8 rounded-md border border-border bg-secondary px-2 text-xs text-muted-foreground"
      >
        取消
      </button>
      {hasKey && (
        <button
          type="button"
          onClick={clearAuth}
          aria-label="清除写接口授权"
          className="h-8 rounded-md border border-border bg-secondary px-2 text-xs text-muted-foreground hover:text-neg"
        >
          清除
        </button>
      )}
      <span className="max-w-[12rem] text-[10px] leading-tight text-muted-foreground">
        仅 sessionStorage · 非 localStorage · 关闭标签即失效
      </span>
    </form>
  );
}
