from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    session_id: str


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class UploadResponse(BaseModel):
    session_id: str
    dive_count: int
    dive_numbers: list[str]
    message: str


# --- Dashboard models ---


class DiveFeature(BaseModel):
    """Per-dive metrics. None means the dive computer didn't record it.

    Only fields any dive computer can produce: no diver-entered star rating.
    """

    dive_number: str
    avg_depth: float
    max_depth: float
    depth_variability: float | None
    avg_temp: float | None
    max_temp: float | None
    min_temp: float | None
    temp_gradient: float | None
    temp_variability: float | None
    avg_pressure: float | None
    max_pressure: float | None
    pressure_variability: float | None
    min_ndl: float | None
    entered_deco: bool = False  # default keeps pre-P1 snapshots loadable
    sac_rate: float | None
    max_ascend_speed: float
    high_ascend_speed_count: float
    # Surfacing tier; defaults keep pre-P1 snapshots loadable
    max_shallow_ascend_speed: float = 0.0
    shallow_bolt_count: int = 0
    dive_site_name: str
    trip_name: str
    latitude: float | None
    longitude: float | None


class DiveMetricPoint(BaseModel):
    dive_number: str
    value: float
    zone: str  # "safe", "warning", "danger"


class MetricRange(BaseModel):
    """Gauge data over the dives where this metric was recorded."""

    label: str
    unit: str
    recorded: int = 0  # dives with a value (0 in pre-P1 snapshots)
    total: int = 0  # dives in the log (0 in pre-P1 snapshots)
    min_val: float | None
    max_val: float | None
    avg_val: float | None
    worst_val: float | None
    safe_upper: float
    warning_upper: float
    zone: str  # "safe", "warning", "danger"
    per_dive: list[DiveMetricPoint]


class ProblematicDive(BaseModel):
    dive_number: str
    danger_score: float
    features: DiveFeature
    issues: list[str]
    summary: str
    pick_reason: str


class AggregateStats(BaseModel):
    total_dives: int
    avg_max_depth: float
    avg_sac_rate: float | None
    avg_max_ascend_speed: float
    dives_with_fast_ascent: int | None = None  # None in pre-P1 snapshots
    dives_with_shallow_bolt: int | None = None  # None in pre-P1 snapshots
    data_coverage: dict[str, int] = {}  # dives with each metric recorded


class DiverProfile(BaseModel):
    water_types: list[str]
    regions: list[str]
    experience_level: str
    dive_sites: list[str]
    temp_exposure: dict[
        str, int
    ]  # e.g. {"Tropical (>24°C)": 45, "Temperate (15-24°C)": 12}


class ProfilePoint(BaseModel):
    time_s: float
    depth: float
    temperature: float | None = None


class AscentEvent(BaseModel):
    """A fast ascent on the profile: sustained (30 s) or a bolt to the surface."""

    kind: str  # "sustained" | "surfacing"
    start_s: float
    end_s: float
    rate: float  # m/min


class SingleDive(BaseModel):
    """Detail for single-dive mode (a log with exactly one dive)."""

    dive_number: str
    duration_min: float
    profile: list[ProfilePoint]  # downsampled for plotting
    ascent_events: list[AscentEvent]
    issues: list[str]  # the same issue sentences the agent sees


class Source(BaseModel):
    """A DAN article retrieved for an answer; ``cited`` if the answer links it."""

    title: str
    url: str
    cited: bool = False


class DashboardResponse(BaseModel):
    # Private session ID: present for the owner, stripped from shared snapshots.
    session_id: str | None = None
    # Public, read-only ID for /api/shared/{share_id}.
    share_id: str | None = None
    aggregate_stats: AggregateStats
    metrics: list[MetricRange]
    all_dives: list[DiveFeature]
    top_problematic_dives: list[ProblematicDive]
    diver_profile: DiverProfile
    roast_summary: str | None = None
    roast_prompt: str | None = None  # which system prompt wrote the roast
    roast_sources: list[Source] = []  # DAN articles retrieved for the roast
    # "single": one dive (e.g. a Garmin FIT file) -> dive detail, not a log
    # overview. Defaults keep pre-P1 snapshots loadable.
    mode: str = "log"
    single_dive: SingleDive | None = None
