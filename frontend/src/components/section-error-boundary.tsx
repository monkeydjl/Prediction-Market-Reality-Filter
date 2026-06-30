"use client";

import { Component, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

export class SectionErrorBoundary extends Component<
  { title: string; children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error(`${this.props.title} failed`, error);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <section className="flex flex-col items-center gap-3 rounded-lg border border-neg/40 bg-neg/10 px-6 py-8 text-center">
        <AlertTriangle className="size-6 text-neg" aria-hidden="true" />
        <div>
          <h2 className="text-sm font-semibold">{this.props.title}加载失败</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            该模块遇到未处理错误，其他模块仍可继续使用。
          </p>
        </div>
        <button
          type="button"
          onClick={() => this.setState({ error: null })}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-3 text-xs font-medium text-foreground"
        >
          <RotateCcw className="size-3.5" aria-hidden="true" />
          重试模块
        </button>
      </section>
    );
  }
}
