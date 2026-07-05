type PaginationControlsProps = {
  page: number;
  pageSize: number;
  total: number;
  loading?: boolean;
  onPageChange: (page: number) => void;
};

export function PaginationControls({
  page,
  pageSize,
  total,
  loading = false,
  onPageChange,
}: PaginationControlsProps) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const canGoPrev = page > 0;
  const canGoNext = page + 1 < pageCount;

  return (
    <div className="flex flex-wrap items-center justify-center gap-2 text-sm text-muted-foreground">
      <button
        type="button"
        disabled={loading || !canGoPrev}
        onClick={() => onPageChange(page - 1)}
        className="rounded-md border border-border bg-card px-3 py-1.5 transition-colors hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50"
      >
        上一页
      </button>
      <span className="font-mono tabular-nums">
        第 {page + 1} / {pageCount} 页 · 共 {total} 条
      </span>
      <button
        type="button"
        disabled={loading || !canGoNext}
        onClick={() => onPageChange(page + 1)}
        className="rounded-md border border-border bg-card px-3 py-1.5 transition-colors hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50"
      >
        下一页
      </button>
    </div>
  );
}
