import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { AlertTriangle, MapPin, Globe } from "lucide-react";
import type { ProblematicDive } from "@/types";

interface Props {
  dive: ProblematicDive;
  rank: number;
}

export function ProblematicDiveCard({ dive, rank }: Props) {
  const siteName = dive.features.dive_site_name;
  const hasSite = siteName && siteName !== "N/A";
  const hasCoords = dive.features.latitude != null && dive.features.longitude != null;
  const mapsUrl = hasCoords
    ? `https://www.google.com/maps?q=${dive.features.latitude},${dive.features.longitude}`
    : null;
  const osmEmbedUrl = hasCoords
    ? `https://www.openstreetmap.org/export/embed.html?bbox=${Number(dive.features.longitude) - 0.35},${Number(dive.features.latitude) - 0.25},${Number(dive.features.longitude) + 0.35},${Number(dive.features.latitude) + 0.25}&layer=mapnik&marker=${dive.features.latitude},${dive.features.longitude}`
    : null;

  // Older shared snapshots may still carry the retired rating-based issue
  const visibleIssues = dive.issues.filter((i) => i !== "adverse conditions");

  return (
    <Card className="border-danger/30">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-danger/20 text-sm font-bold text-danger">
            {rank}
          </div>
          <div className="flex-1">
            <CardTitle className="text-base">
              Dive #{dive.dive_number}
            </CardTitle>
            {hasSite && (
              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                <MapPin className="h-3 w-3" />
                {siteName}
              </p>
            )}
          </div>
          <AlertTriangle className="h-5 w-5 text-danger" />
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

        {/* Key stats */}
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div>
            <span className="text-muted-foreground">Max depth: </span>
            <span className="font-medium">{dive.features.max_depth.toFixed(1)}m</span>
          </div>
          <div>
            <span className="text-muted-foreground">Sustained ascent: </span>
            <span className="font-medium">
              {dive.features.max_ascend_speed.toFixed(1)} m/min
            </span>
          </div>
          <div>
            <span className="text-muted-foreground">Surfacing: </span>
            <span className="font-medium">
              {dive.features.max_shallow_ascend_speed != null
                ? `${dive.features.max_shallow_ascend_speed.toFixed(1)} m/min`
                : "–"}
            </span>
          </div>
          <div>
            <span className="text-muted-foreground">Min NDL: </span>
            <span className="font-medium">
              {dive.features.entered_deco
                ? "entered deco"
                : dive.features.min_ndl != null
                  ? `${dive.features.min_ndl.toFixed(0)} min`
                  : "not recorded"}
            </span>
          </div>
          <div>
            <span className="text-muted-foreground">SAC rate: </span>
            <span className="font-medium">
              {dive.features.sac_rate != null
                ? `${dive.features.sac_rate.toFixed(1)} L/min`
                : "not recorded"}
            </span>
          </div>
        </div>

        {/* Issue badges — pick reason first, then others */}
        <div className="flex flex-wrap gap-1.5">
          <Badge variant="destructive" className="text-xs font-semibold">
            {dive.pick_reason}
          </Badge>
          {visibleIssues.map((issue) => (
            <Badge key={issue} variant="outline" className="text-xs text-muted-foreground">
              {issue}
            </Badge>
          ))}
        </div>

        {/* Agent-generated explanation */}
        {dive.summary && (
          <>
            <Separator />
            <p className="text-xs leading-relaxed text-muted-foreground">
              {dive.summary}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
