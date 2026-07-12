"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ComposableMap,
  Geographies,
  Geography,
  Graticule,
  Marker,
  ZoomableGroup,
} from "react-simple-maps";
import type { Company } from "@/lib/types";
import { useStore, useCompanyList } from "@/lib/store";
import { fmtPrice, fmtPct, changeClass } from "@/lib/format";

const GEO_URL = "/countries-50m.json"; // higher-detail coastlines/borders

const MIN_RADIUS = 3.5;
const MAX_RADIUS = 8;
const VOLATILE_THRESHOLD = 0.03;
const BANKRUPT_COLOR = "#5a5a5a";
const NODE_UP = { r: 0x16, g: 0xc6, b: 0x0c }; // #16c60c
const NODE_DOWN = { r: 0xff, g: 0x4d, b: 0x4f }; // #ff4d4f
const NODE_FLAT = { r: 0x6b, g: 0x70, b: 0x6d }; // dim neutral for ~0%
// change_pct at which the spectrum saturates to full green/red.
const PERF_FULL_SCALE_PCT = 8;
// Quantize the spectrum so microscopic per-tick moves don't produce a new
// color string every tick (which would defeat MarkerNode's memo).
const PERF_STEPS = 20;

function perfColor(changePct: number): string {
  const raw = Math.max(-1, Math.min(1, changePct / PERF_FULL_SCALE_PCT));
  const t = Math.round(raw * PERF_STEPS) / PERF_STEPS;
  const from = NODE_FLAT;
  const to = t >= 0 ? NODE_UP : NODE_DOWN;
  const k = Math.abs(t);
  const mix = (a: number, b: number) => Math.round(a + (b - a) * k);
  return `rgb(${mix(from.r, to.r)},${mix(from.g, to.g)},${mix(from.b, to.b)})`;
}

interface NodeDatum {
  company: Company;
  radius: number;
  color: string;
  pulse: boolean;
}

function MarkerNodeBase({
  node,
  selected,
  showLabel,
  onEnter,
  onLeave,
  onSelect,
}: {
  node: NodeDatum;
  selected: boolean;
  showLabel: boolean;
  onEnter: (ticker: string) => void;
  onLeave: () => void;
  onSelect: (ticker: string) => void;
}) {
  const { company, radius, color, pulse } = node;
  const groupOpacity = company.is_bankrupt ? 0.3 : 1;
  // Small flat square; faint market-cap size cue, kept tiny.
  const side = Math.max(3, radius * 0.7);

  // Terminal-dark chip, offset up-right of the node.
  const dx = side + 8;
  const dy = -(side + 11);
  const chipH = 17;
  const padL = 17;
  const chipW = padL + 10 + company.name.length * 6.1;

  return (
    <Marker
      coordinates={[company.lon, company.lat]}
      onMouseEnter={() => onEnter(company.ticker)}
      onMouseLeave={onLeave}
      onClick={() => onSelect(company.ticker)}
      style={{ default: { cursor: "pointer" }, hover: { cursor: "pointer" }, pressed: {} }}
    >
      <g opacity={groupOpacity}>
        {/* Soft halo (two layered squares — no SVG filters, stays cheap x51) */}
        <rect
          x={-side * 1.6}
          y={-side * 1.6}
          width={side * 3.2}
          height={side * 3.2}
          fill={color}
          opacity={0.07}
          style={{ pointerEvents: "none" }}
        />
        <rect
          x={-side}
          y={-side}
          width={side * 2}
          height={side * 2}
          fill={color}
          opacity={0.16}
          style={{ pointerEvents: "none" }}
        />
        <rect
          className={pulse ? "node-pulse" : undefined}
          x={-side / 2}
          y={-side / 2}
          width={side}
          height={side}
          fill={color}
          stroke={selected ? "#ffffff" : "rgba(0,0,0,0.55)"}
          strokeWidth={selected ? 1.2 : 0.5}
          style={{ pointerEvents: "none" }}
        />

        {(showLabel || selected) && (
          <>
            <line
              x1={0}
              y1={0}
              x2={dx}
              y2={dy + chipH / 2}
              stroke="rgba(255,255,255,0.3)"
              strokeWidth={0.6}
              style={{ pointerEvents: "none" }}
            />
            <g transform={`translate(${dx}, ${dy})`} style={{ pointerEvents: "none" }}>
              <rect
                width={chipW}
                height={chipH}
                rx={2}
                fill="rgba(8,8,8,0.94)"
                stroke="rgba(255,255,255,0.18)"
                strokeWidth={0.6}
              />
              <circle cx={10} cy={chipH / 2} r={2.5} fill={color} />
              <text
                x={padL}
                y={chipH / 2 + 3.2}
                fontSize={9.5}
                fontWeight={600}
                fill="#f5f5f5"
                fontFamily="var(--font-display)"
                letterSpacing="0.04em"
              >
                {company.name}
              </text>
            </g>
          </>
        )}

        <circle r={radius + 6} fill="transparent" />
      </g>
    </Marker>
  );
}

