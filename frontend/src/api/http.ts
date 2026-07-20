import type {
  ApiBox,
  ApiSnippet,
  ApiTunnel,
  BatchResult,
  BoxStats,
  BoxStatus,
  BootstrapPayload,
  DirectoryListing,
  FavoriteToggle,
  FilePreview,
  GitBlameResponse,
  GitBranchesResponse,
  GitDiffFilesResponse,
  GitInfo,
  GitLogResponse,
  GitShowResponse,
  GrepResponse,
  LostSession,
  PinToggle,
  SnapshotStatus,
  SearchResponse,
  SessionInfo,
  TerminalHandshake,
  SimpleMessage,
  ProgressList,
  ProgressDeleteResult,
  DiffNotebookEnvelope,
  DiffNotebookFull,
  DiffNotebookList,
  DiffNotebookDeleteResult,
  ExcelPreview,
} from "./types";

export interface TransferProgress {
  file: string;
  bytes_done: number;
  bytes_total: number;
  index: number;
  total: number;
}

export interface TransferResult {
  succeeded: string[];
  failed: { path: string; error: string }[];
}

const API_BASE = "/api/v1";

// Deduplication: only one token refresh at a time
let _refreshPromise: Promise<string | null> | null = null;

async function _refreshToken(): Promise<string | null> {
  const { useBootstrapStore } = await import('@/stores/bootstrap');
  const bootstrapStore = useBootstrapStore();
  bootstrapStore.setToken(null);
  await bootstrapStore.bootstrap();
  return bootstrapStore.token;
}

function refreshTokenOnce(): Promise<string | null> {
  if (!_refreshPromise) {
    _refreshPromise = _refreshToken().finally(() => { _refreshPromise = null; });
  }
  return _refreshPromise;
}

async function handle<T>(response: Response, requestInfo?: { url: string, options: RequestInit }): Promise<T> {
  if (!response.ok) {
    // Auto-retry on 403 (token mismatch after server restart)
    if (response.status === 403 && !response.url.includes('/bootstrap') && requestInfo) {
      console.warn('[API] 403 received, refreshing token...');
      try {
        const newToken = await refreshTokenOnce();
        if (newToken) {
          const existingHeaders: Record<string, string> = {};
          const origHeaders = requestInfo.options.headers;
          if (origHeaders) {
            if (origHeaders instanceof Headers) {
              origHeaders.forEach((v, k) => { existingHeaders[k] = v; });
            } else if (Array.isArray(origHeaders)) {
              origHeaders.forEach(([k, v]) => { existingHeaders[k] = v; });
            } else {
              Object.assign(existingHeaders, origHeaders);
            }
          }
          const retryResponse = await fetch(requestInfo.url, {
            ...requestInfo.options,
            headers: { ...existingHeaders, ...buildHeaders(newToken) },
            credentials: 'include',
          });
          if (retryResponse.ok) {
            return retryResponse.json() as Promise<T>;
          }
          const retryError = await safeParseError(retryResponse);
          console.error('[API] Retry also failed:', retryResponse.status, retryError);
        }
      } catch (retryError) {
        console.error('[API] Token refresh failed:', retryError);
      }
    }

    if (response.status === 401) {
      await handleAuthErrors(response);
    }

    const detail = await safeParseError(response);
    throw new Error(detail || `request failed with ${response.status}`);
  }
  return response.json() as Promise<T>;
}

/**
 * Fetch wrapper that always passes request info to handle() for 403 retry.
 * Use this instead of raw fetch() for all authenticated API calls.
 * @param timeoutMs - optional request timeout in ms (frees browser connection on slow backends)
 */
async function apiFetch<T>(url: string, init?: RequestInit, timeoutMs?: number): Promise<T> {
  const options: RequestInit = { ...init, credentials: 'include' };
  if (timeoutMs) {
    options.signal = AbortSignal.timeout(timeoutMs);
  }
  const res = await fetch(url, options);
  return handle<T>(res, { url, options });
}

async function safeParseError(response: Response): Promise<string | null> {
  try {
    const data = await response.json();
    if (data?.detail) {
      if (Array.isArray(data.detail)) {
        return data.detail.map((item: any) => item?.msg || String(item)).join(", ");
      }
      return String(data.detail);
    }
  } catch (err) {
    return null;
  }
  return null;
}

