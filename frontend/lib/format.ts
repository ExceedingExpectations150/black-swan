// Shared formatting + color helpers. All numbers render with tabular-nums
// (add the `tnum` class at the element level).

export function fmtPrice(v: number): string {
  return v.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function fmtPct(v: number): string {
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

export function fmtCap(v: number): string {
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  return `$${v.toFixed(0)}`;
}

export function changeClass(v: number): string {
  if (v > 0.0001) return "up";
  if (v < -0.0001) return "down";
  return "text-ink2";
}

// Sentiment -1..1 → red→grey→green hex (for map nodes, accent bars).
export function sentimentColor(s: number): string {
  const clamped = Math.max(-1, Math.min(1, s));
  if (clamped >= 0) {
    // grey (#8b8b8b) → green (#16c60c)
    return lerpHex("#8b8b8b", "#16c60c", clamped);
  }
  return lerpHex("#8b8b8b", "#ff4d4f", -clamped);
}

function lerpHex(a: string, b: string, t: number): string {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  const p = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
  return `#${p.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

export function monogram(name: string): string {
  const words = name.replace(/[^a-zA-Z0-9 ]/g, "").trim().split(/\s+/);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

export function timeAgo(ts: string | null): string {
  if (!ts) return "now";
  const then = new Date(ts).getTime();
  if (Number.isNaN(then)) return "now";
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}
