export default function Loading() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-6 md:px-6">
      <div className="h-4 w-24 animate-pulse rounded bg-muted" />
      <div className="mt-4 h-8 w-64 animate-pulse rounded bg-muted" />
      <div className="mt-6 h-64 w-full animate-pulse rounded bg-muted" />
    </div>
  );
}