export function buildHeaders(token?: string | null): HeadersInit {
  const headers: HeadersInit = {
    Accept: "application/json",
  };
  if (token) {
    headers["X-SSHLER-TOKEN"] = token;
  }
  // NOTE: Do NOT include Authorization header - we use httpOnly cookies
  return headers;
}

/**
 * Axios-style HTTP client with automatic cookie-based auth
 * All requests include credentials: 'include' for httpOnly session cookies
 */
export const http = {
  async get<T = any>(url: string, config?: { headers?: HeadersInit }): Promise<{ data: T }> {
    const headers = { ...config?.headers };
    const response = await fetch(url, {
      method: 'GET',
      headers,
      credentials: 'include' // Include httpOnly cookies
    });

    if (!response.ok) {
      await handleAuthErrors(response);
      const detail = await safeParseError(response);
      throw createHttpError(response.status, detail || `GET ${url} failed with ${response.status}`);
    }

    const data = await response.json();
    return { data };
  },

  async post<T = any>(url: string, body?: any, config?: { headers?: HeadersInit }): Promise<{ data: T }> {
    const headers = {
      'Content-Type': 'application/json',
      ...config?.headers
    };
    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'include' // Include httpOnly cookies
    });

    if (!response.ok) {
      await handleAuthErrors(response);
      const detail = await safeParseError(response);
      throw createHttpError(response.status, detail || `POST ${url} failed with ${response.status}`);
    }

    const data = await response.json();
    return { data };
  },

  async put<T = any>(url: string, body?: any, config?: { headers?: HeadersInit }): Promise<{ data: T }> {
    const headers = {
      'Content-Type': 'application/json',
      ...config?.headers
    };
    const response = await fetch(url, {
      method: 'PUT',
      headers,
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'include' // Include httpOnly cookies
    });

    if (!response.ok) {
      await handleAuthErrors(response);
      const detail = await safeParseError(response);
      throw createHttpError(response.status, detail || `PUT ${url} failed with ${response.status}`);
    }

    const data = await response.json();
    return { data };
  },

  async delete<T = any>(url: string, config?: { headers?: HeadersInit }): Promise<{ data: T }> {
    const headers = { ...config?.headers };
    const response = await fetch(url, {
      method: 'DELETE',
      headers,
      credentials: 'include' // Include httpOnly cookies
    });

    if (!response.ok) {
      await handleAuthErrors(response);
      const detail = await safeParseError(response);
      throw createHttpError(response.status, detail || `DELETE ${url} failed with ${response.status}`);
    }

    const data = await response.json();
    return { data };
  },
};

/**
 * Handle authentication-specific errors
 */
async function handleAuthErrors(response: Response): Promise<void> {
  if (response.status === 401) {
    // Unauthorized - clear auth state and redirect to login
    try {
      const { useAuthStore } = await import('@/stores/auth');
      const authStore = useAuthStore();
      authStore.clearUser();

      // Redirect to login page with return URL
      const currentPath = window.location.pathname;
      if (!currentPath.startsWith('/login')) {
        window.location.href = `/login?redirect=${encodeURIComponent(currentPath)}`;
      }
    } catch (err) {
      console.error('Failed to handle 401 error:', err);
    }
  }
}

/**
 * Create HTTP error with response details
 */
function createHttpError(status: number, message: string): Error {
  const error = new Error(message) as any;
  error.response = { status };
  return error;
}

export async function fetchBootstrap(): Promise<BootstrapPayload> {
  return apiFetch<BootstrapPayload>(`${API_BASE}/bootstrap?_t=${Date.now()}`, {
    headers: buildHeaders(),
    cache: 'no-cache',
  });
}

export async function fetchBoxes(token: string | null): Promise<ApiBox[]> {
  return apiFetch<ApiBox[]>(`${API_BASE}/boxes`, {
    headers: buildHeaders(token),
  });
}

export async function fetchBox(name: string, token: string | null): Promise<ApiBox> {
  return apiFetch<ApiBox>(`${API_BASE}/boxes/${encodeURIComponent(name)}`, {
    headers: buildHeaders(token),
  });
}

