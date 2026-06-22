export interface ModelUsageItem {
  model: string;
  call_count: number;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  total_cost: number;
  avg_latency_ms: number | null;
}

export interface DailyUsageItem {
  date: string;
  trace_count: number;
  observation_count: number;
  total_tokens: number;
  total_cost: number;
}

export interface MetricsResponse {
  total_traces: number;
  total_observations: number;
  total_sessions: number;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  total_cost_usd: number;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
  p95_cost_per_trace: number | null;
  p99_cost_per_trace: number | null;
  by_model: ModelUsageItem[];
  by_date: DailyUsageItem[];
  top_agents: any[];
  truncated: boolean;
  fetched_trace_count: number;
  scope_warning: boolean;
  scope_warning_message: string | null;
}

export interface TraceListItem {
  id: string;
  name: string | null;
  session_id: string | null;
  timestamp: string | null;
  total_tokens: number;
  total_cost: number;
  latency_ms: number | null;
  models_used: string[];
  observation_count: number;
  level: string | null;
  langfuse_console_url: string | null;
}

export interface TracesListResponse {
  traces: TraceListItem[];
  total: number;
  page: number;
  limit: number;
  langfuse_base_console_url: string | null;
  scope_warning: boolean;
  scope_warning_message: string | null;
}

export interface ObservationResponse {
  id: string;
  trace_id: string;
  name: string | null;
  type: string | null;
  model: string | null;
  start_time: string | null;
  end_time: string | null;
  completion_start_time: string | null;
  latency_ms: number | null;
  time_to_first_token_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  input_cost: number;
  output_cost: number;
  total_cost: number;
  input: any | null;
  output: any | null;
  metadata: Record<string, any> | null;
  level: string | null;
  status_message: string | null;
  parent_observation_id: string | null;
}

export interface ScoreItem {
  id: string;
  name: string;
  value: number;
  source: string | null;
  comment: string | null;
  created_at: string | null;
}

export interface TraceDetailResponse {
  id: string;
  name: string | null;
  user_id: string | null;
  session_id: string | null;
  timestamp: string | null;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  total_cost: number;
  latency_ms: number | null;
  models_used: string[];
  observations: ObservationResponse[];
  scores: ScoreItem[];
  input: any | null;
  output: any | null;
  metadata: Record<string, any> | null;
  tags: string[];
  level: string | null;
  status: string | null;
  langfuse_console_url: string | null;
  scope_warning: boolean;
  scope_warning_message: string | null;
}

export interface AgentListItem {
  agent_id: string;
  agent_name: string | null;
  project_id: string | null;
  project_name: string | null;
  trace_count: number;
  session_count: number;
  total_tokens: number;
  total_cost: number;
  avg_latency_ms: number | null;
  models_used: string[];
  last_activity: string | null;
  error_count: number;
}

export interface AgentListResponse {
  agents: AgentListItem[];
  total: number;
  truncated: boolean;
  fetched_trace_count: number;
  scope_warning: boolean;
  scope_warning_message: string | null;
}

export interface ProjectListItem {
  project_id: string;
  project_name: string | null;
  agent_count: number;
  trace_count: number;
  session_count: number;
  total_tokens: number;
  total_cost: number;
  last_activity: string | null;
}

export interface ProjectListResponse {
  projects: ProjectListItem[];
  total: number;
  truncated: boolean;
  fetched_trace_count: number;
  scope_warning: boolean;
  scope_warning_message: string | null;
}

export interface UserUsageItem {
  user_id: string;
  username: string;
  display_name: string | null;
  email: string | null;
  role: string;
  trace_count: number;
  total_tokens: number;
  total_cost: number;
  last_activity: string | null;
}

export interface UserUsageListResponse {
  users: UserUsageItem[];
  total: number;
  scope_warning: boolean;
  scope_warning_message: string | null;
}

