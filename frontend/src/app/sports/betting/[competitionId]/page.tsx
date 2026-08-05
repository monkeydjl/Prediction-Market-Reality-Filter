import { notFound } from "next/navigation";
import { CompetitionLanding } from "@/components/sports/betting/competition-landing";
import {
  BETTING_COMPETITIONS,
  getCompetitionById,
} from "@/lib/betting/competition-catalog";

type PageProps = {
  params: Promise<{ competitionId: string }>;
};

/**
 * The catalog is a fixed, enumerable list, so `output: "export"` can emit one
 * HTML file per competition at build time. Any id outside the catalog is a
 * build-time 404 rather than a runtime lookup.
 */
export function generateStaticParams(): { competitionId: string }[] {
  return BETTING_COMPETITIONS.map((c) => ({ competitionId: c.id }));
}

export const dynamicParams = false;

export default async function BettingCompetitionPage({ params }: PageProps) {
  const { competitionId } = await params;
  const competition = getCompetitionById(competitionId);
  if (!competition) {
    notFound();
  }

  return <CompetitionLanding competition={competition} />;
}