export async function fetchDirectory(
  box: string,
  directory: string,
  token: string | null,
): Promise<DirectoryListing> {
  const url = new URL(`${API_BASE}/boxes/${encodeURIComponent(box)}/ls`, window.location.origin);
  url.searchParams.set("directory", directory || "/");
  return apiFetch<DirectoryListing>(url.toString().replace(window.location.origin, ""), {
    headers: buildHeaders(token),
  });
}

export async function fetchFilePreview(
  box: string,
  path: string,
  token: string | null,
): Promise<FilePreview> {
  const url = new URL(`${API_BASE}/boxes/${encodeURIComponent(box)}/file`, window.location.origin);
  url.searchParams.set("path", path);
  return apiFetch<FilePreview>(url.toString().replace(window.location.origin, ""), {
    headers: buildHeaders(token),
  });
}

export async function fetchExcelPreview(
  box: string,
  path: string,
  token: string | null,
): Promise<ExcelPreview> {
  const url = new URL(`${API_BASE}/boxes/${encodeURIComponent(box)}/excel`, window.location.origin);
  url.searchParams.set("path", path);
  return apiFetch<ExcelPreview>(url.toString().replace(window.location.origin, ""), {
    headers: buildHeaders(token),
  });
}

export async function fetchSessions(token: string | null): Promise<SessionInfo> {
  return apiFetch<SessionInfo>(`${API_BASE}/sessions`, {
    headers: buildHeaders(token),
  });
}

export async function fetchLayouts(token: string | null) {
  return apiFetch<Array<{
    id: string
    name: string
    terminals: Array<{ boxName: string; sessionName: string; directory: string }>
    created_at: number
  }>>(`${API_BASE}/layouts`, {
    headers: buildHeaders(token),
  });
}

export async function createLayout(
  name: string,
  terminals: Array<{ boxName: string; sessionName: string; directory: string }>,
  token: string | null,
) {
  return apiFetch<{
    id: string
    name: string
    terminals: any[]
    created_at: number
  }>(`${API_BASE}/layouts`, {
    method: 'POST',
    headers: { ...buildHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, terminals }),
  });
}

export async function deleteLayout(layoutId: string, token: string | null) {
  return apiFetch<{ status: string }>(`${API_BASE}/layouts/${layoutId}`, {
    method: 'DELETE',
    headers: buildHeaders(token),
  });
}

export async function fetchTerminalHandshake(token: string | null): Promise<TerminalHandshake> {
  return apiFetch<TerminalHandshake>(`${API_BASE}/terminal/handshake`, {
    headers: buildHeaders(token),
  });
}

export interface TmuxCaptureResult {
  session: string;
  target: string;
  text: string;
  lines: number;
  chars: number;
}

export async function captureTmuxPane(
  box: string,
  session: string,
  token: string | null,
  opts?: { lines?: number; window?: string; pane?: string },
): Promise<TmuxCaptureResult> {
  const params = new URLSearchParams();
  if (opts?.lines !== undefined) params.set("lines", String(opts.lines));
  if (opts?.window) params.set("window", opts.window);
  if (opts?.pane) params.set("pane", opts.pane);
  const qs = params.toString();
  const url = `${API_BASE}/boxes/${encodeURIComponent(box)}/sessions/${encodeURIComponent(session)}/capture${qs ? `?${qs}` : ""}`;
  return apiFetch<TmuxCaptureResult>(url, {
    headers: buildHeaders(token),
  }, 30000);
}

export async function togglePin(name: string, token: string | null): Promise<PinToggle> {
  return apiFetch<PinToggle>(`${API_BASE}/boxes/${encodeURIComponent(name)}/pin`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
  });
}

export async function toggleFavorite(
  name: string,
  path: string,
  favorite: boolean,
  token: string | null,
): Promise<FavoriteToggle> {
  return apiFetch<FavoriteToggle>(`${API_BASE}/boxes/${encodeURIComponent(name)}/fav`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ path, favorite }),
  });
}

export async function touchFile(
  name: string,
  directory: string,
  filename: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/boxes/${encodeURIComponent(name)}/touch`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ directory, filename }),
  });
}

export async function createFolder(
  name: string,
  directory: string,
  foldername: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/boxes/${encodeURIComponent(name)}/mkdir`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ directory, filename: foldername }),
  });
}

