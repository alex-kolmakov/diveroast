import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AlertTriangle, ArrowUp, ArrowUpToLine } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { METRICS, NOT_RECORDED, ZONE_TEXT, fmtValue, metricKind, type MetricKind, type Zone } from "@/lib/metrics";
import type { AscentEvent, DiveFeature, MetricRange, SingleDive } from "@/types";

interface Props {
  dive: SingleDive;
  features: DiveFeature;
  metrics: MetricRange[];
}

const EVENT_STYLE = {
  sustained: { color: "var(--warning)", Icon: ArrowUp },
  surfacing: { color: "var(--danger)", Icon: ArrowUpToLine },
} as const;

type EventKind = keyof typeof EVENT_STYLE;

const eventKind = (e: AscentEvent): EventKind => (e.kind in EVENT_STYLE ? (e.kind as EventKind) : "sustained");

const mmss = (seconds: number) => {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

function Stat({ label, qualifier, value, zone }: { label: string; qualifier?: string; value: string; zone?: Zone }) {
  const recorded = value !== NOT_RECORDED;
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div
        className={
          recorded
            ? `text-lg font-bold ${zone && zone !== "safe" ? ZONE_TEXT[zone] : ""}`
            : "text-sm leading-7 text-muted-foreground"
        }
      >
        {value}
      </div>
      {qualifier && <div className="text-[11px] text-muted-foreground/70">{qualifier}</div>}
    </div>
  );
}

function ProfileTooltip({ active, payload }: { active?: boolean; payload?: { payload: { t: number; depth: number; temperature: number | null } }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-md border border-border bg-card px-3 py-2 text-xs shadow-md">
      <div className="font-medium text-foreground">{p.depth.toFixed(1)} m</div>
      <div className="text-muted-foreground">
        {mmss(p.t * 60)} min{p.temperature != null ? ` · ${p.temperature.toFixed(1)} °C` : ""}
      </div>
    </div>
  );
}

/** One problem: its headline, the explanation, and when it happened on the profile. */
function Problem({ kind, title, body, events }: { kind?: EventKind; title: string; body?: string; events: AscentEvent[] }) {
  const color = kind ? EVENT_STYLE[kind].color : "var(--danger)";
  const Icon = kind ? EVENT_STYLE[kind].Icon : AlertTriangle;
  return (
    <li className="flex gap-3 text-sm">
      <span
        className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full"
        style={{ background: `color-mix(in oklch, ${color} 20%, transparent)` }}
      >
        <Icon className="h-3.5 w-3.5" style={{ color }} aria-hidden />
      </span>
      <div className="min-w-0 space-y-1">
        <div className="font-medium text-foreground">{title}</div>
        {body && <div className="text-muted-foreground">{body}</div>}
        {events.length > 0 && (
          <div className="flex flex-wrap gap-1.5 text-xs">
            {events.map((e, i) => (
              <span key={i} className="rounded-full border border-border px-2 py-0.5 text-muted-foreground">
                {e.rate.toFixed(1)} m/min · {mmss(e.start_s)}–{mmss(e.end_s)}
              </span>
            ))}
          </div>
        )}
      </div>
    </li>
  );
}

