"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import { PredictionTrajectory } from "@/components/sports/learning/prediction-trajectory";

export default function MatchTrajectoryPage() {
  const params = useParams();
  const matchId = params.matchId as string;

  return (
    <main className="mx-auto max-w-7xl px-4 py-6 md:px-6">
      <PredictionTrajectory matchId={matchId} />
    </main>
  );
}