export async function deleteFile(
  name: string,
  path: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/boxes/${encodeURIComponent(name)}/delete`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

export async function renameFile(
  name: string,
  path: string,
  new_name: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/boxes/${encodeURIComponent(name)}/rename`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ path, new_name }),
  });
}

export async function moveFile(
  name: string,
  source: string,
  destination: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/boxes/${encodeURIComponent(name)}/move`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ source, destination }),
  });
}

export async function copyFile(
  name: string,
  source: string,
  destination: string,
  new_name: string | null,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/boxes/${encodeURIComponent(name)}/copy`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ source, destination, new_name }),
  });
}

export async function writeFile(
  name: string,
  path: string,
  content: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/boxes/${encodeURIComponent(name)}/write`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ path, content }),
  });
}

export async function uploadFile(
  name: string,
  directory: string,
  file: File,
  token: string | null,
  onProgress?: (percent: number) => void,
): Promise<SimpleMessage> {
  const form = new FormData();
  form.append("directory", directory);
  form.append("file", file);

  // Build headers (excluding Content-Type for FormData)
  const headers = buildHeaders(token);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/boxes/${encodeURIComponent(name)}/upload`, true);

    // Set headers
    Object.entries(headers).forEach(([key, value]) => {
      xhr.setRequestHeader(key, String(value));
    });

    // IMPORTANT: Include credentials for cookie-based auth
    xhr.withCredentials = true;

    if (xhr.upload && onProgress) {
      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable) return;
        const percent = Math.round((event.loaded / event.total) * 100);
        onProgress(percent);
      };
    }

    xhr.onerror = () => reject(new Error("upload failed: network error"));
    xhr.onload = async () => {
      const status = xhr.status;
      const body = xhr.responseText || "{}";
      if (status >= 200 && status < 300) {
        try {
          resolve(JSON.parse(body) as SimpleMessage);
        } catch (err) {
          reject(new Error(`upload parse failed: ${err}`));
        }
        return;
      }
      try {
        const parsed = JSON.parse(body);
        if (parsed?.detail) {
          reject(new Error(Array.isArray(parsed.detail) ? parsed.detail.join(", ") : String(parsed.detail)));
          return;
        }
      } catch {
        // ignore parse errors
      }
      reject(new Error(`upload failed with ${status}`));
    };

    xhr.send(form);
  });
}

export async function boxStatus(name: string, token: string | null) {
  return apiFetch<BoxStatus>(`${API_BASE}/boxes/${encodeURIComponent(name)}/status`, {
    headers: buildHeaders(token),
  });
}

export async function boxStats(name: string, token: string | null) {
  return apiFetch<BoxStats>(`${API_BASE}/boxes/${encodeURIComponent(name)}/stats`, {
    headers: buildHeaders(token),
  }, 12_000); // 12s timeout — remote boxes may need SSH connection
}

export function statsStreamUrl(boxNames: string[], token: string | null): string {
  const params = new URLSearchParams()
  if (boxNames.length > 0) params.set('boxes', boxNames.join(','))
  if (token) params.set('token', token)
  return `${API_BASE}/boxes/stats/stream?${params.toString()}`
}

export async function gitInfo(name: string, directory: string, token: string | null) {
  return apiFetch<GitInfo>(
    `${API_BASE}/boxes/${encodeURIComponent(name)}/git?directory=${encodeURIComponent(directory)}`,
    { headers: buildHeaders(token) },
  );
}

export async function gitLog(
  name: string,
  directory: string,
  token: string | null,
  limit = 50,
): Promise<GitLogResponse> {
  const params = new URLSearchParams({ directory, limit: String(limit) });
  return apiFetch<GitLogResponse>(
    `${API_BASE}/boxes/${encodeURIComponent(name)}/git/log?${params}`,
    { headers: buildHeaders(token) },
  );
}

export async function gitBranches(
  name: string,
  directory: string,
  token: string | null,
): Promise<GitBranchesResponse> {
  const params = new URLSearchParams({ directory });
  return apiFetch<GitBranchesResponse>(
    `${API_BASE}/boxes/${encodeURIComponent(name)}/git/branches?${params}`,
    { headers: buildHeaders(token) },
  );
}

