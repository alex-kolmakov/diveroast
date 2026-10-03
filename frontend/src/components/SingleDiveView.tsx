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
import { AlertTriangle, ArrowUp, ArrowUpToLine, Anchor, Clock, Gauge, Thermometer, Wind } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AscentEvent, DiveFeature, SingleDive } from "@/types";

interface Props {
  dive: SingleDive;
  features: DiveFeature;
}

const EVENT_STYLE = {
  sustained: { color: "var(--warning)", label: "Sustained fast ascent (30 s)", Icon: ArrowUp },
  surfacing: { color: "var(--danger)", label: "Bolted to the surface (last 8 m)", Icon: ArrowUpToLine },
} as const;

const mmss = (seconds: number) => {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

const fmt = (v: number | null | undefined, digits: number, unit: string) =>
  v == null ? "not recorded" : `${v.toFixed(digits)} ${unit}`;

function Stat({ icon: Icon, label, value }: { icon: typeof Anchor; label: string; value: string }) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 pt-4">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
          <Icon className="h-5 w-5 text-primary" />
        </div>
        <div>
          <div className="text-lg font-bold">{value}</div>
          <div className="text-xs text-muted-foreground">{label}</div>
        </div>
      </CardContent>
    </Card>
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

function EventRow({ event }: { event: AscentEvent }) {
  const style = EVENT_STYLE[event.kind as keyof typeof EVENT_STYLE] ?? EVENT_STYLE.sustained;
  const { Icon } = style;
  return (
    <li className="flex items-center gap-3 text-sm">
      <span
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full"
        style={{ background: `color-mix(in oklch, ${style.color} 20%, transparent)` }}
      >
        <Icon className="h-3.5 w-3.5" style={{ color: style.color }} aria-hidden />
      </span>
      <span className="text-foreground">{style.label}</span>
      <span className="text-muted-foreground">
        {event.rate.toFixed(1)} m/min · {mmss(event.start_s)}–{mmss(event.end_s)}
      </span>
    </li>
  );
}

/** One dive in detail: its profile, where it went wrong, and its numbers. */
export function SingleDiveView({ dive, features }: Props) {
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
  const ndl = features.entered_deco ? "entered deco" : fmt(features.min_ndl, 0, "min");

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat icon={Anchor} label="Max depth" value={`${features.max_depth.toFixed(1)} m`} />
        <Stat icon={Clock} label="Duration" value={`${dive.duration_min.toFixed(0)} min`} />
        <Stat icon={ArrowUp} label="Max sustained ascent (30 s)" value={`${features.max_ascend_speed.toFixed(1)} m/min`} />
        <Stat
          icon={ArrowUpToLine}
          label="Fastest surfacing (last 8 m)"
          value={fmt(features.max_shallow_ascend_speed, 1, "m/min")}
        />
        <Stat icon={Gauge} label="Min NDL" value={ndl} />
        <Stat icon={Wind} label="SAC rate" value={fmt(features.sac_rate, 1, "L/min")} />
        <Stat icon={Thermometer} label="Avg temperature" value={fmt(features.avg_temp, 1, "°C")} />
      </div>

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
                    fill={EVENT_STYLE[e.kind as keyof typeof EVENT_STYLE]?.color ?? "var(--warning)"}
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

          {dive.ascent_events.length > 0 ? (
            <ul className="mt-4 space-y-2">
              {dive.ascent_events.map((e, i) => (
                <EventRow key={i} event={e} />
              ))}
            </ul>
          ) : (
            <p className="mt-4 text-sm text-muted-foreground">
              No fast ascents: sustained rate and every surfacing stayed at or under 10 m/min.
            </p>
          )}
        </CardContent>
      </Card>

      {dive.issues.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <AlertTriangle className="h-4 w-4 text-danger" aria-hidden />
              Issues on this dive
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="list-disc space-y-1.5 pl-5 text-sm text-foreground">
              {dive.issues.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
