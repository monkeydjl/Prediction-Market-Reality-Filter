export default function Loading() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-6 md:px-6">
      <div className="h-8 w-48 animate-pulse rounded bg-muted" />
      <div className="mt-6 h-10 w-full animate-pulse rounded bg-muted" />
      <div className="mt-4 h-64 w-full animate-pulse rounded bg-muted" />
    </div>
  );
}
