"use client";

import { Newspaper, Heart, Repeat2, X } from "lucide-react";
import { useStore } from "@/lib/store";
import { sentimentColor, monogram, timeAgo } from "@/lib/format";
import type { SocialPostT } from "@/lib/types";

export default function NewsFeed() {
  const social = useStore((s) => s.social);
  const selectedTicker = useStore((s) => s.selectedTicker);
  const setSelectedTicker = useStore((s) => s.setSelectedTicker);

  const posts = selectedTicker
    ? social.filter((p) => p.author_ticker === selectedTicker)
    : social;

  return (
    <div className="panel flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-hair px-3 py-2.5">
        <div className="flex items-center gap-2">
          <Newspaper size={14} className="text-ink2" />
          <span className="section-title">Live News Feed</span>
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
            NO POSTS YET
          </div>
        ) : (
          <ul className="p-2">
            {posts.map((post, i) => (
              <PostCard key={post.post_id} post={post} isNewest={i === 0} />
            ))}
          </ul>
        )}
      </div>

      <div className="flex items-center justify-between border-t border-hair px-3 py-1.5">
        <span className="text-[9px] uppercase tracking-[0.14em] text-ink3">Wire + desk chatter</span>
        <span className="tnum font-mono text-[9px] uppercase tracking-[0.14em] text-ink3">
          {posts.length} posts
        </span>
      </div>
    </div>
  );
}

function PostCard({ post, isNewest }: { post: SocialPostT; isNewest: boolean }) {
  const isCompany = post.author_type === "company";
  return (
    <li
      className={`mb-1.5 flex gap-2.5 rounded-sm bg-white/[0.015] p-2.5 ${
        isNewest ? "post-enter" : ""
      }`}
    >
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white/[0.05] text-ink2 ring-1 ring-inset ring-hair">
        {isCompany ? (
          <span className="text-[10px] font-semibold">
            {monogram(post.author_display)}
          </span>
        ) : (
          <Newspaper size={15} />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-1.5 text-[11px]">
          <div className="text-[12px] font-bold text-ink mt-0.5 truncate">
            {post.author_display} <span className="text-ink3 font-normal font-mono text-[11px] ml-1">{post.handle}</span>
          </div>
          <span className="text-ink3">· {timeAgo(post.ts)}</span>
          <span
            className="inline-block h-1.5 w-1.5 self-center rounded-full"
            title={`Sentiment ${post.sentiment.toFixed(2)}`}
            style={{ backgroundColor: sentimentColor(post.sentiment) }}
          />
        </div>
        <p className="mt-1 text-xs leading-relaxed text-ink2">{post.content}</p>
        <div className="mt-1.5 flex items-center gap-4 text-[10px] text-ink3">
          <span className="flex items-center gap-1">
            <Heart size={11} />
            <span className="tnum">{post.likes}</span>
          </span>
          <span className="flex items-center gap-1">
            <Repeat2 size={11} />
            <span className="tnum">{post.reposts}</span>
          </span>
        </div>
      </div>
    </li>
  );
}
