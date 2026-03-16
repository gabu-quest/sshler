export interface BootstrapPayload {
  version: string;
  token_header: string;
  token: string | null;
  basic_auth_required: boolean;
  allow_origins: string[];
  spa_base: string;
  spa_enabled: boolean;
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

export interface RecoveryWindow {
  index: number;
  name: string;
  command: string;
  path: string;
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
