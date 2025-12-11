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
}

export interface DirectoryEntry {
  name: string;
  path: string;
  is_directory: boolean;
  size?: number | null;
  modified?: number | null;
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

export interface TerminalHandshake {
  ws_url: string;
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

export interface DownloadResponse {
  blob: Blob;
  filename: string;
}
