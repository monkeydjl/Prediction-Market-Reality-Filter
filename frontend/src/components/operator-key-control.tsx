"use client";

import { useEffect, useState } from "react";
import { KeyRound } from "lucide-react";
import { getOperatorApiKey, setOperatorApiKey } from "@/lib/api";

export function OperatorKeyControl() {
  const [value, setValue] = useState("");
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setValue(getOperatorApiKey()), 0);
    return () => window.clearTimeout(timer);
  }, []);

  function save() {
    setOperatorApiKey(value);
    setValue(getOperatorApiKey());
    setEditing(false);
  }

  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => setEditing(true)}
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
        autoFocus
      />
      <button
        type="submit"
        className="h-8 rounded-md border border-primary bg-primary/15 px-2 text-xs font-medium text-primary"
      >
        保存
      </button>
      <button
        type="button"
        onClick={() => {
          setValue(getOperatorApiKey());
          setEditing(false);
        }}
        className="h-8 rounded-md border border-border bg-secondary px-2 text-xs text-muted-foreground"
      >
        取消
      </button>
    </form>
  );
}
