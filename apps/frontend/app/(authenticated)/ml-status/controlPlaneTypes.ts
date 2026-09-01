export type ControlStatus = 'passed' | 'degraded' | 'failed' | 'unavailable';

export type ControlException = {
  code: string;
  severity: 'warning' | 'critical';
  summary: string;
  count?: number;
};

export type ControlSnapshot = {
  generated_at: string;
  status: ControlStatus;
  publication_eligible?: boolean;
  /** Compatibility with v1 snapshots during a rolling deployment. */
  decision_safe?: boolean;
  data: {
    status: ControlStatus;
    source_date: string | null;
    expected_source_date: string | null;
    source_session_lag: number | null;
    event_coverage_pct: number | null;
    expected_events: number | null;
    covered_events: number | null;
    missing_events: number | null;
    contract_rejection_rate: number | null;
    pair_rejection_rate: number | null;
    decision_group_rejection_rate?: number | null;
    decision_groups?: number | null;
    eligible_decision_groups?: number | null;
    contracts: number | null;
    eligible_contracts: number | null;
    live_trading_eligible: boolean;
    decision_scope: string | null;
    quarantine_records: number | null;
    quarantine_status: string;
    replay_status: string;
    corporate_action_status: string;
    corporate_action_rows: number;
    duplicate_rows: number;
  };
  model: {
    status: ControlStatus;
    monitored_at: string | null;
    snapshot_date: string | null;
    champion_active: boolean;
    challenger_present: boolean;
    shadow_roles: string[];
    drift_status: string;
    critical_features: number | null;
    hard_missing_features: number | null;
    warning_features: number;
    fallback_bundle_available: boolean;
    outcome_status: string;
    outcome_common_rows: number | null;
    outcome_minimum_rows: number | null;
    outcome_evaluated_at?: string | null;
    outcome_evaluations?: number;
    rollback_recorded: boolean;
  };
  release?: {
    artifact_promotion_status: ControlStatus;
    data_r2_status: ControlStatus;
    forecast_r2_status: ControlStatus;
    frontend_payload_status: ControlStatus;
    neon_import_status: ControlStatus;
  };
  exceptions: ControlException[];
};

export type ControlWorkflowReference = {
  run_id: string;
  run_number: string;
  run_attempt: string;
  url: string;
  event_name?: string;
  started_at?: string;
  control_ready_seconds?: number | null;
};

export type ControlHistoryRun = {
  generated_at: string;
  status: ControlStatus;
  publication_eligible: boolean;
  source_date: string | null;
  source_session_lag: number | null;
  event_coverage_pct: number | null;
  expected_events: number | null;
  covered_events: number | null;
  missing_events: number | null;
  contract_rejection_rate: number | null;
  pair_rejection_rate: number | null;
  decision_group_rejection_rate?: number | null;
  decision_groups?: number | null;
  eligible_decision_groups?: number | null;
  quarantine_records?: number | null;
  quarantine_status?: string;
  replay_status?: string;
  corporate_action_status?: string;
  duplicate_rows: number | null;
  model_snapshot_date: string | null;
  model_status: ControlStatus;
  drift_status: string;
  critical_features: number | null;
  warning_features: number | null;
  hard_missing_features?: number | null;
  challenger_present: boolean;
  outcome_status: string;
  outcome_common_rows?: number | null;
  outcome_minimum_rows?: number | null;
  outcome_evaluations?: number | null;
  rollback_recorded?: boolean;
  artifact_promotion_status?: ControlStatus;
  data_r2_status?: ControlStatus;
  forecast_r2_status?: ControlStatus;
  frontend_payload_status?: ControlStatus;
  neon_import_status?: ControlStatus;
  critical_exceptions: number;
  warning_exceptions: number;
  exception_codes: string[];
  workflow: ControlWorkflowReference | null;
};

export type ControlHistory = {
  schema: 'quantiv.control-plane-history.v1';
  generated_at: string;
  runs: ControlHistoryRun[];
};
