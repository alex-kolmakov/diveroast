/** One name per metric, shared by every view. */
export const METRICS = {
  depth: { name: "Max depth", qualifier: "" },
  ascent: { name: "Fastest ascent", qualifier: "30 s sustained, or the last 5 m to the surface" },
  // The two ascent tiers, as older shared snapshots still carry them.
  sustained: { name: "Sustained ascent", qualifier: "30 s average" },
  surfacing: { name: "Surfacing speed", qualifier: "from the stop, last 5 m" },
  ndl: { name: "Min NDL", qualifier: "" },
  sac: { name: "SAC rate", qualifier: "" },
  temp: { name: "Temperature", qualifier: "" },
  reserve: { name: "Gas left on exit", qualifier: "last tank reading underwater" },
  stop: { name: "Safety stop", qualifier: "between 3 and 6 m" },
  redescents: { name: "Re-descents", qualifier: "of 5 m or more" },
  surfacings: { name: "Mid-dive surfacings", qualifier: "" },
  missedStop: { name: "Above a required stop", qualifier: "" },
  po2: { name: "Oxygen pressure", qualifier: "at the deepest point, for the gas breathed" },
  density: { name: "Gas density", qualifier: "at the deepest point" },
  backDown: { name: "Re-descents", qualifier: "metres back down after coming up, on dives deeper than 10 m" },
  stops: { name: "Stops", qualifier: "safety and deco stops" },
} as const;

export type MetricKind = keyof typeof METRICS;
export type Zone = "safe" | "warning" | "danger";

export const NOT_RECORDED = "not recorded";

export const ZONE_TEXT: Record<Zone, string> = {
  safe: "text-safe",
  warning: "text-warning",
  danger: "text-danger",
};

/** Which metric a backend gauge label refers to (labels differ in older snapshots). */
export function metricKind(label: string): MetricKind | null {
  const l = label.toLowerCase();
  if (l.includes("oxygen")) return "po2";
  if (l.includes("gas left")) return "reserve";
  if (l.includes("re-descent")) return "backDown";
  if (l.includes("surfacing")) return "surfacing";
  if (l.includes("sustained")) return "sustained";
  if (l.includes("ascent")) return "ascent";
  if (l.includes("ndl")) return "ndl";
  if (l.includes("sac")) return "sac";
  if (l.includes("temp")) return "temp";
  if (l.includes("depth")) return "depth";
  return null;
}

/** A dive's one ascent number: the faster of the sustained rate and the surfacing. */
export function fastestAscent(f: {
  max_ascend_speed: number | null;
  max_shallow_ascend_speed?: number | null;
}): number | null {
  const rates = [f.max_ascend_speed, f.max_shallow_ascend_speed].filter((r): r is number => r != null);
  return rates.length > 0 ? Math.max(...rates) : null;
}

export const fmtValue = (v: number | null | undefined, digits: number, unit: string) =>
  v == null ? NOT_RECORDED : `${v.toFixed(digits)} ${unit}`;
