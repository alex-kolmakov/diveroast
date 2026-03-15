import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Anchor, ArrowUp, Wind, Thermometer, User, MapPin, Waves, Award } from "lucide-react";
import { DashboardHeader } from "@/components/DashboardHeader";
import { RangeGauge } from "@/components/RangeGauge";
import { AgentRoastSummary } from "@/components/AgentRoastSummary";
import { ProblematicDiveCard } from "@/components/ProblematicDiveCard";
import type { ChatMessage, DashboardData } from "@/types";

// Temperature labels that belong in the dedicated temp section, not Water Types
const TEMP_WATER_TYPES = new Set(["Cold water", "Temperate", "Tropical"]);

const COLD_COLOR = "#3b82f6";
const TEMP_COLOR = "#22c55e";
const TROP_COLOR = "#ef4444";

interface Props {
  data: DashboardData;
  messages?: ChatMessage[];
  isLoading?: boolean;
  onToggleChat?: () => void;
  shareUrl?: string;
  readOnly?: boolean;
}

const STAT_ICONS = [
  { icon: Anchor, label: "Avg Max Depth", suffix: "m" },
  { icon: Wind, label: "Avg SAC Rate", suffix: "" },
  { icon: ArrowUp, label: "Avg Max Ascent", suffix: "" },
  { icon: Thermometer, label: "Adverse Dives", suffix: "" },
];

