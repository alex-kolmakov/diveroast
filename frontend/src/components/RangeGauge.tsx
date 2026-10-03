import { useState } from "react";
import { METRICS, NOT_RECORDED, ZONE_TEXT, metricKind } from "@/lib/metrics";
import type { DiveMetricPoint, MetricRange } from "@/types";

interface Props {
  metric: MetricRange;
}

const ZONE_COLORS = {
  safe: "bg-safe",
  warning: "bg-warning",
  danger: "bg-danger",
};

const COLD_COLOR = "#3b82f6";
const TEMPERATE_COLOR = "#22c55e";
const TROPICAL_COLOR = "#ef4444";

const fmt = (v: number | null) => (v == null ? "–" : v.toFixed(1));

/** One name per metric; unknown labels (older snapshots) are shown as sent. */
function displayName(metric: MetricRange): string {
  const kind = metricKind(metric.label);
  return kind ? METRICS[kind].name : metric.label;
}

/** Metric the computer didn't log for any dive: say so instead of drawing a gauge. */
function NotRecorded({ name }: { name: string }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{name}</span>
        <span className="text-sm text-muted-foreground">{NOT_RECORDED}</span>
      </div>
      <div className="h-5 w-full rounded-full border border-dashed border-muted" />
    </div>
  );
}

/** "recorded for 141/197 dives" when some dives lack this metric. */
function coverage(metric: MetricRange): string | null {
  if (!metric.total || metric.recorded >= metric.total) return null;
  return `recorded for ${metric.recorded}/${metric.total} dives`;
}

/** One equal-width segment per dive, highlighted on hover. */
function DiveBar({
  dives,
  hovered,
  onHover,
  color,
  children,
}: {
  dives: DiveMetricPoint[];
  hovered: DiveMetricPoint | null;
  onHover: (dive: DiveMetricPoint | null) => void;
  color: (dive: DiveMetricPoint) => { className?: string; background?: string };
  children?: React.ReactNode;
}) {
  const segmentWidth = 100 / dives.length;
  return (
    <div className="relative h-5 w-full overflow-hidden rounded-full bg-muted/50">
      {dives.map((dive, i) => {
        const { className = "", background } = color(dive);
        return (
          <div
            key={dive.dive_number}
            className={`absolute inset-y-0 transition-opacity ${className} ${
              hovered && hovered.dive_number !== dive.dive_number
                ? "opacity-40"
                : "opacity-80 hover:opacity-100"
            }`}
            style={{
              left: `${i * segmentWidth}%`,
              width: `${segmentWidth}%`,
              background,
              borderRight: i < dives.length - 1 ? "1px solid oklch(0.13 0.03 230 / 50%)" : "none",
            }}
            onMouseEnter={() => onHover(dive)}
            onMouseLeave={() => onHover(null)}
          />
        );
      })}
      {children}
    </div>
  );
}

function tempColor(temp: number): string {
  if (temp < 15) return COLD_COLOR;
  if (temp <= 24) return TEMPERATE_COLOR;
  return TROPICAL_COLOR;
}

function tempNickname(cold: number, temperate: number, tropical: number): { title: string; color: string } {
  const total = cold + temperate + tropical || 1;
  const cPct = cold / total;
  const tPct = temperate / total;
  const trPct = tropical / total;
  const color = trPct >= 0.65 ? TROPICAL_COLOR : cPct >= 0.65 ? COLD_COLOR : TEMPERATE_COLOR;

  let title = "All-Around Diver";
  if (trPct >= 0.85) title = "Coral Chaser";
  else if (trPct >= 0.65) title = "Warmwater Regular";
  else if (cPct >= 0.85) title = "Ice Diver";
  else if (cPct >= 0.65) title = "Cold Water Devotee";
  else if (tPct >= 0.65) title = "Temperate Explorer";
  else if (cPct >= 0.4 && trPct <= 0.2) title = "Cold Water Diver";
  else if (trPct >= 0.4 && cPct <= 0.2) title = "Sun Seeker";
  else if (Math.abs(cPct - trPct) < 0.15 && tPct < 0.3) title = "Extreme Contrarian";
  return { title, color };
}

