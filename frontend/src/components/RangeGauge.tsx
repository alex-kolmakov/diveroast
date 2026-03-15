import { useState } from "react";
import type { DiveMetricPoint, MetricRange } from "@/types";

interface Props {
  metric: MetricRange;
}

const ZONE_COLORS = {
  safe: "bg-safe",
  warning: "bg-warning",
  danger: "bg-danger",
};

const ZONE_TEXT = {
  safe: "text-safe",
  warning: "text-warning",
  danger: "text-danger",
};


function TemperatureGauge({ metric }: Props) {
  const [hoveredDive, setHoveredDive] = useState<DiveMetricPoint | null>(null);
  const dives = metric.per_dive;
  if (dives.length === 0) return null;

  const totalDives = dives.length;
  const segmentWidth = 100 / totalDives;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{metric.label}</span>
        <span className="text-sm font-semibold text-muted-foreground">
          {metric.min_val.toFixed(1)}° – {metric.max_val.toFixed(1)}°
        </span>
      </div>

      {/* Same equal-width segment bar as other metrics, colored by temperature band */}
      <div className="relative h-5 w-full overflow-hidden rounded-full bg-muted/50">
        {dives.map((dive, i) => (
          <div
            key={dive.dive_number}
            className={`absolute inset-y-0 transition-opacity ${
              hoveredDive && hoveredDive.dive_number !== dive.dive_number
                ? "opacity-40"
                : "opacity-80 hover:opacity-100"
            }`}
            style={{
              left: `${i * segmentWidth}%`,
              width: `${segmentWidth}%`,
              background: _tempColor(dive.value),
              borderRight: i < totalDives - 1 ? "1px solid oklch(0.13 0.03 230 / 50%)" : "none",
            }}
            onMouseEnter={() => setHoveredDive(dive)}
            onMouseLeave={() => setHoveredDive(null)}
          />
        ))}
      </div>

      {/* Zone labels + hover tooltip */}
      <div className="flex justify-between text-xs text-muted-foreground">
        {hoveredDive ? (
          <span style={{ color: _tempColor(hoveredDive.value) }}>
            Dive #{hoveredDive.dive_number}: {hoveredDive.value.toFixed(1)} {metric.unit}
          </span>
        ) : (
          <>
            <span className="text-blue-400">Cold &lt;15°</span>
            <span className="text-green-400">Temperate</span>
            <span className="text-red-400">Tropical &gt;24°</span>
          </>
        )}
      </div>
    </div>
  );
}

/** Pick display colour matching the absolute-scale band zones. */
function _tempColor(temp: number): string {
  if (temp < 15) return "#3b82f6";
  if (temp < 24) return "#22c55e";
  return "#ef4444";
}

export function RangeGauge({ metric }: Props) {
  const [hoveredDive, setHoveredDive] = useState<DiveMetricPoint | null>(null);

  if (metric.label === "Avg Temperature") {
    return <TemperatureGauge metric={metric} />;
  }

  const dives = metric.per_dive;
  const totalDives = dives.length;
  if (totalDives === 0) return null;

  // Each dive gets an equal-width segment of the bar
  const segmentWidth = 100 / totalDives;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{metric.label}</span>
        <span className={`text-sm font-semibold ${ZONE_TEXT[metric.zone]}`}>
          {metric.worst_val != null
            ? `${metric.worst_val.toFixed(1)} ${metric.unit}`
            : `${metric.min_val.toFixed(1)}° – ${metric.max_val.toFixed(1)}°`}
        </span>
      </div>

      {/* Gauge bar — each dive is a segment */}
      <div className="relative h-5 w-full overflow-hidden rounded-full bg-muted/50">
        {dives.map((dive, i) => (
          <div
            key={dive.dive_number}
            className={`absolute inset-y-0 transition-opacity ${ZONE_COLORS[dive.zone]} ${
              hoveredDive && hoveredDive.dive_number !== dive.dive_number
                ? "opacity-40"
                : "opacity-80 hover:opacity-100"
            }`}
            style={{
              left: `${i * segmentWidth}%`,
              width: `${segmentWidth}%`,
              borderRight: i < totalDives - 1 ? "1px solid oklch(0.13 0.03 230 / 50%)" : "none",
            }}
            onMouseEnter={() => setHoveredDive(dive)}
            onMouseLeave={() => setHoveredDive(null)}
          />
        ))}

        {/* Threshold markers */}
        {metric.safe_upper > 0 && (
          <div
            className="absolute inset-y-0 w-px bg-foreground/30"
            style={{ left: `${_thresholdPercent(metric.safe_upper, metric)}%` }}
          />
        )}
        {metric.warning_upper > 0 && metric.warning_upper !== metric.safe_upper && (
          <div
            className="absolute inset-y-0 w-px bg-foreground/30"
            style={{ left: `${_thresholdPercent(metric.warning_upper, metric)}%` }}
          />
        )}
      </div>

      {/* Tooltip / legend */}
      <div className="flex justify-between text-xs text-muted-foreground">
        {hoveredDive ? (
          <span className={ZONE_TEXT[hoveredDive.zone]}>
            Dive #{hoveredDive.dive_number}: {hoveredDive.value.toFixed(1)} {metric.unit}
          </span>
        ) : (
          <span>Min: {metric.min_val.toFixed(1)}</span>
        )}
        {!hoveredDive && <span>Max: {metric.max_val.toFixed(1)}</span>}
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
