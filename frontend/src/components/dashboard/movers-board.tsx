import Link from "next/link";
import type { EventView } from "@/lib/adapt";
import { categoryLabel, fmtPct } from "@/lib/format";
import { DeltaPill } from "@/components/indicators";
import { Sparkline } from "@/components/sparkline";

function MoverCard({ event, spark }: { event: EventView; spark: number[] }) {
  return (
    <Link
      href={`/events?id=${encodeURIComponent(event.id)}`}
      className="group flex flex-col gap-3 rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex items-center justify-between">
        <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
          {categoryLabel(event.category)}
        </span>
        <DeltaPill delta={event.delta} />
      </div>
      <p className="line-clamp-2 text-sm font-medium leading-snug text-pretty group-hover:text-primary">
        {event.title}
      </p>
      <div className="mt-auto flex items-end justify-between">
        <div>
          <div className="font-mono text-2xl font-semibold tabular-nums">
            {fmtPct(event.currentProbability)}
          </div>
          <div className="text-[11px] text-muted-foreground">当前发生概率</div>
        </div>
        <Sparkline data={spark} trend={event.trend} />
      </div>
    </Link>
  );
}

export function MoversBoard({
  movers,
  sparklines,
}: {
  movers: EventView[];
  sparklines: Record<string, number[]>;
}) {
  if (movers.length === 0) {
    return (
      <section className="flex flex-col gap-3">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-semibold">概率异动榜</h2>
          <span className="text-xs text-muted-foreground">概率变动最大的事件</span>
        </div>
        <div className="rounded-lg border border-dashed py-8 text-center">
          <p className="text-sm text-muted-foreground">
            暂无概率异动事件
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            事件概率发生变化后，变动最大的事件将显示在这里
          </p>
        </div>
      </section>
    );
  }
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">概率异动榜</h2>
        <span className="text-xs text-muted-foreground">概率变动最大的事件</span>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        {movers.slice(0, 3).map((e) => (
          <MoverCard key={e.id} event={e} spark={sparklines[e.id] ?? []} />
        ))}
      </div>
    </section>
  );
}
