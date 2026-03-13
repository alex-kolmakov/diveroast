export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
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

export interface DiveFeature {
  dive_number: string;
  avg_depth: number;
  max_depth: number;
  depth_variability: number;
  avg_temp: number;
  max_temp: number;
  temp_variability: number;
  avg_pressure: number;
  max_pressure: number;
  pressure_variability: number;
  min_ndl: number;
  sac_rate: number;
  rating: number;
  max_ascend_speed: number;
  high_ascend_speed_count: number;
  adverse_conditions: number;
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
  min_val: number;
  max_val: number;
  avg_val: number;
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
  avg_sac_rate: number;
  avg_max_ascend_speed: number;
  dives_with_adverse_conditions: number;
}

export interface DiverProfile {
  water_types: string[];
  regions: string[];
  experience_level: string;
  dive_sites: string[];
}

export interface DashboardData {
  session_id: string;
  aggregate_stats: AggregateStats;
  metrics: MetricRange[];
  all_dives: DiveFeature[];
  top_problematic_dives: ProblematicDive[];
  diver_profile: DiverProfile;
  roast_summary?: string | null;
}
