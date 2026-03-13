import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Anchor, ArrowUp, Wind, Thermometer, User, MapPin, Waves, Award } from "lucide-react";
import { DashboardHeader } from "@/components/DashboardHeader";
import { RangeGauge } from "@/components/RangeGauge";
import { AgentRoastSummary } from "@/components/AgentRoastSummary";
import { ProblematicDiveCard } from "@/components/ProblematicDiveCard";
import type { ChatMessage, DashboardData } from "@/types";

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
            <Card>
              <CardContent className="grid gap-4 pt-4 md:grid-cols-2 lg:grid-cols-4">
                <div className="flex items-start gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                    <Award className="h-5 w-5 text-primary" />
                  </div>
                  <div>
                    <div className="text-sm font-medium capitalize">{data.diver_profile.experience_level}</div>
                    <div className="text-xs text-muted-foreground">Experience Level</div>
                  </div>
                </div>
                {data.diver_profile.water_types.length > 0 && (
                  <div className="flex items-start gap-3">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                      <Waves className="h-5 w-5 text-primary" />
                    </div>
                    <div>
                      <div className="text-sm font-medium">{data.diver_profile.water_types.join(", ")}</div>
                      <div className="text-xs text-muted-foreground">Water Types</div>
                    </div>
                  </div>
                )}
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
          </div>
        )}

        {/* Top 3 problematic dives */}
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
