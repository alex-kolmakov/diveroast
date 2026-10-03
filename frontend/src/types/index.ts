export interface Source {
  title: string;
  url: string;
  cited?: boolean; // the answer links to it (absent in older snapshots)
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
}

export interface UploadResponse {
  session_id: string;
  dive_count: number;
  dive_numbers: string[];
  message: string;
}

export interface ChatRequest {
  message: string;
  session_id: string;
}

// --- Dashboard types ---

export type AppPhase = "upload" | "analyzing" | "dashboard";

/** Per-dive metrics. `null` means the dive computer didn't record it. */
export interface DiveFeature {
  dive_number: string;
  avg_depth: number;
  max_depth: number;
  depth_variability: number | null;
  avg_temp: number | null;
  max_temp: number | null;
  min_temp: number | null;
  temp_gradient: number | null;
  temp_variability: number | null;
  avg_pressure: number | null;
  max_pressure: number | null;
  pressure_variability: number | null;
  min_ndl: number | null;
  entered_deco: boolean;
  sac_rate: number | null;
  max_ascend_speed: number;
  high_ascend_speed_count: number;
  max_shallow_ascend_speed?: number; // fastest surfacing through the last 8 m
  shallow_bolt_count?: number;
  dive_site_name: string;
  trip_name: string;
  latitude: number | null;
  longitude: number | null;
}

export interface DiveMetricPoint {
  dive_number: string;
  value: number;
  zone: "safe" | "warning" | "danger";
}

export interface MetricRange {
  label: string;
  unit: string;
  recorded: number; // dives with a value (0 in older shared snapshots)
  total: number;
  min_val: number | null;
  max_val: number | null;
  avg_val: number | null;
  worst_val: number | null;
  safe_upper: number;
  warning_upper: number;
  zone: "safe" | "warning" | "danger";
  per_dive: DiveMetricPoint[];
}

export interface ProblematicDive {
  dive_number: string;
  danger_score: number;
  features: DiveFeature;
  issues: string[];
  summary: string;
  pick_reason: string;
}

export interface AggregateStats {
  total_dives: number;
  avg_max_depth: number;
  avg_sac_rate: number | null;
  avg_max_ascend_speed: number;
  dives_with_fast_ascent?: number | null; // absent in older shared snapshots
  dives_with_shallow_bolt?: number | null;
  data_coverage?: Record<string, number>;
}

export interface DiverProfile {
  water_types: string[];
  regions: string[];
  experience_level: string;
  dive_sites: string[];
  temp_exposure: Record<string, number>;
}

export interface ProfilePoint {
  time_s: number;
  depth: number;
  temperature: number | null;
}

export interface AscentEvent {
  kind: "sustained" | "surfacing" | string;
  start_s: number;
  end_s: number;
  rate: number; // m/min
}

/** Detail for a one-dive upload (e.g. a Garmin FIT file). */
export interface SingleDive {
  dive_number: string;
  duration_min: number;
  profile: ProfilePoint[];
  ascent_events: AscentEvent[];
  issues: string[];
}

export interface DashboardData {
  session_id: string | null; // private; null in shared snapshots
  share_id?: string | null; // public, read-only
  aggregate_stats: AggregateStats;
  metrics: MetricRange[];
  all_dives: DiveFeature[];
  top_problematic_dives: ProblematicDive[];
  diver_profile: DiverProfile;
  roast_summary?: string | null;
  roast_prompt?: string | null;
  roast_sources?: Source[]; // absent in older shared snapshots
  mode?: "single" | "log"; // absent in older shared snapshots = "log"
  single_dive?: SingleDive | null;
}
