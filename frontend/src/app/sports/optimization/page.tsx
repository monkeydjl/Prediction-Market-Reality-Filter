"use client";
import { OptimizationDashboard } from "@/components/sports/optimization/OptimizationDashboard";

export default function OptimizationPage() {
  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 md:px-6">
      <h1 className="text-2xl font-bold">参数优化</h1>
      <OptimizationDashboard />
    </main>
  );
}
