import { notFound } from "next/navigation";
import { CompetitionLanding } from "@/components/sports/betting/competition-landing";
import { getCompetitionById } from "@/lib/betting/competition-catalog";

type PageProps = {
  params: Promise<{ competitionId: string }>;
};

export default async function BettingCompetitionPage({ params }: PageProps) {
  const { competitionId } = await params;
  const competition = getCompetitionById(competitionId);
  if (!competition) {
    notFound();
  }

  return <CompetitionLanding competition={competition} />;
}