export async function gitDiffFiles(
  name: string,
  directory: string,
  refA: string,
  refB: string,
  token: string | null,
): Promise<GitDiffFilesResponse> {
  const params = new URLSearchParams({ directory, ref_a: refA, ref_b: refB });
  return apiFetch<GitDiffFilesResponse>(
    `${API_BASE}/boxes/${encodeURIComponent(name)}/git/diff-files?${params}`,
    { headers: buildHeaders(token) },
  );
}

export async function gitShow(
  name: string,
  directory: string,
  path: string,
  ref: string,
  token: string | null,
): Promise<GitShowResponse> {
  const params = new URLSearchParams({ directory, path, ref });
  return apiFetch<GitShowResponse>(
    `${API_BASE}/boxes/${encodeURIComponent(name)}/git/show?${params}`,
    { headers: buildHeaders(token) },
  );
}

export async function gitBlame(
  name: string,
  directory: string,
  path: string,
  token: string | null,
): Promise<GitBlameResponse> {
  const params = new URLSearchParams({ directory, path });
  return apiFetch<GitBlameResponse>(
    `${API_BASE}/boxes/${encodeURIComponent(name)}/git/blame?${params}`,
    { headers: buildHeaders(token) },
    65_000,
  );
}

export async function chmodFile(
  name: string,
  path: string,
  mode: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/boxes/${encodeURIComponent(name)}/chmod`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ path, mode }),
  });
}

export async function setBoxTerminalTheme(
  name: string,
  theme: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/boxes/${encodeURIComponent(name)}/theme`, {
    method: "PUT",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ terminal_theme: theme }),
  });
}

export async function fetchBoxSessions(
  name: string,
  token: string | null,
  activeOnly = false,
): Promise<import("./types").ApiSession[]> {
  const params = new URLSearchParams();
  if (activeOnly) params.set("active_only", "true");
  const qs = params.toString() ? `?${params.toString()}` : "";
  return apiFetch<import("./types").ApiSession[]>(
    `${API_BASE}/boxes/${encodeURIComponent(name)}/sessions${qs}`,
    { headers: buildHeaders(token) },
  );
}

export async function syncBoxSessions(
  name: string,
  token: string | null,
): Promise<import("./types").ApiSession[]> {
  return apiFetch<import("./types").ApiSession[]>(
    `${API_BASE}/boxes/${encodeURIComponent(name)}/sessions/sync`,
    { method: "POST", headers: buildHeaders(token) },
    12_000, // 12s timeout — prevents unreachable boxes from blocking browser connection pool
  );
}

export async function deleteSession(
  boxName: string,
  sessionId: string,
  token: string | null,
  killTmux = false,
): Promise<SimpleMessage> {
  const qs = killTmux ? "?kill_tmux=true" : "";
  return apiFetch<SimpleMessage>(
    `${API_BASE}/boxes/${encodeURIComponent(boxName)}/sessions/${encodeURIComponent(sessionId)}${qs}`,
    { method: "DELETE", headers: buildHeaders(token) },
  );
}

export async function renameSession(
  boxName: string,
  sessionId: string,
  newName: string,
  token: string | null,
): Promise<import("./types").ApiSession> {
  return apiFetch<import("./types").ApiSession>(
    `${API_BASE}/boxes/${encodeURIComponent(boxName)}/sessions/${encodeURIComponent(sessionId)}`,
    {
      method: "PATCH",
      headers: { ...buildHeaders(token), "Content-Type": "application/json" },
      body: JSON.stringify({ session_name: newName }),
    },
  );
}

// --- Claude session dashboard -------------------------------------------- //

export async function fetchClaudeSessions(
  token: string | null,
  limit?: number,
  sinceDays?: number,
): Promise<import("./types").ClaudeSession[]> {
  const params = new URLSearchParams();
  if (limit != null) params.set("limit", String(limit));
  if (sinceDays != null) params.set("since_days", String(sinceDays));
  const qs = params.toString() ? `?${params.toString()}` : "";
  return apiFetch<import("./types").ClaudeSession[]>(
    `${API_BASE}/claude/sessions${qs}`,
    { headers: buildHeaders(token) },
  );
}

