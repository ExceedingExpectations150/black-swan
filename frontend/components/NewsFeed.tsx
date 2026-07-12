"use client";

// The news feed reads as a daily newsletter: posts are grouped by simulated
// day, and each day leads with the Market Wire end-of-day report (@DailyBrief)
// as its headline article, followed by that day's dispatches. All content is
// real — the daily report and dispatches come from the backend's newsroom.

import { useMemo } from "react";
import { Newspaper, X } from "lucide-react";
import { useStore } from "@/lib/store";
import { sentimentColor, monogram } from "@/lib/format";
import type { SocialPostT } from "@/lib/types";

const DAY_SECONDS = 86400;
const DAILY_BRIEF_HANDLE = "@DailyBrief";

interface DayGroup {
  key: string;
  dayEpoch: number | null;
  report: SocialPostT | null;
  items: SocialPostT[];
}

function postEpoch(post: SocialPostT): number | null {
  if (!post.ts) return null;
  const ms = Date.parse(post.ts);
  return Number.isNaN(ms) ? null : ms / 1000;
}

function groupByDay(posts: SocialPostT[]): DayGroup[] {
  const groups = new Map<string, DayGroup>();
  for (const post of posts) {
    const epoch = postEpoch(post);
    const dayEpoch = epoch === null ? null : Math.floor(epoch / DAY_SECONDS) * DAY_SECONDS;
    const key = dayEpoch === null ? "unknown" : String(dayEpoch);
    let group = groups.get(key);
    if (!group) {
      group = { key, dayEpoch, report: null, items: [] };
      groups.set(key, group);
    }
    if (post.handle === DAILY_BRIEF_HANDLE && !group.report) {
      group.report = post;
    } else {
      group.items.push(post);
    }
  }
  // Newest day first; the "unknown" bucket (no timestamp) sinks to the bottom.
  return [...groups.values()].sort((a, b) => {
    if (a.dayEpoch === null) return 1;
    if (b.dayEpoch === null) return -1;
    return b.dayEpoch - a.dayEpoch;
  });
}

function dayLabel(dayEpoch: number | null, ticksPerDay: number | null, tickId: number): string {
  if (dayEpoch === null) return "Earlier";
  const date = new Date(dayEpoch * 1000).toISOString().slice(0, 10);
  const dayNum = ticksPerDay ? Math.max(1, Math.ceil(tickId / ticksPerDay)) : null;
  return dayNum ? `Day ${dayNum} · ${date}` : date;
}

export default function NewsFeed() {
  const social = useStore((s) => s.social);
  const ticksPerDay = useStore((s) => s.ticksPerDay);
  const selectedTicker = useStore((s) => s.selectedTicker);
  const setSelectedTicker = useStore((s) => s.setSelectedTicker);

  const posts = selectedTicker
    ? social.filter((p) => p.author_ticker === selectedTicker)
    : social;

  const days = useMemo(() => groupByDay(posts), [posts]);

  return (
    <div className="panel flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-hair px-3 py-2.5">
        <div className="flex items-center gap-2">
          <Newspaper size={14} className="text-ink2" />
          <span className="section-title">Market Newsletter</span>
          <span className="flex items-center gap-1.5 text-[10px] text-ink2">
            <span className="live-dot inline-block h-1.5 w-1.5 rounded-full bg-up" />
            Live
          </span>
        </div>
        {selectedTicker && (
          <button
            onClick={() => setSelectedTicker(null)}
            className="flex items-center gap-1 rounded bg-white/[0.06] px-2 py-1 text-[10px] uppercase tracking-[0.12em] text-ink2 transition hover:text-ink"
          >
            filtered: {selectedTicker}
            <X size={11} />
          </button>
        )}
      </div>

      <div className="scroll-thin flex-1 overflow-y-auto">
        {posts.length === 0 ? (
          <div className="flex h-full items-center justify-center py-10 text-[10px] uppercase tracking-[0.16em] text-ink3">
            AWAITING THE DAY&rsquo;S REPORTING
          </div>
        ) : (
          <div className="flex flex-col">
            {days.map((group) => {
              const anchorTick = group.report?.tick_id ?? group.items[0]?.tick_id ?? 0;
              return (
                <section key={group.key}>
                  <div className="sticky top-0 z-10 flex items-center justify-between border-b border-hair bg-black/90 px-3 py-1.5 backdrop-blur">
                    <span className="tnum text-[10px] font-semibold uppercase tracking-[0.16em] text-ink2">
                      {dayLabel(group.dayEpoch, ticksPerDay, anchorTick)}
                    </span>
                    <span className="tnum font-mono text-[9px] uppercase tracking-[0.14em] text-ink3">
                      {group.items.length + (group.report ? 1 : 0)} items
                    </span>
                  </div>

                  {group.report && <DailyReport post={group.report} />}

                  <ul className="px-2 py-1.5">
                    {group.items.map((post) => (
                      <DispatchRow key={post.post_id} post={post} />
                    ))}
                  </ul>
                </section>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function DailyReport({ post }: { post: SocialPostT }) {
  const up = post.sentiment >= 0;
  return (
    <article className="border-b border-hair bg-white/[0.02] px-3 py-3">
      <div className="mb-1.5 flex items-center gap-2">
        <span
          className="rounded-sm px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.14em]"
          style={{
            color: up ? "var(--up)" : "var(--down)",
            background: up ? "rgba(22,198,12,0.12)" : "rgba(255,77,79,0.12)",
          }}
        >
          Daily Report
        </span>
        <span className="text-[11px] font-semibold text-ink">Market Wire</span>
        <span className="font-mono text-[10px] text-ink3">{post.handle}</span>
      </div>
      <p className="text-[13px] leading-relaxed text-ink">{post.content}</p>
    </article>
  );
}

function DispatchRow({ post }: { post: SocialPostT }) {
  const isCompany = post.author_type === "company";
  return (
    <li className="flex gap-2.5 rounded-sm px-1 py-2 transition hover:bg-white/[0.02]">
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/[0.05] text-ink2 ring-1 ring-inset ring-hair">
        {isCompany ? (
          <span className="text-[9px] font-semibold">{monogram(post.author_display)}</span>
        ) : (
          <Newspaper size={12} />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-1.5">
          <span className="text-[11px] font-semibold text-ink">{post.author_display}</span>
          <span className="font-mono text-[10px] text-ink3">{post.handle}</span>
          <span
            className="ml-auto inline-block h-1.5 w-1.5 self-center rounded-full"
            title={`Sentiment ${post.sentiment.toFixed(2)}`}
            style={{ backgroundColor: sentimentColor(post.sentiment) }}
          />
        </div>
        <p className="mt-0.5 text-xs leading-relaxed text-ink2">{post.content}</p>
      </div>
    </li>
  );
}
