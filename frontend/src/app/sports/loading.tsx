export default function Loading() {
  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 md:px-6">
      <div className="h-8 w-32 animate-pulse rounded bg-muted" />
      <div className="flex gap-1">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-8 w-20 animate-pulse rounded bg-muted" />
        ))}
      </div>
      <div className="space-y-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-20 animate-pulse rounded-lg bg-muted" />
        ))}
      </div>
    </main>
  );
}
