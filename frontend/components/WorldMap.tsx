"use client";

import { memo, useCallback, useMemo, useState } from "react";
import { geoInterpolate } from "d3-geo";
import {
  ComposableMap,
  Geographies,
  Geography,
  Line,
  Marker,
} from "react-simple-maps";
import type { Company } from "@/lib/types";
import { useStore, useCompanyList } from "@/lib/store";
import {
  sentimentColor,
  fmtPrice,
  fmtPct,
  changeClass,
  monogram,
} from "@/lib/format";

const GEO_URL = "/countries-110m.json";
const PROJECTION = "geoEqualEarth";
const PROJECTION_CONFIG = { scale: 150, center: [0, 15] as [number, number] };

const MIN_RADIUS = 3;
const MAX_RADIUS = 9;
const VOLATILE_THRESHOLD = 0.03;
const ARC_SAMPLES = 24;
const BANKRUPT_COLOR = "#5a5a5a";

interface NodeDatum {
  company: Company;
  radius: number;
  color: string;
  pulse: boolean;
}

interface ArcDatum {
  id: string;
  coordinates: [number, number][];
}

function MarkerNodeBase({
  node,
  selected,
  onEnter,
  onLeave,
  onSelect,
}: {
  node: NodeDatum;
  selected: boolean;
  onEnter: (ticker: string) => void;
  onLeave: () => void;
  onSelect: (ticker: string) => void;
}) {
  const { company, radius, color, pulse } = node;
  const bankrupt = company.is_bankrupt;
  const groupOpacity = bankrupt ? 0.3 : 1;

  // Offset label chip (upper-right of the node).
  const dx = radius + 7;
  const dy = -(radius + 7);
  const mono = monogram(company.name);
  const chipH = 14;
  const chipW = 20 + company.name.length * 5.3;

  return (
    <Marker
      coordinates={[company.lon, company.lat]}
      onMouseEnter={() => onEnter(company.ticker)}
      onMouseLeave={onLeave}
      onClick={() => onSelect(company.ticker)}
      style={{ default: { cursor: "pointer" }, hover: { cursor: "pointer" }, pressed: {} }}
    >
      <g opacity={groupOpacity}>
        {/* Glowing halo */}
        <circle
          className={pulse ? "node-pulse" : undefined}
          r={radius * 2.1}
          fill={color}
          opacity={0.28}
          style={{ filter: "blur(4px)", pointerEvents: "none" }}
        />
        {/* Sentiment dot */}
        <circle
          r={radius}
          fill={color}
          stroke={selected ? "#f5f5f5" : "none"}
          strokeWidth={selected ? 1.2 : 0}
          style={{ pointerEvents: "none" }}
        />
        {/* White glowing core */}
        <circle
          r={Math.max(1, radius * 0.4)}
          fill="#ffffff"
          opacity={0.9}
          style={{ pointerEvents: "none" }}
        />

        {/* Connector + label chip */}
        <line
          x1={0}
          y1={0}
          x2={dx}
          y2={dy}
          stroke="var(--map-stroke)"
          strokeWidth={0.5}
          style={{ pointerEvents: "none" }}
        />
        <g transform={`translate(${dx}, ${dy - chipH / 2})`} style={{ pointerEvents: "none" }}>
          <rect
            width={chipW}
            height={chipH}
            rx={3}
            fill="rgba(0,0,0,0.6)"
            stroke="var(--panel-border)"
            strokeWidth={0.5}
          />
          <text x={6} y={chipH / 2 + 3} fontSize={9} fontWeight={700} fill={color}>
            {mono}
          </text>
          <text x={18} y={chipH / 2 + 3} fontSize={9} fill="#e8e8e8">
            {company.name}
          </text>
        </g>

        {/* Transparent hit target for hover/click */}
        <circle r={radius + 5} fill="transparent" />
      </g>
    </Marker>
  );
}

const MarkerNode = memo(MarkerNodeBase);

