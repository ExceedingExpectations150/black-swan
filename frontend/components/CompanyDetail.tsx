"use client";

// Right-side company drawer for the Black Swan terminal. Backfills description +
// recent posts + price history on open, then stays live from the store. Renders
// nothing until a ticker is selected.

import { useEffect, useMemo, useState } from "react";
import { X, TrendingUp, Heart, Repeat2, AlertTriangle } from "lucide-react";
import { useStore } from "@/lib/store";
import { API_BASE } from "@/lib/socket";
import {
  fmtPrice,
  fmtPct,
  fmtCap,
  changeClass,
  sentimentColor,
  monogram,
  timeAgo,
} from "@/lib/format";
import type { Company, SocialPostT, PricePoint } from "@/lib/types";
import LiveStockChart from "@/components/LiveStockChart";

type CompanyDetailResponse = Company & {
  recent_posts: SocialPostT[];
  price_series: PricePoint[];
};

export default function CompanyDetail() {
  const selectedTicker = useStore((s) => s.selectedTicker);
  const setSelectedTicker = useStore((s) => s.setSelectedTicker);
  const company = useStore((s) =>
    selectedTicker ? s.companies[selectedTicker] : undefined,
  );
  const social = useStore((s) => s.social);

  const [fetchedPosts, setFetchedPosts] = useState<SocialPostT[]>([]);
  const [mounted, setMounted] = useState(false);

  // Slide-in on mount.
  useEffect(() => {
    if (selectedTicker) {
      const id = requestAnimationFrame(() => setMounted(true));
      return () => cancelAnimationFrame(id);
    }
    setMounted(false);
  }, [selectedTicker]);

  // Backfill description enrichment + posts + price history on open.
  useEffect(() => {
    if (!selectedTicker) return;
    setFetchedPosts([]);
    const controller = new AbortController();
    void (async () => {
      try {
        const res = await fetch(
          `${API_BASE}/api/companies/${encodeURIComponent(selectedTicker)}`,
          { signal: controller.signal },
        );
        if (!res.ok) return;
        const data = (await res.json()) as CompanyDetailResponse;
        setFetchedPosts(data.recent_posts ?? []);
        if (data.price_series?.length) {
          useStore.getState().mergePriceSeries(selectedTicker, data.price_series);
        }
      } catch {
        // backend not up / aborted — live store still drives the drawer
      }
    })();
    return () => controller.abort();
  }, [selectedTicker]);

  // Merge backfilled posts with live posts for this ticker, dedupe, newest first.
  const posts = useMemo(() => {
    if (!selectedTicker) return [];
    const byId = new Map<string, SocialPostT>();
    for (const p of fetchedPosts) byId.set(p.post_id, p);
    for (const p of social) {
      if (p.author_ticker === selectedTicker) byId.set(p.post_id, p);
    }
    return [...byId.values()].sort((a, b) => b.tick_id - a.tick_id);
  }, [fetchedPosts, social, selectedTicker]);

  if (!selectedTicker) return null;

  const close = () => setSelectedTicker(null);

  return (
    <div className="fixed inset-0 z-50">
      {/* backdrop */}
      <div
        onClick={close}
        className="absolute inset-0 bg-black/60 transition-opacity duration-300"
        style={{ opacity: mounted ? 1 : 0 }}
      />
      {/* drawer */}
      <aside
        className="panel scroll-thin absolute right-0 top-0 h-screen w-[440px] max-w-[92vw] overflow-y-auto rounded-none border-l border-hair bg-black/85 backdrop-blur-md transition-transform duration-300 ease-out"
        style={{ transform: mounted ? "translateX(0)" : "translateX(100%)" }}
      >
        <button
          onClick={close}
          aria-label="Close"
          className="absolute right-3 top-3 z-10 rounded-md p-1.5 text-ink2 transition-colors hover:bg-white/5 hover:text-ink"
        >
          <X size={16} />
        </button>

        {!company ? (
          <div className="flex h-full items-center justify-center">
            <span className="section-title text-ink3">
              {selectedTicker} — NOT IN FEED
            </span>
          </div>
        ) : (
          <div className="p-5">
            {/* header */}
            <div className="flex items-start gap-3 pr-8">
              <div
                className="tnum flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-sm font-semibold text-ink"
                style={{ background: "rgba(255,255,255,0.06)" }}
              >
                {monogram(company.name)}
              </div>
              <div className="min-w-0">
                <div className="truncate text-[15px] font-semibold text-ink">
                  {company.name}
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-[11px] text-ink2">
                  <span className="tnum font-mono text-ink">
                    {company.ticker}
                  </span>
                  <span className="text-ink3">•</span>
                  <span>{company.sector}</span>
                </div>
                <div className="mt-0.5 text-[11px] text-ink3">
                  HQ: {company.city}, {company.country}
                </div>
              </div>
            </div>

            {/* price */}
            <div className="mt-4 flex items-baseline gap-3">
              <span className="tnum text-2xl font-semibold text-ink">
                {fmtPrice(company.current_price)}
              </span>
              <span
                className={`tnum text-sm font-medium ${changeClass(company.change_pct)}`}
              >
                {fmtPct(company.change_pct)}
              </span>
              {company.is_bankrupt && (
                <span className="ml-auto flex items-center gap-1 rounded-md border border-down/40 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-down">
                  <AlertTriangle size={11} /> Bankrupt
                </span>
              )}
            </div>

            {/* stat rows */}
            <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3">
              <StatRow label="Sentiment">
                <span
                  className="inline-block h-2.5 w-2.5 rounded-full"
                  style={{ background: sentimentColor(company.sentiment) }}
                />
                <span className="tnum text-ink">
                  {company.sentiment.toFixed(2)}
                </span>
              </StatRow>
              <StatRow label="Volatility">
                <span className="tnum text-ink">
                  {company.volatility.toFixed(3)}
                </span>
              </StatRow>
              <StatRow label="Market Cap">
                <span className="tnum text-ink">
                  {fmtCap(company.market_cap)}
                </span>
              </StatRow>
              <StatRow label="Anchor">
                <span className="tnum text-ink">
                  {fmtPrice(company.anchor_price)}
                </span>
              </StatRow>
            </div>

            {/* chart */}
            <div className="mt-5 h-[240px]">
              <LiveStockChart ticker={selectedTicker} />
            </div>

            {/* description */}
            {company.description && (
              <p className="mt-5 text-[12.5px] leading-relaxed text-ink2">
                {company.description}
              </p>
            )}

            {/* posts */}
            <div className="mt-6">
              <div className="mb-2 flex items-center gap-2">
                <TrendingUp size={12} className="text-ink3" />
                <span className="section-title">Recent Chatter</span>
              </div>
              {posts.length === 0 ? (
                <div className="py-4 text-center text-[11px] text-ink3">
                  No posts yet
                </div>
              ) : (
                <ul className="scroll-thin flex flex-col gap-2">
                  {posts.map((post) => (
                    <li
                      key={post.post_id}
                      className="rounded-md bg-white/[0.02] py-2 pl-3 pr-2.5"
                      style={{
                        borderLeft: `2px solid ${sentimentColor(post.sentiment)}`,
                      }}
                    >
                      <div className="flex items-center gap-1.5 text-[11px]">
                        <span className="font-medium text-ink">
                          {post.author_display}
                        </span>
                        <span className="text-ink3">{post.handle}</span>
                        <span className="text-ink3">•</span>
                        <span className="tnum text-ink3">
                          {timeAgo(post.ts)}
                        </span>
                      </div>
                      <p className="mt-1 text-[12px] leading-snug text-ink2">
                        {post.content}
                      </p>
                      <div className="mt-1.5 flex items-center gap-3 text-[10px] text-ink3">
                        <span className="flex items-center gap-1">
                          <Heart size={10} />
                          <span className="tnum">{post.likes}</span>
                        </span>
                        <span className="flex items-center gap-1">
                          <Repeat2 size={11} />
                          <span className="tnum">{post.reposts}</span>
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}

function StatRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="section-title mb-1">{label}</div>
      <div className="flex items-center gap-1.5 text-[13px]">{children}</div>
    </div>
  );
}
