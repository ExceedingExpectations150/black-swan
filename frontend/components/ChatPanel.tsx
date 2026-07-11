"use client";

// Trading Desk chat: local-only conversation with the backend desk model via
// POST /api/chat. No store involvement — the thread lives and dies with the
// component instance.

import { useEffect, useRef, useState } from "react";
import { MessageSquareText, Send } from "lucide-react";
import { API_BASE } from "@/lib/socket";

type DeskSource = "llm" | "offline";

type DeskMessage = {
  role: "user" | "assistant";
  content: string;
  /** Client-generated link-failure notice — excluded from the API payload. */
  isError?: boolean;
};

const LINK_DOWN_MESSAGE = "Desk link down — check the backend connection.";

export default function ChatPanel() {
  const [messages, setMessages] = useState<DeskMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [isPending, setIsPending] = useState(false);
  const [lastSource, setLastSource] = useState<DeskSource | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, isPending]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const content = draft.trim();
    if (!content || isPending) return;

    const withUser: DeskMessage[] = [...messages, { role: "user", content }];
    setMessages(withUser);
    setDraft("");
    setIsPending(true);

    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: withUser
            .filter((m) => !m.isError)
            .map(({ role, content: c }) => ({ role, content: c })),
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { reply: string; source: DeskSource };
      setMessages((prev) => [...prev, { role: "assistant", content: data.reply }]);
      setLastSource(data.source === "offline" ? "offline" : "llm");
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: LINK_DOWN_MESSAGE, isError: true },
      ]);
    } finally {
      setIsPending(false);
      inputRef.current?.focus();
    }
  };

  return (
    <div className="panel flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-hair px-3 py-2.5">
        <div className="flex items-center gap-2">
          <MessageSquareText size={14} className="text-ink2" />
          <span className="section-title">Trading Desk</span>
        </div>
        {lastSource && (
          <span className="flex items-center gap-1.5 rounded bg-white/[0.04] px-2 py-1 text-[9px] uppercase tracking-[0.16em] text-ink2">
            <span
              className={`inline-block h-1.5 w-1.5 rounded-full ${
                lastSource === "llm" ? "bg-up" : "bg-warn"
              }`}
            />
            {lastSource === "llm" ? "Desk Model" : "Stats Only"}
          </span>
        )}
      </div>

      <div ref={threadRef} className="scroll-thin flex-1 overflow-y-auto p-3">
        {messages.length === 0 && !isPending ? (
          <div className="flex h-full items-center justify-center text-[11px] tracking-wide text-ink3">
            Ask the desk about momentum, risk, or any ticker.
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[70%] rounded-sm bg-white/[0.07] px-3 py-2 text-xs leading-relaxed text-ink">
                    {m.content}
                  </div>
                </div>
              ) : (
                <div key={i} className="flex justify-start">
                  <div
                    className={`max-w-[85%] rounded-sm border px-3 py-2 ${
                      m.isError
                        ? "border-warn/40 bg-warn/5"
                        : "border-hair bg-white/[0.02]"
                    }`}
                  >
                    <div
                      className={`mb-1 text-[9px] uppercase tracking-[0.16em] ${
                        m.isError ? "text-warn" : "text-ink3"
                      }`}
                    >
                      {m.isError ? "Desk — Link" : "Desk"}
                    </div>
                    <p className="tnum whitespace-pre-wrap font-mono text-xs leading-relaxed text-ink2">
                      {m.content}
                    </p>
                  </div>
                </div>
              )
            )}
            {isPending && (
              <div className="flex justify-start">
                <div className="flex items-center gap-1.5 rounded-md border border-hair bg-white/[0.02] px-3 py-2.5">
                  {[0, 150, 300].map((delay) => (
                    <span
                      key={delay}
                      className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-white/40"
                      style={{ animationDelay: `${delay}ms` }}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <form
        onSubmit={submit}
        className="flex shrink-0 items-center gap-2 border-t border-hair px-3 py-2.5"
      >
        <input
          ref={inputRef}
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={isPending}
          placeholder="Message the desk…"
          className="min-w-0 flex-1 rounded-md border border-hair bg-white/[0.03] px-3 py-2 text-xs text-ink placeholder:text-ink3 outline-none transition focus:border-white/20 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={isPending || !draft.trim()}
          aria-label="Send message"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-hair bg-white/[0.05] text-ink2 transition hover:bg-white/[0.09] hover:text-ink disabled:opacity-40 disabled:hover:bg-white/[0.05] disabled:hover:text-ink2"
        >
          <Send size={13} />
        </button>
      </form>
    </div>
  );
}