// Custom comparator: `node` is a fresh object every tick (the store clones
// companies on each price update), but the marker only draws from these
// fields — compare them by value so 51 SVG subtrees stop re-rendering on
// every tick.
const MarkerNode = memo(MarkerNodeBase, (prev, next) => {
  const a = prev.node;
  const b = next.node;
  return (
    prev.selected === next.selected &&
    prev.showLabel === next.showLabel &&
    a.radius === b.radius &&
    a.color === b.color &&
    a.pulse === b.pulse &&
    a.company.ticker === b.company.ticker &&
    a.company.name === b.company.name &&
    a.company.lon === b.company.lon &&
    a.company.lat === b.company.lat &&
    a.company.is_bankrupt === b.company.is_bankrupt
  );
});

function Tooltip({ company }: { company: Company }) {
  return (
    <Marker coordinates={[company.lon, company.lat]} style={{ default: {}, hover: {}, pressed: {} }}>
      <foreignObject x={14} y={-78} width={200} height={64} style={{ overflow: "visible", pointerEvents: "none" }}>
        <div className="panel" style={{ padding: "6px 9px", display: "inline-block", lineHeight: 1.4, background: "rgba(5,5,5,0.92)" }}>
          <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--text)" }}>{company.name}</div>
          <div className="tnum" style={{ fontSize: 10.5, color: "var(--text-2)" }}>
            {company.ticker} · {fmtPrice(company.current_price)}
          </div>
          <div className={`tnum ${changeClass(company.change_pct)}`} style={{ fontSize: 10.5 }}>
            {fmtPct(company.change_pct)}
          </div>
        </div>
      </foreignObject>
    </Marker>
  );
}

