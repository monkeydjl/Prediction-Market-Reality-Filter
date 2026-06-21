import { AppNav } from "@/components/app-nav";

export default function Loading() {
  return (
    <div className="min-h-screen">
      <AppNav />
      <main className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-6 md:px-6 md:py-8">
        <div className="h-8 w-48 animate-pulse rounded-md bg-secondary" />
        <div className="grid gap-3 md:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-24 animate-pulse rounded-lg border border-border bg-card" />
          ))}
        </div>
        <div className="h-80 animate-pulse rounded-lg border border-border bg-card" />
      </main>
    </div>
  );
}
