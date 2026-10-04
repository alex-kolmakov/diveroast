import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Award, MapPin, Waves, Anchor } from "lucide-react";
import { DashboardHeader } from "@/components/DashboardHeader";
import { RangeGauge, TemperatureGauge } from "@/components/RangeGauge";
import { AgentRoastSummary } from "@/components/AgentRoastSummary";
import { ProblematicDiveCard } from "@/components/ProblematicDiveCard";
import { DonationNotice } from "@/components/DonationNotice";
import { metricKind } from "@/lib/metrics";
import { lazy, Suspense } from "react";

// Loaded on demand: it pulls in the charting library, which log mode never needs.
const SingleDiveView = lazy(() =>
  import("@/components/SingleDiveView").then((m) => ({ default: m.SingleDiveView }))
);
import type { ChatMessage, DashboardData, DiverProfile, DonationReceipt } from "@/types";

// Temperature labels that belong in the temperature gauge, not Water Types
const TEMP_WATER_TYPES = new Set(["Cold water", "Temperate", "Tropical"]);

interface Props {
  data: DashboardData;
  messages?: ChatMessage[];
  isLoading?: boolean;
  onToggleChat?: () => void;
  shareUrl?: string;
  readOnly?: boolean;
  donation?: DonationReceipt | null;
}

export function Dashboard({ data, messages = [], isLoading = false, onToggleChat, shareUrl, readOnly, donation }: Props) {
  const single = data.mode === "single" && data.single_dive && data.all_dives.length === 1;
  const total = data.aggregate_stats.total_dives;
  const site = single ? data.all_dives[0].dive_site_name : null;
  const subject = single
    ? `Dive #${data.single_dive!.dive_number}${site && site !== "N/A" ? ` · ${site}` : ""}`
    : `${total} ${total === 1 ? "dive" : "dives"}`;
  const worst = data.top_problematic_dives;

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

        {!readOnly && donation && <DonationNotice donation={donation} />}

        <div className="space-y-3">
          <DashboardHeader subject={subject} onToggleChat={onToggleChat} shareUrl={shareUrl} readOnly={readOnly} />
          {!single && data.diver_profile && <ProfileStrip profile={data.diver_profile} />}
        </div>

        <Separator />

        {/* The roast — live sessions stream from messages; shared views use the snapshot */}
        <AgentRoastSummary
          messages={messages}
          isLoading={isLoading}
          staticText={readOnly ? data.roast_summary : undefined}
          staticSources={data.roast_sources}
        />

        {single ? (
          <Suspense fallback={<div className="h-64 animate-pulse rounded-xl bg-muted/30" />}>
            <SingleDiveView dive={data.single_dive!} features={data.all_dives[0]} metrics={data.metrics} />
          </Suspense>
        ) : (
          <>
            <div>
              <h2 className="mb-4 text-lg font-semibold">Dive Metrics</h2>
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {data.metrics.map((metric) => (
                  <Card key={metric.label}>
                    <CardContent className="pt-4">
                      {metricKind(metric.label) === "temp" ? (
                        <TemperatureGauge metric={metric} exposure={data.diver_profile?.temp_exposure} />
                      ) : (
                        <RangeGauge metric={metric} />
                      )}
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>

            {worst.length > 0 && (
              <div>
                <h2 className="mb-4 text-lg font-semibold">
                  {worst.length === 1 ? "Worst Dive" : `Top ${worst.length} Worst Dives`}
                </h2>
                <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                  {worst.map((dive, i) => (
                    <ProblematicDiveCard key={dive.dive_number} dive={dive} rank={i + 1} />
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** Who this diver is, on one line under the header. */
function ProfileStrip({ profile }: { profile: DiverProfile }) {
  const waterTypes = profile.water_types.filter((t) => !TEMP_WATER_TYPES.has(t));
  const sites = profile.dive_sites.length;
  const items = [
    { Icon: Award, label: "Experience level", value: profile.experience_level, capitalize: true },
    waterTypes.length > 0 && { Icon: Waves, label: "Water types", value: waterTypes.join(", ") },
    profile.regions.length > 0 && { Icon: MapPin, label: "Regions", value: profile.regions.join(", ") },
    sites > 0 && {
      Icon: Anchor,
      label: "Dive sites visited",
      value: `${sites} ${sites === 1 ? "site" : "sites"}`,
      title: profile.dive_sites.join(", "),
    },
  ].filter((item) => !!item);

  return (
    <ul className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-muted-foreground">
      {items.map(({ Icon, label, value, capitalize, title }) => (
        <li key={label} className="flex items-center gap-1.5" title={title ?? label}>
          <Icon className="h-4 w-4 text-primary" aria-hidden />
          <span className="sr-only">{label}:</span>
          <span className={capitalize ? "capitalize text-foreground" : "text-foreground"}>{value}</span>
        </li>
      ))}
    </ul>
  );
}
