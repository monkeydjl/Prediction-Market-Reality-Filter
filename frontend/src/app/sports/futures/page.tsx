"use client";
import { FuturesDashboard } from "@/components/sports/futures/FuturesDashboard";

export default function FuturesPage() {
  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 md:px-6">
      <h1 className="text-2xl font-bold">期货/冠军市场</h1>
      <FuturesDashboard />
    </main>
  );
}