/** Temperature range, per-dive bar and exposure by band, in one gauge. */
export function TemperatureGauge({
  metric,
  exposure,
}: Props & { exposure?: Record<string, number> }) {
  const [hoveredDive, setHoveredDive] = useState<DiveMetricPoint | null>(null);
  const dives = metric.per_dive;
  if (dives.length === 0) return <NotRecorded name={METRICS.temp.name} />;

  const bands = [
    { label: "Cold (<15°C)", count: exposure?.["Cold (<15°C)"] ?? 0, color: COLD_COLOR },
    { label: "Temperate (15–24°C)", count: exposure?.["Temperate (15-24°C)"] ?? 0, color: TEMPERATE_COLOR },
    { label: "Tropical (>24°C)", count: exposure?.["Tropical (>24°C)"] ?? 0, color: TROPICAL_COLOR },
  ];
  const total = bands.reduce((sum, b) => sum + b.count, 0);
  const nickname = total > 0 ? tempNickname(bands[0].count, bands[1].count, bands[2].count) : null;
  const meta = [`avg ${fmt(metric.avg_val)} ${metric.unit}`, coverage(metric)].filter(Boolean).join(" · ");

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{METRICS.temp.name}</span>
        <span className="text-sm font-semibold text-muted-foreground">
          {fmt(metric.min_val)}° – {fmt(metric.max_val)}°
        </span>
      </div>
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{meta}</span>
        {nickname && (
          <span className="font-semibold" style={{ color: nickname.color }}>
            {nickname.title}
          </span>
        )}
      </div>

      <DiveBar
        dives={dives}
        hovered={hoveredDive}
        onHover={setHoveredDive}
        color={(dive) => ({ background: tempColor(dive.value) })}
      />

      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {hoveredDive ? (
          <span style={{ color: tempColor(hoveredDive.value) }}>
            Dive #{hoveredDive.dive_number}: {hoveredDive.value.toFixed(1)} {metric.unit}
          </span>
        ) : (
          bands
            .filter((b) => total === 0 || b.count > 0)
            .map((b) => (
              <span key={b.label}>
                {total > 0 && (
                  <span className="font-semibold" style={{ color: b.color }}>
                    {b.count}{" "}
                  </span>
                )}
                <span style={total > 0 ? undefined : { color: b.color }}>{b.label}</span>
                {total > 0 && ` · ${((b.count / total) * 100).toFixed(0)}%`}
              </span>
            ))
        )}
      </div>
    </div>
  );
}

export function RangeGauge({ metric }: Props) {
  const [hoveredDive, setHoveredDive] = useState<DiveMetricPoint | null>(null);
  const name = displayName(metric);
  const dives = metric.per_dive;
  if (dives.length === 0) return <NotRecorded name={name} />;

  // NDL is inverted: lower is worse, so its thresholds run high to low.
  const inverted = metric.warning_upper < metric.safe_upper;
  const hasThresholds = metric.safe_upper > 0 && metric.warning_upper > 0;
  const overLimit = dives.filter((d) => d.zone === "danger").length;
  const kind = metricKind(metric.label);
  const meta = `avg ${fmt(metric.avg_val)} · range ${fmt(metric.min_val)}–${fmt(metric.max_val)}`;
  // What the number means and where the limits sit, under the bar.
  const legend = [
    kind && METRICS[kind].qualifier,
    hasThresholds &&
      (inverted
        ? `safe ≥ ${metric.safe_upper} · danger < ${metric.warning_upper} ${metric.unit}`
        : `safe ≤ ${metric.safe_upper} · limit ${metric.warning_upper} ${metric.unit}`),
    coverage(metric),
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{name}</span>
        <span className={`shrink-0 text-sm font-semibold ${ZONE_TEXT[metric.zone]}`}>
          {metric.worst_val != null ? `worst ${metric.worst_val.toFixed(1)} ${metric.unit}` : "–"}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{meta}</span>
        {overLimit > 0 && (
          <span className="shrink-0 font-semibold text-danger">
            {overLimit} {inverted ? `under ${metric.warning_upper} ${metric.unit}` : "over limit"}
          </span>
        )}
      </div>

      <DiveBar
        dives={dives}
        hovered={hoveredDive}
        onHover={setHoveredDive}
        color={(dive) => ({ className: ZONE_COLORS[dive.zone] })}
      >
        {hasThresholds &&
          [...new Set([metric.safe_upper, metric.warning_upper])].map((threshold) => (
            <div
              key={threshold}
              className="absolute inset-y-0 w-px bg-foreground/30"
              style={{ left: `${_thresholdPercent(threshold, metric)}%` }}
            />
          ))}
      </DiveBar>

      <div className="text-xs text-muted-foreground">
        {hoveredDive ? (
          <span className={ZONE_TEXT[hoveredDive.zone]}>
            Dive #{hoveredDive.dive_number}: {hoveredDive.value.toFixed(1)} {metric.unit}
          </span>
        ) : (
          <span>{legend || "\u00a0"}</span>
        )}
      </div>
    </div>
  );
}

/** Convert a threshold value to a percentage position based on where it falls among sorted dives */
function _thresholdPercent(threshold: number, metric: MetricRange): number {
  const dives = metric.per_dive;
  if (dives.length === 0) return 0;
  // Find how many dives are below the threshold
  const below = dives.filter((d) => d.value <= threshold).length;
  return (below / dives.length) * 100;
}