export function Dashboard({ data, messages = [], isLoading = false, onToggleChat, shareUrl, readOnly }: Props) {
  const statValues = [
    data.aggregate_stats.avg_max_depth.toFixed(1),
    data.aggregate_stats.avg_sac_rate.toFixed(1),
    data.aggregate_stats.avg_max_ascend_speed.toFixed(1),
    String(data.aggregate_stats.dives_with_adverse_conditions),
  ];

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-6 p-6">
        {readOnly && (
          <div className="rounded-lg border border-primary/20 bg-primary/5 px-4 py-3 text-sm text-center text-muted-foreground">
            Viewing shared dive results —{" "}
            <a href="/" className="text-primary underline underline-offset-2 hover:no-underline">
              upload yours to get roasted
            </a>
          </div>
        )}

        <DashboardHeader
          stats={data.aggregate_stats}
          onToggleChat={onToggleChat}
          shareUrl={shareUrl}
          readOnly={readOnly}
        />

        <Separator />

        {/* Aggregate stats */}
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {STAT_ICONS.map(({ icon: Icon, label, suffix }, i) => (
            <Card key={label}>
              <CardContent className="flex items-center gap-3 pt-4">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                  <Icon className="h-5 w-5 text-primary" />
                </div>
                <div>
                  <div className="text-xl font-bold">{statValues[i]}{suffix}</div>
                  <div className="text-xs text-muted-foreground">{label}</div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        {/* Metric gauges grid */}
        <div>
          <h2 className="mb-4 text-lg font-semibold">Dive Metrics</h2>
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {data.metrics.map((metric) => (
              <Card key={metric.label}>
                <CardContent className="pt-4">
                  <RangeGauge metric={metric} />
                </CardContent>
              </Card>
            ))}
          </div>
        </div>

        {/* Agent roast summary — live sessions stream from messages; shared views use snapshot */}
        <AgentRoastSummary
          messages={messages}
          isLoading={isLoading}
          staticText={readOnly ? data.roast_summary : undefined}
        />

        {/* Diver Profile */}
        {data.diver_profile && (
          <div>
            <h2 className="mb-4 text-lg font-semibold">Diver Profile</h2>
            <div className="space-y-3">
              {/* Main profile row */}
              <Card>
                <CardContent className="grid gap-4 pt-4 md:grid-cols-2 lg:grid-cols-4">
                  {(() => {
                    const nonTempWaterTypes = data.diver_profile.water_types.filter(
                      (t) => !TEMP_WATER_TYPES.has(t)
                    );
                    return (
                      <>
                        <div className="flex items-start gap-3">
                          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                            <Award className="h-5 w-5 text-primary" />
                          </div>
                          <div>
                            <div className="text-sm font-medium capitalize">{data.diver_profile.experience_level}</div>
                            <div className="text-xs text-muted-foreground">Experience Level</div>
                          </div>
                        </div>
                        {nonTempWaterTypes.length > 0 && (
                          <div className="flex items-start gap-3">
                            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                              <Waves className="h-5 w-5 text-primary" />
                            </div>
                            <div>
                              <div className="text-sm font-medium">{nonTempWaterTypes.join(", ")}</div>
                              <div className="text-xs text-muted-foreground">Water Types</div>
                            </div>
                          </div>
                        )}
                      </>
                    );
                  })()}
                  {data.diver_profile.regions.length > 0 && (
                    <div className="flex items-start gap-3">
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                        <MapPin className="h-5 w-5 text-primary" />
                      </div>
                      <div>
                        <div className="text-sm font-medium">{data.diver_profile.regions.join(", ")}</div>
                        <div className="text-xs text-muted-foreground">Regions</div>
                      </div>
                    </div>
                  )}
                  {data.diver_profile.dive_sites.length > 0 && (
                    <div className="flex items-start gap-3">
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                        <User className="h-5 w-5 text-primary" />
                      </div>
                      <div>
                        <div className="text-sm font-medium">{data.diver_profile.dive_sites.length} sites</div>
                        <div className="text-xs text-muted-foreground">Dive Sites Visited</div>
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Temperature exposure row */}
              {data.diver_profile.temp_exposure &&
                Object.keys(data.diver_profile.temp_exposure).length > 0 && (
                  <TemperatureExposureCard exposure={data.diver_profile.temp_exposure} />
                )}
            </div>
          </div>
        )}

        {/* Top 3 worst dives */}
        {data.top_problematic_dives.length > 0 && (
          <div>
            <h2 className="mb-4 text-lg font-semibold">Top 3 Worst Dives</h2>
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {data.top_problematic_dives.map((dive, i) => (
                <ProblematicDiveCard key={dive.dive_number} dive={dive} rank={i + 1} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function TemperatureExposureCard({ exposure }: { exposure: Record<string, number> }) {
  const cold = exposure["Cold (<15°C)"] ?? 0;
  const temperate = exposure["Temperate (15-24°C)"] ?? 0;
  const tropical = exposure["Tropical (>24°C)"] ?? 0;
  const total = cold + temperate + tropical || 1;
  const coldPct = (cold / total) * 100;
  const tempPct = (temperate / total) * 100;
  const tropPct = (tropical / total) * 100;
  const title = _tempTitle(cold, temperate, tropical, total);

  return (
    <Card>
      <CardContent className="pt-4">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
            <Thermometer className="h-5 w-5 text-primary" />
          </div>
          <div className="flex-1">
            <div className="flex items-baseline justify-between mb-2">
              <div className="text-sm font-medium">Temperature Exposure</div>
              <div className="text-xs font-semibold" style={{ color: _titleColor(cold, temperate, tropical) }}>
                {title}
              </div>
            </div>
            <div className="h-3 w-full rounded-full overflow-hidden">
              <div className="flex h-full w-full">
                {coldPct > 0 && <div className="h-full" style={{ width: `${coldPct}%`, background: COLD_COLOR }} />}
                {tempPct > 0 && <div className="h-full" style={{ width: `${tempPct}%`, background: TEMP_COLOR }} />}
                {tropPct > 0 && <div className="h-full" style={{ width: `${tropPct}%`, background: TROP_COLOR }} />}
              </div>
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
              {cold > 0 && (
                <span className="text-xs text-muted-foreground">
                  <span className="font-semibold" style={{ color: COLD_COLOR }}>{cold}</span> Cold (&lt;15°C) · {coldPct.toFixed(0)}%
                </span>
              )}
              {temperate > 0 && (
                <span className="text-xs text-muted-foreground">
                  <span className="font-semibold" style={{ color: TEMP_COLOR }}>{temperate}</span> Temperate (15–24°C) · {tempPct.toFixed(0)}%
                </span>
              )}
              {tropical > 0 && (
                <span className="text-xs text-muted-foreground">
                  <span className="font-semibold" style={{ color: TROP_COLOR }}>{tropical}</span> Tropical (&gt;24°C) · {tropPct.toFixed(0)}%
                </span>
              )}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function _tempTitle(cold: number, temperate: number, tropical: number, total: number): string {
  const cPct = cold / total;
  const tPct = temperate / total;
  const trPct = tropical / total;

  if (trPct >= 0.85) return "Coral Chaser";
  if (trPct >= 0.65) return "Warmwater Regular";
  if (cPct >= 0.85) return "Ice Diver";
  if (cPct >= 0.65) return "Cold Water Devotee";
  if (tPct >= 0.65) return "Temperate Explorer";
  if (cPct >= 0.4 && trPct <= 0.2) return "Cold Water Diver";
  if (trPct >= 0.4 && cPct <= 0.2) return "Sun Seeker";
  if (Math.abs(cPct - trPct) < 0.15 && tPct < 0.3) return "Extreme Contrarian";
  return "All-Around Diver";
}

function _titleColor(cold: number, temperate: number, tropical: number): string {
  const total = cold + temperate + tropical || 1;
  if (tropical / total >= 0.65) return TROP_COLOR;
  if (cold / total >= 0.65) return COLD_COLOR;
  return TEMP_COLOR;
}