export async function openClaudeSession(
  sessionId: string,
  token: string | null,
  commandTemplate?: string | null,
): Promise<import("./types").ClaudeOpenResult> {
  const hasTemplate = commandTemplate != null && commandTemplate.trim() !== "";
  return apiFetch<import("./types").ClaudeOpenResult>(
    `${API_BASE}/claude/sessions/${encodeURIComponent(sessionId)}/open`,
    {
      method: "POST",
      headers: hasTemplate
        ? { ...buildHeaders(token), "Content-Type": "application/json" }
        : buildHeaders(token),
      body: hasTemplate
        ? JSON.stringify({ command_template: commandTemplate })
        : undefined,
    },
  );
}

export async function downloadFile(
  name: string,
  path: string,
  token: string | null,
): Promise<Blob> {
  const url = new URL(`${API_BASE}/boxes/${encodeURIComponent(name)}/download`, window.location.origin);
  url.searchParams.set("path", path);
  const res = await fetch(url.toString().replace(window.location.origin, ""), {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  if (!res.ok) {
    throw new Error(`download failed ${res.status}`);
  }
  return res.blob();
}

export async function directorySize(
  name: string,
  path: string,
  token: string | null,
): Promise<{ size_bytes: number }> {
  return apiFetch<{ size_bytes: number }>(
    `${API_BASE}/boxes/${encodeURIComponent(name)}/dir-size?path=${encodeURIComponent(path)}`,
    { headers: buildHeaders(token) },
  );
}

export async function exportPdf(
  html: string,
  filename: string,
  token: string | null,
): Promise<Blob> {
  const res = await fetch(`${API_BASE}/pdf/render`, {
    method: "POST",
    headers: {
      ...buildHeaders(token),
      "Content-Type": "application/json",
    },
    credentials: "include",
    body: JSON.stringify({ html, filename }),
  });
  if (!res.ok) {
    const detail = await safeParseError(res);
    throw new Error(detail || `PDF export failed (${res.status})`);
  }
  return res.blob();
}

export async function downloadDirectory(
  name: string,
  path: string,
  token: string | null,
): Promise<Blob> {
  const url = new URL(`${API_BASE}/boxes/${encodeURIComponent(name)}/download-dir`, window.location.origin);
  url.searchParams.set("path", path);
  const res = await fetch(url.toString().replace(window.location.origin, ""), {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`download failed ${res.status}: ${detail}`);
  }
  return res.blob();
}

export async function statPath(
  name: string,
  path: string,
  token: string | null,
): Promise<{ exists: boolean; is_directory: boolean; is_file: boolean }> {
  return apiFetch<{ exists: boolean; is_directory: boolean; is_file: boolean }>(
    `${API_BASE}/boxes/${encodeURIComponent(name)}/stat?path=${encodeURIComponent(path)}`,
    { headers: buildHeaders(token) },
  );
}

export async function batchDelete(
  name: string,
  paths: string[],
  token: string | null,
): Promise<BatchResult> {
  return apiFetch<BatchResult>(`${API_BASE}/boxes/${encodeURIComponent(name)}/batch/delete`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  });
}

export async function batchMove(
  name: string,
  paths: string[],
  destination: string,
  token: string | null,
): Promise<BatchResult> {
  return apiFetch<BatchResult>(`${API_BASE}/boxes/${encodeURIComponent(name)}/batch/move`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ paths, destination }),
  });
}

export async function batchCopy(
  name: string,
  paths: string[],
  destination: string,
  token: string | null,
): Promise<BatchResult> {
  return apiFetch<BatchResult>(`${API_BASE}/boxes/${encodeURIComponent(name)}/batch/copy`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ paths, destination }),
  });
}

/**
 * Same-box transfer — delegates to existing batchCopy/batchMove.
 */
export async function sameBoxTransfer(
  box: string,
  paths: string[],
  destination: string,
  mode: "copy" | "move",
  token: string | null,
): Promise<BatchResult> {
  if (mode === "move") {
    return batchMove(box, paths, destination, token);
  }
  return batchCopy(box, paths, destination, token);
}

/**
 * Cross-box transfer via SSE streaming POST.
 * Returns an AbortController so the caller can cancel.
 * EventSource doesn't support POST, so we use fetch + ReadableStream.
 */