function Tooltip({ company }: { company: Company }) {
  return (
    <Marker coordinates={[company.lon, company.lat]} style={{ default: {}, hover: {}, pressed: {} }}>
      <foreignObject
        x={12}
        y={-70}
        width={190}
        height={58}
        style={{ overflow: "visible", pointerEvents: "none" }}
      >
        <div
          className="panel"
          style={{ padding: "6px 8px", display: "inline-block", lineHeight: 1.35 }}
        >
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text)" }}>
            {company.name}
          </div>
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

  const nodes = useMemo<NodeDatum[]>(() => {
    const caps = companies.filter((c) => c.market_cap > 0).map((c) => Math.log(c.market_cap));
    const minLog = caps.length ? Math.min(...caps) : 0;
    const maxLog = caps.length ? Math.max(...caps) : 1;
    const span = maxLog - minLog || 1;
    return companies.map((c) => {
      const radius =
        c.market_cap > 0
          ? Math.max(
              MIN_RADIUS,
              Math.min(MAX_RADIUS, MIN_RADIUS + ((Math.log(c.market_cap) - minLog) / span) * (MAX_RADIUS - MIN_RADIUS)),
            )
          : MIN_RADIUS;
      return {
        company: c,
        radius,
        color: c.is_bankrupt ? BANKRUPT_COLOR : sentimentColor(c.sentiment),
        pulse: !c.is_bankrupt && c.volatility > VOLATILE_THRESHOLD,
      };
    });
  }, [companies]);

  // Great-circle arcs: chain each company to the next within its sector.
  const arcs = useMemo<ArcDatum[]>(() => {
    const bySector = new Map<string, Company[]>();
    for (const c of companies) {
      const list = bySector.get(c.sector) ?? [];
      list.push(c);
      bySector.set(c.sector, list);
    }
    const out: ArcDatum[] = [];
    for (const list of Array.from(bySector.values())) {
      const sorted = [...list].sort((a, b) => a.ticker.localeCompare(b.ticker));
      for (let i = 0; i < sorted.length - 1; i++) {
        const from = sorted[i];
        const to = sorted[i + 1];
        const interp = geoInterpolate([from.lon, from.lat], [to.lon, to.lat]);
        const coordinates: [number, number][] = [];
        for (let s = 0; s <= ARC_SAMPLES; s++) {
          coordinates.push(interp(s / ARC_SAMPLES) as [number, number]);
        }
        out.push({ id: `${from.ticker}-${to.ticker}`, coordinates });
      }
    }
    return out;
  }, [companies]);

  const handleEnter = useCallback((ticker: string) => setHovered(ticker), []);
  const handleLeave = useCallback(() => setHovered(null), []);
  const handleSelect = useCallback((ticker: string) => {
    useStore.getState().setSelectedTicker(ticker);
  }, []);

  const hoveredCompany = hovered ? nodes.find((n) => n.company.ticker === hovered)?.company : undefined;

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <ComposableMap
        projection={PROJECTION}
        projectionConfig={PROJECTION_CONFIG}
        style={{ width: "100%", height: "100%" }}
      >
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
                      fill: "#0a0a0a",
                      stroke: "var(--map-stroke)",
                      strokeWidth: 0.5,
                      outline: "none",
                    },
                    hover: {
                      fill: "#0a0a0a",
                      stroke: "var(--map-stroke)",
                      strokeWidth: 0.5,
                      outline: "none",
                    },
                    pressed: {
                      fill: "#0a0a0a",
                      stroke: "var(--map-stroke)",
                      strokeWidth: 0.5,
                      outline: "none",
                    },
                  }}
                />
              ))
          }
        </Geographies>

        {/* Sector arcs (behind nodes) */}
        {arcs.map((arc) => (
          <Line
            key={arc.id}
            coordinates={arc.coordinates}
            stroke="var(--arc)"
            strokeWidth={0.6}
            fill="none"
            strokeLinecap="round"
          />
        ))}

        {/* Company nodes */}
        {nodes.map((node) => (
          <MarkerNode
            key={node.company.ticker}
            node={node}
            selected={node.company.ticker === selectedTicker}
            onEnter={handleEnter}
            onLeave={handleLeave}
            onSelect={handleSelect}
          />
        ))}

        {/* Tooltip on top of everything */}
        {hoveredCompany && <Tooltip company={hoveredCompany} />}
      </ComposableMap>

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
          <span
            style={{
              fontSize: 11,
              letterSpacing: "0.22em",
              textTransform: "uppercase",
              color: "var(--text-3)",
            }}
          >
            Awaiting company nodes
          </span>
        </div>
      )}
    </div>
  );
}