export default function WorldMap() {
  const companies = useCompanyList();
  const selectedTicker = useStore((s) => s.selectedTicker);
  const [hovered, setHovered] = useState<string | null>(null);

  // Measure the container so the map fills it edge-to-edge (no letterboxing).
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0].contentRect;
      setSize({ w: Math.round(cr.width), h: Math.round(cr.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // geoEqualEarth spans ~5.4 × scale wide; fill the width and let the poles
  // clip vertically, matching the reference's wide framing.
  const scale = size.w > 0 ? size.w / 5.3 : 160;
  // Framing lives on ZoomableGroup (center) so drag/zoom stay consistent;
  // the projection only carries scale.
  const projectionConfig = useMemo(() => ({ scale }), [scale]);

  const nodes = useMemo<NodeDatum[]>(() => {
    const caps = companies.filter((c) => c.market_cap > 0).map((c) => Math.log(c.market_cap));
    const minLog = caps.length ? Math.min(...caps) : 0;
    const maxLog = caps.length ? Math.max(...caps) : 1;
    const span = maxLog - minLog || 1;
    return companies.map((c) => {
      const raw =
        c.market_cap > 0
          ? Math.max(
              MIN_RADIUS,
              Math.min(MAX_RADIUS, MIN_RADIUS + ((Math.log(c.market_cap) - minLog) / span) * (MAX_RADIUS - MIN_RADIUS)),
            )
          : MIN_RADIUS;
      // Quantize to 0.5px steps: per-tick market-cap jitter would otherwise
      // change every radius microscopically and defeat MarkerNode's memo.
      const radius = Math.round(raw * 2) / 2;
      return {
        company: c,
        radius,
        // Session performance is the color channel: a red->neutral->green
        // spectrum scaled by how much the company has gained or lost.
        color: c.is_bankrupt ? BANKRUPT_COLOR : perfColor(c.change_pct),
        pulse: !c.is_bankrupt && c.volatility > VOLATILE_THRESHOLD,
      };
    });
  }, [companies]);

  const handleEnter = useCallback((ticker: string) => setHovered(ticker), []);
  const handleLeave = useCallback(() => setHovered(null), []);
  const handleSelect = useCallback((ticker: string) => {
    useStore.getState().setSelectedTicker(ticker);
  }, []);

  const hoveredCompany = hovered ? nodes.find((n) => n.company.ticker === hovered)?.company : undefined;

  return (
    <div ref={wrapRef} style={{ position: "relative", width: "100%", height: "100%" }}>
      {size.w > 0 && (
        <ComposableMap
          projection="geoEqualEarth"
          projectionConfig={projectionConfig}
          width={size.w}
          height={size.h}
          style={{ width: "100%", height: "100%" }}
        >
          <ZoomableGroup
            center={[10, 30]}
            zoom={1.4}
            minZoom={1}
            maxZoom={20}
            translateExtent={[
              [-size.w * 0.6, -size.h * 0.6],
              [size.w * 1.6, size.h * 1.6],
            ]}
          >
          {/* Faint lat/lon grid: cartographic depth without visual noise. */}
          <Graticule stroke="rgba(255,255,255,0.035)" strokeWidth={0.4} step={[20, 20]} />

          <Geographies geography={GEO_URL}>
            {({ geographies }) =>
              geographies
                .filter((geo) => geo.properties?.name !== "Antarctica")
                .map((geo) => (
                  <Geography
                    key={geo.rsmKey}
                    geography={geo}
                    style={{
                      default: {
                        fill: "#111312",
                        stroke: "rgba(255,255,255,0.26)",
                        strokeWidth: 0.4,
                        // Hairlines stay crisp at any zoom level instead of
                        // fattening as the user zooms in.
                        vectorEffect: "non-scaling-stroke",
                        outline: "none",
                      },
                      hover: {
                        fill: "rgba(255,255,255,0.06)",
                        stroke: "rgba(255,255,255,0.45)",
                        strokeWidth: 0.6,
                        vectorEffect: "non-scaling-stroke",
                        outline: "none",
                      },
                      pressed: {
                        fill: "rgba(255,255,255,0.1)",
                        stroke: "rgba(255,255,255,0.55)",
                        strokeWidth: 0.6,
                        vectorEffect: "non-scaling-stroke",
                        outline: "none",
                      },
                    }}
                  />
                ))
            }
          </Geographies>

          {nodes.map((node) => (
            <MarkerNode
              key={node.company.ticker}
              node={node}
              selected={node.company.ticker === selectedTicker}
              showLabel={node.company.ticker === hovered}
              onEnter={handleEnter}
              onLeave={handleLeave}
              onSelect={handleSelect}
            />
          ))}

          {hoveredCompany && <Tooltip company={hoveredCompany} />}
          </ZoomableGroup>
        </ComposableMap>
      )}

      {companies.length === 0 && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            pointerEvents: "none",
          }}
        >
          <span style={{ fontSize: 11, letterSpacing: "0.22em", textTransform: "uppercase", color: "var(--text-3)" }}>
            Awaiting company nodes
          </span>
        </div>
      )}
    </div>
  );
}
