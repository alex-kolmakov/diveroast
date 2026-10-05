/** One name per metric, shared by every view. */
export const METRICS = {
  depth: { name: "Max depth", qualifier: "" },
  sustained: { name: "Sustained ascent", qualifier: "30 s average" },
  surfacing: { name: "Surfacing speed", qualifier: "from the stop, last 5 m" },
  ndl: { name: "Min NDL", qualifier: "" },
  sac: { name: "SAC rate", qualifier: "" },
  temp: { name: "Temperature", qualifier: "" },
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
  if (l.includes("surfacing")) return "surfacing";
  if (l.includes("ascent")) return "sustained";
  if (l.includes("ndl")) return "ndl";
  if (l.includes("sac")) return "sac";
  if (l.includes("temp")) return "temp";
  if (l.includes("depth")) return "depth";
  return null;
}

export const fmtValue = (v: number | null | undefined, digits: number, unit: string) =>
  v == null ? NOT_RECORDED : `${v.toFixed(digits)} ${unit}`;
