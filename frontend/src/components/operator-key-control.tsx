"use client";

import { useEffect, useState } from "react";
import { KeyRound } from "lucide-react";
import { getOperatorApiKey, getOperatorId, setOperatorApiKey, setOperatorId } from "@/lib/api";

export function OperatorKeyControl() {
  const [value, setValue] = useState("");
  const [operatorId, setOperatorIdValue] = useState("");
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setValue(getOperatorApiKey());
      setOperatorIdValue(getOperatorId());
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  function save() {
    setOperatorApiKey(value);
    setOperatorId(operatorId);
    setValue(getOperatorApiKey());
    setOperatorIdValue(getOperatorId());
    setEditing(false);
  }

  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => setEditing(true)}
        aria-label={value ? "编辑写接口 API key" : "配置写接口 API key"}
        className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-muted-foreground transition-colors hover:text-foreground"
        title="配置写接口 API key"
      >
        <KeyRound className="size-3.5" aria-hidden="true" />
        {value ? "已授权" : "授权"}
      </button>
    );
  }

  return (
    <form
      className="flex items-center gap-1.5"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <input
        type="password"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="h-8 w-32 rounded-md border border-border bg-secondary px-2 text-xs text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
        placeholder="API key"
        aria-label="写接口 API key"
        autoFocus
      />
      <input
        type="text"
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
    </form>
  );
}
