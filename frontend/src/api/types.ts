export interface WindowsShell {
  id: string;
  label: string;
  available: boolean;
}

export interface BootstrapPayload {
  version: string;
  token_header: string;
  token: string | null;
  basic_auth_required: boolean;
  allow_origins: string[];
  spa_base: string;
  spa_enabled: boolean;
  wsl_distro?: string | null;
  pdf_available?: boolean;
  platform?: string;
  windows_shells?: WindowsShell[];
  default_shell?: string | null;
}

export interface ApiBox {
  name: string;
  host: string;
  user: string;
  port: number;
  transport: string;
  pinned: boolean;
  default_dir?: string | null;
  favorites: string[];
  terminal_theme?: string | null;
}

export interface DirectoryEntry {
  name: string;
  path: string;
  is_directory: boolean;
  size?: number | null;
  modified?: number | null;
  mode?: number | null;
  gitignored?: boolean;
}

export interface DirectoryListing {
  box: string;
  directory: string;
  entries: DirectoryEntry[];
}

export interface FilePreview {
  box: string;
  path: string;
  parent: string;
  content?: string | null;
  syntax_class?: string | null;
  image_data?: string | null;
  image_mime?: string | null;
  image_too_large: boolean;
  image_limit_kb: number;
  is_markdown: boolean;
}

export interface SessionInfo {
  sessions: string[];
}

export interface ApiSession {
  id: string;
  box: string;
  session_name: string;
  working_directory: string;
  created_at: number;
  last_accessed_at: number;
  active: boolean;
  window_count: number;
  metadata: Record<string, unknown>;
}

export interface TerminalHandshake {
  ws_url: string;
  token_header?: string;
  token?: string | null;
}

export interface PinToggle {
  name: string;
  pinned: boolean;
}

export interface FavoriteToggle {
  path: string;
  favorite: boolean;
}

export interface SimpleMessage {
  status: string;
  message: string;
  path?: string | null;
}

export interface BoxStatus {
  name: string;
  status: string;
  latency_ms: number | null;
}

export interface BoxStats {
  name: string;
  cpu_percent: number | null;
  memory_used_mb: number | null;
  memory_total_mb: number | null;
  memory_percent: number | null;
  uptime_seconds: number | null;
  error: string | null;
}

export interface GitInfo {
  branch: string | null;
  commit: string | null;
  is_repo: boolean;
  dirty: boolean;
  error?: string | null;
}

export interface DownloadResponse {
  blob: Blob;
  filename: string;
}

export interface SearchResult {
  path: string;
  score: number;
  source: "frecency" | "discovery";
}

export interface SearchResponse {
  box: string;
  query: string;
  results: SearchResult[];
}

export interface BatchResult {
  status: "ok" | "partial";
  succeeded: string[];
  failed: { path: string; error: string }[];
}

export interface GrepMatch {
  file: string;
  line_number: number;
  line: string;
}

export interface GrepResponse {
  box: string;
  pattern: string;
  directory: string;
  matches: GrepMatch[];
  truncated: boolean;
}

export interface ApiSnippet {
  id: string;
  box: string;
  label: string;
  command: string;
  category: string;
  sort_order: number;
  created_at: number;
}

export interface ApiTunnel {
  id: string;
  box: string;
  tunnel_type: "local" | "remote";
  local_host: string;
  local_port: number;
  remote_host: string;
  remote_port: number;
  created_at: number;
}

export interface RecoveryPane {
  index: number;
  command: string;
  path: string;
}

export interface RecoveryWindow {
  index: number;
  name: string;
  command: string;
  path: string;
  panes?: RecoveryPane[];
}

export interface LostSession {
  id: string;
  box: string;
  session_name: string;
  working_directory: string;
  last_snapshot_at: number;
  windows: RecoveryWindow[];
}

export interface SnapshotStatus {
  last_snapshot_at: number | null;
}

// Git Integration types (Commander M4)

export interface GitCommit {
  hash: string;
  short_hash: string;
  message: string;
  author: string;
  date: string;
}

export interface GitLogResponse {
  box: string;
  directory: string;
  commits: GitCommit[];
  is_repo: boolean;
  error?: string | null;
}

export interface GitBlameLine {
  line_number: number;
  content: string;
  commit: string;
  author: string;
  date: string;
}

export interface GitBlameResponse {
  box: string;
  path: string;
  lines: GitBlameLine[];
  truncated?: boolean;
  error?: string | null;
}

export interface GitBranch {
  name: string;
  is_current: boolean;
  last_commit: string | null;
}

export interface GitBranchesResponse {
  box: string;
  directory: string;
  branches: GitBranch[];
  root: string | null;
  is_repo: boolean;
  error?: string | null;
}

export interface GitDiffFile {
  path: string;
  status: string;
}

export interface GitDiffFilesResponse {
  box: string;
  directory: string;
  ref_a: string;
  ref_b: string;
  files: GitDiffFile[];
  error?: string | null;
}

export interface GitShowResponse {
  content: string;
  ref: string;
  path: string;
}

export type ProgressStatus = "running" | "done" | "failed" | "cancelled";

export interface ProgressBar {
  name: string;
  current: number;
  total: number;
  color: string | null;
  label: string | null;
  status: ProgressStatus;
  created_at: number;
  updated_at: number;
  metadata: Record<string, unknown>;
  metadata_error: string | null;
}

export interface ProgressList {
  bars: ProgressBar[];
}

export interface ProgressDeleteResult {
  ok: boolean;
  removed: boolean;
}

export type ProgressEvent =
  | { type: "snapshot"; bars: ProgressBar[] }
  | { type: "upsert"; name: string; bar: ProgressBar }
  | { type: "delete"; name: string; bar: null };

// Diff Notebook (server-side persistence)
export interface DiffNotebookEnvelope {
  v: number;
  cells: Array<{
    l: { box: string; directory: string; ref: string; path: string };
    r: { box: string; directory: string; ref: string; path: string };
  }>;
  def?: { box: string; directory: string };
}

export interface DiffNotebookMeta {
  id: string;
  label: string;
  cell_count: number;
  created_at: number;
  updated_at: number;
}

export interface DiffNotebookFull extends DiffNotebookMeta {
  envelope: DiffNotebookEnvelope;
}

export interface DiffNotebookList {
  notebooks: DiffNotebookMeta[];
}

export interface DiffNotebookDeleteResult {
  ok: boolean;
  removed: boolean;
}

export interface ExcelSheet {
  name: string;
  rows: string[][];
  truncated_rows: boolean;
  truncated_cols: boolean;
}

export interface ExcelPreview {
  sheets: ExcelSheet[];
  active_sheet: string;
  file_too_large: boolean;
}

export interface PingEvent {
  type: "ping";
  id: string;
  title: string;
  body?: string | null;
  color?: "success" | "warning" | "error" | "info" | null;
  icon?: string | null;
  duration?: number | null;
  source?: string | null;
  metadata?: Record<string, unknown> | null;
  sent_at: number;
}

// Claude session dashboard — resumable Claude Code conversations on the local box.
export interface ClaudeSession {
  id: string; // session UUID
  cwd: string;
  title: string;
  last_prompt: string | null;
  last_active: number; // epoch seconds
  git_branch: string | null;
  version: string | null;
  size_bytes: number;
  project_dir: string;
  repo_root: string | null; // git repo root containing cwd (grouping key)
}

export interface ClaudeOpenResult {
  box: string;
  session_name: string;
  working_directory: string;
  window: string; // tmux window (tab) within the session running this conversation
  already_open: boolean; // window already existed (selected, not re-spawned)
}
