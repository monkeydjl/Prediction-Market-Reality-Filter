export default function Loading() {
  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 md:px-6">
      <div className="h-4 w-20 animate-pulse rounded bg-muted" />
      <div className="h-10 w-64 animate-pulse rounded bg-muted" />
      <div className="h-10 w-24 animate-pulse rounded bg-muted" />
      <div className="space-y-4">
        <div className="h-32 animate-pulse rounded bg-muted" />
        <div className="h-48 animate-pulse rounded bg-muted" />
      </div>
    </main>
  );
}