/** Which ascent events an issue line ("HIGH ASCENT RATE: ...") is describing. */
function issueKind(title: string): EventKind | undefined {
  if (/bolted/i.test(title)) return "surfacing";
  if (/ascent/i.test(title)) return "sustained";
  return undefined;
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const sentenceCase = (s: string) => capitalize(s.toLowerCase());

/** One dive in detail: its numbers, its profile, and where it went wrong. */
export function SingleDiveView({ dive, features, metrics }: Props) {
  const data = dive.profile.map((p) => ({ t: p.time_s / 60, depth: p.depth, temperature: p.temperature }));
  const maxDepth = Math.max(...dive.profile.map((p) => p.depth), 1);
  const durationMin = data.length ? data[data.length - 1].t - data[0].t : 0;
  // A bolt can last seconds; keep its band at least ~1.5% of the axis wide so
  // it stays visible, centred on the actual event.
  const band = (e: AscentEvent) => {
    const start = e.start_s / 60;
    const end = e.end_s / 60;
    const pad = Math.max(0, durationMin * 0.015 - (end - start)) / 2;
    return { x1: start - pad, x2: end + pad };
  };

  const zone = (kind: MetricKind): Zone | undefined =>
    metrics.find((m) => metricKind(m.label) === kind)?.per_dive[0]?.zone;

  // Issues carry the explanation, ascent events the timing: show them as one list.
  const problems = dive.issues.map((issue) => {
    const [head, ...rest] = issue.split(": ");
    const kind = issueKind(head);
    return {
      kind,
      title: sentenceCase(head),
      body: capitalize(rest.join(": ")),
      events: kind ? dive.ascent_events.filter((e) => eventKind(e) === kind) : [],
    };
  });
  const explained = new Set(problems.map((p) => p.kind));
  (["sustained", "surfacing"] as const)
    .filter((kind) => !explained.has(kind))
    .forEach((kind) => {
      const events = dive.ascent_events.filter((e) => eventKind(e) === kind);
      if (events.length > 0) {
        problems.push({
          kind,
          title: kind === "sustained" ? "Sustained fast ascent" : "Bolted to the surface",
          body: "",
          events,
        });
      }
    });

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="grid grid-cols-2 gap-x-4 gap-y-5 pt-5 sm:grid-cols-4 lg:grid-cols-7">
          <Stat label={METRICS.depth.name} value={fmtValue(features.max_depth, 1, "m")} zone={zone("depth")} />
          <Stat label="Duration" value={`${dive.duration_min.toFixed(0)} min`} />
          <Stat
            label={METRICS.sustained.name}
            qualifier={METRICS.sustained.qualifier}
            value={fmtValue(features.max_ascend_speed, 1, "m/min")}
            zone={zone("sustained")}
          />
          <Stat
            label={METRICS.surfacing.name}
            qualifier={METRICS.surfacing.qualifier}
            value={fmtValue(features.max_shallow_ascend_speed, 1, "m/min")}
            zone={zone("surfacing")}
          />
          <Stat
            label={METRICS.ndl.name}
            value={features.entered_deco ? "entered deco" : fmtValue(features.min_ndl, 0, "min")}
            zone={features.entered_deco ? "danger" : zone("ndl")}
          />
          <Stat label={METRICS.sac.name} value={fmtValue(features.sac_rate, 1, "L/min")} zone={zone("sac")} />
          <Stat label={METRICS.temp.name} qualifier="average" value={fmtValue(features.avg_temp, 1, "°C")} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Dive profile</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-64 w-full" role="img" aria-label={`Depth over time, maximum ${features.max_depth.toFixed(1)} metres`}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--border)" strokeOpacity={0.5} vertical={false} />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  tickFormatter={(t: number) => `${Math.round(t)}′`}
                  stroke="var(--muted-foreground)"
                  tick={{ fontSize: 11 }}
                  tickLine={false}
                />
                <YAxis
                  dataKey="depth"
                  reversed
                  domain={[0, Math.ceil(maxDepth * 1.1)]}
                  tickFormatter={(d: number) => `${d} m`}
                  stroke="var(--muted-foreground)"
                  tick={{ fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                  width={44}
                />
                {dive.ascent_events.map((e, i) => (
                  <ReferenceArea
                    key={i}
                    {...band(e)}
                    fill={EVENT_STYLE[eventKind(e)].color}
                    fillOpacity={0.35}
                    strokeOpacity={0}
                  />
                ))}
                <Tooltip content={<ProfileTooltip />} cursor={{ stroke: "var(--muted-foreground)", strokeWidth: 1 }} />
                <Area
                  type="monotone"
                  dataKey="depth"
                  stroke="var(--primary)"
                  strokeWidth={2}
                  fill="var(--primary)"
                  fillOpacity={0.12}
                  baseValue={0}
                  isAnimationActive={false}
                  activeDot={{ r: 4, stroke: "var(--card)", strokeWidth: 2 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {problems.length > 0 ? (
            <div className="mt-5 border-t border-border pt-4">
              <h3 className="mb-3 text-sm font-semibold">What went wrong</h3>
              <ul className="space-y-3">
                {problems.map((p) => (
                  <Problem key={p.title} {...p} />
                ))}
              </ul>
            </div>
          ) : (
            <p className="mt-4 text-sm text-muted-foreground">
              Nothing flagged: sustained ascent and every surfacing stayed at or under 10 m/min, and no other limit was crossed.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