export function crossBoxTransfer(
  srcBox: string,
  destBox: string,
  paths: string[],
  destination: string,
  mode: "copy" | "move",
  token: string | null,
  onProgress: (progress: TransferProgress) => void,
  onDone: (result: TransferResult) => void,
  onError: (error: string) => void,
): AbortController {
  const controller = new AbortController();

  const run = async () => {
    try {
      const res = await fetch(`${API_BASE}/transfer`, {
        method: "POST",
        headers: {
          ...buildHeaders(token),
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify({
          src_box: srcBox,
          dest_box: destBox,
          paths,
          destination,
          mode,
        }),
        signal: controller.signal,
      });

      if (!res.ok) {
        const text = await res.text();
        let detail: string;
        try {
          detail = JSON.parse(text).detail || text;
        } catch {
          detail = text;
        }
        onError(detail);
        return;
      }

      const reader = res.body?.getReader();
      if (!reader) {
        onError("No response body");
        return;
      }

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Parse SSE events from buffer
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let currentEvent = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            const data = line.slice(6);
            try {
              const parsed = JSON.parse(data);
              if (currentEvent === "progress") {
                onProgress(parsed as TransferProgress);
              } else if (currentEvent === "done") {
                onDone(parsed as TransferResult);
              } else if (currentEvent === "error") {
                onError(parsed.detail || "Transfer error");
              }
            } catch {
              // Ignore malformed JSON
            }
            currentEvent = "";
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      onError(err instanceof Error ? err.message : String(err));
    }
  };

  void run();
  return controller;
}

export async function grepContent(
  name: string,
  pattern: string,
  directory: string,
  token: string | null,
  caseSensitive: boolean = false,
  limit: number = 100,
): Promise<GrepResponse> {
  const url = new URL(`${API_BASE}/boxes/${encodeURIComponent(name)}/grep`, window.location.origin);
  url.searchParams.set("pattern", pattern);
  url.searchParams.set("directory", directory);
  url.searchParams.set("case_sensitive", String(caseSensitive));
  url.searchParams.set("limit", String(limit));
  return apiFetch<GrepResponse>(url.toString().replace(window.location.origin, ""), {
    headers: buildHeaders(token),
  });
}

export async function createArchive(
  name: string,
  paths: string[],
  destination: string,
  archiveName: string,
  format: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/boxes/${encodeURIComponent(name)}/archive/create`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ paths, destination, archive_name: archiveName, format }),
  });
}

export async function extractArchive(
  name: string,
  archivePath: string,
  destination: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/boxes/${encodeURIComponent(name)}/archive/extract`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ archive_path: archivePath, destination }),
  });
}

export async function searchDirectories(
  name: string,
  query: string,
  token: string | null,
  limit: number = 20,
): Promise<SearchResponse> {
  const url = new URL(`${API_BASE}/boxes/${encodeURIComponent(name)}/search`, window.location.origin);
  url.searchParams.set("q", query);
  url.searchParams.set("limit", String(limit));
  return apiFetch<SearchResponse>(url.toString().replace(window.location.origin, ""), {
    headers: buildHeaders(token),
  });
}

// Snippets

export async function fetchSnippets(
  box: string,
  token: string | null,
): Promise<ApiSnippet[]> {
  return apiFetch<ApiSnippet[]>(`${API_BASE}/snippets?box=${encodeURIComponent(box)}`, {
    headers: buildHeaders(token),
  });
}

export async function createSnippet(
  box: string,
  label: string,
  command: string,
  category: string,
  token: string | null,
): Promise<ApiSnippet> {
  return apiFetch<ApiSnippet>(`${API_BASE}/snippets`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ box, label, command, category }),
  });
}

