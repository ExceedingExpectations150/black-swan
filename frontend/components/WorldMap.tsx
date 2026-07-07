"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ComposableMap,
  Geographies,
  Geography,
  Marker,
  ZoomableGroup,
} from "react-simple-maps";
import type { Company } from "@/lib/types";
import { useStore, useCompanyList } from "@/lib/store";
import { sentimentColor, fmtPrice, fmtPct, changeClass, monogram } from "@/lib/format";

const GEO_URL = "/countries-110m.json";

const MIN_RADIUS = 3.5;
const MAX_RADIUS = 8;
const VOLATILE_THRESHOLD = 0.03;
const BANKRUPT_COLOR = "#5a5a5a";

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

  // White logo-style pill, offset up-right of the glowing node.
  const dx = radius + 9;
  const dy = -(radius + 13);
  const mono = monogram(company.name);
  const chipH = 17;
  const padL = 17;
  const chipW = padL + 8 + company.name.length * 6.1;

  return (
    <Marker
      coordinates={[company.lon, company.lat]}
      onMouseEnter={() => onEnter(company.ticker)}
      onMouseLeave={onLeave}
      onClick={() => onSelect(company.ticker)}
      style={{ default: { cursor: "pointer" }, hover: { cursor: "pointer" }, pressed: {} }}
    >
      <g opacity={groupOpacity}>
        <circle
          className={pulse ? "node-pulse" : undefined}
          r={radius * 2.2}
          fill={color}
          opacity={0.3}
          style={{ filter: "blur(4px)", pointerEvents: "none" }}
        />
        <circle
          r={radius}
          fill={color}
          stroke={selected ? "#ffffff" : "none"}
          strokeWidth={selected ? 1.4 : 0}
          style={{ pointerEvents: "none" }}
        />
        <circle
          r={Math.max(1.2, radius * 0.42)}
          fill="#ffffff"
          opacity={0.95}
          style={{ pointerEvents: "none" }}
        />

        {(showLabel || selected) && (
          <>
            <line
              x1={0}
              y1={0}
              x2={dx}
              y2={dy + chipH / 2}
              stroke="rgba(255,255,255,0.35)"
              strokeWidth={0.6}
              style={{ pointerEvents: "none" }}
            />
            <g transform={`translate(${dx}, ${dy})`} style={{ pointerEvents: "none" }}>
              <rect width={chipW} height={chipH} rx={4} fill="#f5f5f5" />
              <circle cx={9} cy={chipH / 2} r={4} fill={color} />
              <text
                x={padL}
                y={chipH / 2 + 3.2}
                fontSize={10}
                fontWeight={600}
                fill="#0a0a0a"
                fontFamily="var(--font-display)"
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

const MarkerNode = memo(MarkerNodeBase);

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
            zoom={1}
            minZoom={1}
            maxZoom={8}
            translateExtent={[
              [-size.w * 0.5, -size.h * 0.5],
              [size.w * 1.5, size.h * 1.5],
            ]}
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
                        fill: "#080808",
                        stroke: "var(--map-stroke)",
                        strokeWidth: 0.5,
                        outline: "none",
                      },
                      hover: {
                        fill: "rgba(255,255,255,0.09)",
                        stroke: "rgba(255,255,255,0.5)",
                        strokeWidth: 0.75,
                        outline: "none",
                      },
                      pressed: {
                        fill: "rgba(255,255,255,0.14)",
                        stroke: "rgba(255,255,255,0.6)",
                        strokeWidth: 0.75,
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
