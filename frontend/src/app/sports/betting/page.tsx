import Link from "next/link";
import {
  BETTING_TOOL_LINKS,
  SECTION_LABELS,
  competitionsBySection,
  statusLabel,
  type BettingCompetition,
  type CompetitionSection,
} from "@/lib/betting/competition-catalog";

const SECTION_ORDER: CompetitionSection[] = [
  "football",
  "americas",
  "esports",
  "tools",
];

function StatusBadge({ status }: { status: BettingCompetition["status"] }) {
  const tone =
    status === "live"
      ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
      : status === "kernel"
        ? "bg-sky-500/15 text-sky-700 dark:text-sky-400"
        : "bg-muted text-muted-foreground";
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium ${tone}`}
    >
      {statusLabel(status)}
    </span>
  );
}

function CompetitionCard({ item }: { item: BettingCompetition }) {
  const href =
    item.status === "coming_soon"
      ? `/sports/betting/${item.id}`
      : item.track === "kernel"
        ? `/sports/betting/${item.id}`
        : item.href;

  return (
    <Link
      href={href}
      className="group rounded-lg border border-border p-4 transition-colors hover:bg-secondary/40"
      data-testid={`betting-comp-${item.id}`}
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-semibold group-hover:text-primary">{item.label}</h3>
        <StatusBadge status={item.status} />
      </div>
      <p className="mt-1 text-sm text-muted-foreground">{item.description}</p>
    </Link>
  );
}

export default function BettingHubPage() {
  return (
    <main className="mx-auto max-w-4xl space-y-8 px-4 py-6 md:px-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold">竞猜中心</h1>
        <p className="text-sm text-muted-foreground">
          统一入口覆盖世界杯专题、五大联赛 / NBA 等 Kernel 赛程，以及 Edge
          与推荐工具。电竞等赛道先占位，不展示假盘口。
        </p>
      </div>

      <div
        role="note"
        className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs leading-relaxed text-muted-foreground"
      >
        <p>
          <span className="font-medium text-foreground">双轨说明</span>
          ：世界杯走{" "}
          <code className="rounded bg-muted px-1">/api/world-cup/*</code>
          ；NBA / MLB / NHL / 足球联赛走 Kernel{" "}
          <code className="rounded bg-muted px-1">/api/sports/*</code>
          。二者数据与结算路径不同，卡片上会标明状态。
        </p>
      </div>

      {SECTION_ORDER.map((section) => {
        if (section === "tools") {
          return (
            <section key={section} className="space-y-3" aria-labelledby={`sec-${section}`}>
              <h2 id={`sec-${section}`} className="text-lg font-semibold">
                {SECTION_LABELS[section]}
              </h2>
              <div className="grid gap-3 sm:grid-cols-2">
                {BETTING_TOOL_LINKS.map((tool) => (
                  <Link
                    key={tool.id}
                    href={tool.href}
                    className="group rounded-lg border border-border p-4 transition-colors hover:bg-secondary/40"
                    data-testid={`betting-tool-${tool.id}`}
                  >
                    <h3 className="font-semibold group-hover:text-primary">
                      {tool.title}
                    </h3>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {tool.description}
                    </p>
                  </Link>
                ))}
              </div>
            </section>
          );
        }

        const items = competitionsBySection(section);
        if (items.length === 0) return null;

        return (
          <section key={section} className="space-y-3" aria-labelledby={`sec-${section}`}>
            <h2 id={`sec-${section}`} className="text-lg font-semibold">
              {SECTION_LABELS[section]}
            </h2>
            <div className="grid gap-3 sm:grid-cols-2">
              {items.map((item) => (
                <CompetitionCard key={item.id} item={item} />
              ))}
            </div>
          </section>
        );
      })}
    </main>
  );
}