export async function updateSnippet(
  snippetId: string,
  data: { label?: string; command?: string; category?: string; sort_order?: number },
  token: string | null,
): Promise<ApiSnippet> {
  return apiFetch<ApiSnippet>(`${API_BASE}/snippets/${encodeURIComponent(snippetId)}`, {
    method: "PUT",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function deleteSnippet(
  snippetId: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/snippets/${encodeURIComponent(snippetId)}`, {
    method: "DELETE",
    headers: buildHeaders(token),
  });
}

// Tunnels

export async function fetchTunnels(
  box: string,
  token: string | null,
): Promise<ApiTunnel[]> {
  return apiFetch<ApiTunnel[]>(`${API_BASE}/boxes/${encodeURIComponent(box)}/tunnels`, {
    headers: buildHeaders(token),
  });
}

export async function createTunnel(
  box: string,
  data: { tunnel_type: string; local_host: string; local_port: number; remote_host: string; remote_port: number },
  token: string | null,
): Promise<ApiTunnel> {
  return apiFetch<ApiTunnel>(`${API_BASE}/boxes/${encodeURIComponent(box)}/tunnels`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function deleteTunnel(
  box: string,
  tunnelId: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(
    `${API_BASE}/boxes/${encodeURIComponent(box)}/tunnels/${encodeURIComponent(tunnelId)}`,
    { method: "DELETE", headers: buildHeaders(token) },
  );
}

// Recovery

export async function fetchSnapshotStatus(
  token: string | null,
): Promise<SnapshotStatus> {
  return apiFetch<SnapshotStatus>(`${API_BASE}/snapshot/status`, {
    headers: buildHeaders(token),
  });
}

export async function fetchSnapshotConfig(token: string | null) {
  return apiFetch<{ enabled: boolean; interval: number }>(`${API_BASE}/snapshot/config`, {
    headers: buildHeaders(token),
  })
}

export async function updateSnapshotConfig(config: { enabled?: boolean; interval?: number }, token: string | null) {
  return apiFetch<{ enabled: boolean; interval: number }>(`${API_BASE}/snapshot/config`, {
    method: 'PUT',
    headers: { ...buildHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
}

export async function fetchRecovery(
  token: string | null,
): Promise<LostSession[]> {
  return apiFetch<LostSession[]>(`${API_BASE}/recovery`, {
    headers: buildHeaders(token),
  });
}

export async function recreateSession(
  sessionId: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/recovery/${encodeURIComponent(sessionId)}/recreate`, {
    method: "POST",
    headers: buildHeaders(token),
  });
}

export async function recreateSessionBatch(
  sessionIds: string[],
  token: string | null,
): Promise<{ results: Record<string, string> }> {
  return apiFetch<{ results: Record<string, string> }>(`${API_BASE}/recovery/recreate-batch`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ session_ids: sessionIds }),
  });
}

export async function dismissRecoverySession(
  sessionId: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/recovery/${encodeURIComponent(sessionId)}/dismiss`, {
    method: "POST",
    headers: buildHeaders(token),
  });
}

export async function dismissRecovery(
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/recovery/dismiss`, {
    method: "POST",
    headers: buildHeaders(token),
  });
}

export async function fetchProgress(token: string | null): Promise<ProgressList> {
  return apiFetch<ProgressList>(`${API_BASE}/progress`, {
    headers: buildHeaders(token),
  });
}

export async function deleteProgress(
  name: string,
  token: string | null,
): Promise<ProgressDeleteResult> {
  return apiFetch<ProgressDeleteResult>(
    `${API_BASE}/progress/${encodeURIComponent(name)}`,
    {
      method: "DELETE",
      headers: buildHeaders(token),
    },
  );
}

// --- Diff Notebooks (server-side persistence) ---

export async function createDiffNotebook(
  envelope: DiffNotebookEnvelope,
  label: string,
  token: string | null,
): Promise<DiffNotebookFull> {
  return apiFetch<DiffNotebookFull>(
    `${API_BASE}/diff/notebooks`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", ...buildHeaders(token) },
      body: JSON.stringify({ envelope, label }),
    },
  );
}

export async function getDiffNotebook(
  id: string,
  token: string | null,
): Promise<DiffNotebookFull> {
  return apiFetch<DiffNotebookFull>(
    `${API_BASE}/diff/notebooks/${encodeURIComponent(id)}`,
    { headers: buildHeaders(token) },
  );
}

export async function listDiffNotebooks(
  token: string | null,
): Promise<DiffNotebookList> {
  return apiFetch<DiffNotebookList>(
    `${API_BASE}/diff/notebooks`,
    { headers: buildHeaders(token) },
  );
}

export async function deleteDiffNotebook(
  id: string,
  token: string | null,
): Promise<DiffNotebookDeleteResult> {
  return apiFetch<DiffNotebookDeleteResult>(
    `${API_BASE}/diff/notebooks/${encodeURIComponent(id)}`,
    {
      method: "DELETE",
      headers: buildHeaders(token),
    },
  );
}
