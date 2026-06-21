import { AppNav } from "@/components/app-nav";

export function RouteLoading({
  cards = 3,
  rows = 4,
}: {
  cards?: number;
  rows?: number;
}) {
  return (
    <div className="min-h-screen">
      <AppNav />
      <main className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 md:py-8">
        <div className="flex flex-col gap-2">
          <div className="h-7 w-52 animate-pulse rounded-md bg-secondary" />
          <div className="h-4 w-full max-w-xl animate-pulse rounded bg-secondary/70" />
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          {Array.from({ length: cards }).map((_, i) => (
            <div key={i} className="h-24 animate-pulse rounded-lg border border-border bg-card" />
          ))}
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex flex-col gap-3">
            {Array.from({ length: rows }).map((_, i) => (
              <div key={i} className="h-12 animate-pulse rounded-md bg-secondary/70" />
            ))}
          </div>
        </div>
      </main>
    </div>
  );
}
