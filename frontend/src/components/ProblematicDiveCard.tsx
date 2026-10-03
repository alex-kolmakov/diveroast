import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { MapPin, Globe } from "lucide-react";
import { METRICS, fmtValue, type MetricKind } from "@/lib/metrics";
import type { ProblematicDive } from "@/types";

interface Props {
  dive: ProblematicDive;
  rank: number;
}

// The stat that got the dive picked, and the issue badge the title already covers.
const PICKED_FOR: Record<string, { kind: MetricKind; issue: string }> = {
  "Fastest ascent rate": { kind: "sustained", issue: "rapid ascent" },
  "Fastest bolt to the surface": { kind: "surfacing", issue: "bolted to surface" },
  "Closest to decompression limit": { kind: "ndl", issue: "low NDL" },
  "Highest air consumption": { kind: "sac", issue: "high air consumption" },
  "Deepest dive with issues": { kind: "depth", issue: "deep dive" },
};

const STAT_ORDER: MetricKind[] = ["depth", "sustained", "surfacing", "ndl", "sac"];

export function ProblematicDiveCard({ dive, rank }: Props) {
  const f = dive.features;
  const siteName = f.dive_site_name;
  const hasSite = siteName && siteName !== "N/A";
  const hasCoords = f.latitude != null && f.longitude != null;
  const mapsUrl = hasCoords ? `https://www.google.com/maps?q=${f.latitude},${f.longitude}` : null;
  const osmEmbedUrl = hasCoords
    ? `https://www.openstreetmap.org/export/embed.html?bbox=${Number(f.longitude) - 0.35},${Number(f.latitude) - 0.25},${Number(f.longitude) + 0.35},${Number(f.latitude) + 0.25}&layer=mapnik&marker=${f.latitude},${f.longitude}`
    : null;

  const stats: Record<string, string> = {
    depth: fmtValue(f.max_depth, 1, "m"),
    sustained: fmtValue(f.max_ascend_speed, 1, "m/min"),
    surfacing: fmtValue(f.max_shallow_ascend_speed, 1, "m/min"),
    ndl: f.entered_deco ? "entered deco" : fmtValue(f.min_ndl, 0, "min"),
    sac: fmtValue(f.sac_rate, 1, "L/min"),
  };
  const picked = PICKED_FOR[dive.pick_reason];

  // Older shared snapshots may still carry the retired rating-based issue
  const otherIssues = dive.issues.filter((i) => i !== "adverse conditions" && i !== picked?.issue);

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-danger/20 text-sm font-bold text-danger">
            {rank}
          </div>
          <div className="min-w-0 flex-1">
            <CardTitle className="text-base">{dive.pick_reason}</CardTitle>
            <p className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
              <span className="shrink-0 whitespace-nowrap">Dive #{dive.dive_number}</span>
              {hasSite && (
                <>
                  <span aria-hidden>·</span>
                  <MapPin className="h-3 w-3 shrink-0" aria-hidden />
                  <span className="min-w-0 truncate">{siteName}</span>
                </>
              )}
            </p>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Mini map or placeholder */}
        {osmEmbedUrl && mapsUrl ? (
          <a
            href={mapsUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="relative block h-[90px] overflow-hidden rounded-md border border-border"
          >
            <iframe
              src={osmEmbedUrl}
              className="pointer-events-none absolute left-1/2 top-1/2 h-[400px] w-[600px] origin-center -translate-x-1/2 -translate-y-[55%] scale-[0.5]"
              title={`Map of ${siteName || "dive site"}`}
              loading="lazy"
            />
          </a>
        ) : hasSite ? (
          <a
            href={`https://www.google.com/maps/search/${encodeURIComponent(siteName || "")}`}
            target="_blank"
            rel="noopener noreferrer"
            className="flex h-[90px] items-center justify-center gap-2 rounded-md border border-border bg-muted/30 text-xs text-muted-foreground hover:bg-muted/50 transition-colors"
          >
            <Globe className="h-4 w-4" />
            <span>Search "{siteName}" on Maps</span>
          </a>
        ) : null}

        {/* The stat that got it picked, then the rest on one line */}
        {picked && (
          <div>
            <div className="text-2xl font-bold text-danger">{stats[picked.kind]}</div>
            <div className="text-xs text-muted-foreground">{METRICS[picked.kind].name}</div>
          </div>
        )}
        <p className="text-xs leading-relaxed text-muted-foreground">
          {STAT_ORDER.filter((kind) => kind !== picked?.kind).map((kind, i) => (
            <span key={kind}>
              {i > 0 && " · "}
              {METRICS[kind].name} <span className="font-medium text-foreground">{stats[kind]}</span>
            </span>
          ))}
        </p>

        {otherIssues.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {otherIssues.map((issue) => (
              <Badge key={issue} variant="outline" className="text-xs text-muted-foreground">
                {issue}
              </Badge>
            ))}
          </div>
        )}

        {/* Agent-generated explanation */}
        {dive.summary && (
          <>
            <Separator />
            <p className="text-sm leading-relaxed">{dive.summary}</p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
