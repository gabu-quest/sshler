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
  ArtifactCreateResult,
  ArtifactDeleteResult,
  ArtifactList,
  ArtifactMode,
  ArtifactPageList,
  ArtifactProject,
  ArtifactProjectList,
  ArtifactRegistration,
} from "./types";
import { ApiError, apiErrorOf, apiErrorOfBody, networkError } from "./apiError";
import type { FailureReason } from "./apiError";

export interface TransferProgress {
  file: string;
  bytes_done: number;
  bytes_total: number;
  index: number;
  total: number;
}

export interface TransferResult {
  succeeded: string[];
  failed: { path: string; error: string; reason?: FailureReason }[];
}

/** Sent only when true, so a request without it is the plain, refusing one. */
export interface OverwriteOption {
  overwrite?: boolean;
}

/** A cross-box transfer's options. */
export interface CrossBoxOptions extends OverwriteOption {
  /**
   * An item the server could not place, told the moment it happens. The last event
   * names every such item again, so this is only for what is shown meanwhile.
   */
  onItemFailed?: (item: TransferResult["failed"][number]) => void;
}

function overwriteField(options: OverwriteOption): { overwrite?: true } {
  return options.overwrite ? { overwrite: true } : {};
}

const API_BASE = "/api/v1";
const TOKEN_HEADER = "X-SSHLER-TOKEN";

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

/** The page's current token, for a request whose caller sent none. */
async function currentToken(): Promise<string | null> {
  const { useBootstrapStore } = await import('@/stores/bootstrap');
  return useBootstrapStore().token;
}

/** How a request acts on a failure besides throwing it. */
interface FailurePolicy {
  /** On `token`: refresh the token once and send once more (false for the bootstrap
   * call, which is the refresh itself). Default true. */
  refresh?: boolean;
  /** On `auth`: go to the sign-in page. Default true. */
  signIn?: boolean;
}

interface RequestOptions extends FailurePolicy {
  /** Abort after this many ms (frees the browser's connection on a slow backend). */
  timeoutMs?: number;
}

/**
 * Run `attempt` and act on its failure by the reason, never by the status: `token`
 * refreshes the token once and runs it once more with the new token (a second failure
 * is thrown, never retried); `auth` goes to the sign-in page; every failure is then
 * thrown. Anything that is not an `ApiError` (an `AbortError`) passes untouched.
 */
async function withFailurePolicy<T>(
  attempt: (freshToken?: string) => Promise<T>,
  policy: FailurePolicy,
): Promise<T> {
  try {
    return await attempt();
  } catch (err) {
    if (!(err instanceof ApiError)) throw err;
    if (err.reason === "token" && policy.refresh !== false) {
      let fresh: string | null = null;
      try {
        fresh = await refreshTokenOnce();
      } catch (refreshError) {
        // A refresh that fails leaves the first failure as the answer.
        console.error('[API] Token refresh failed:', refreshError);
      }
      if (!fresh) throw err;
      try {
        return await attempt(fresh);
      } catch (again) {
        if (again instanceof ApiError && again.reason === "auth" && policy.signIn !== false) {
          await handleAuthErrors();
        }
        throw again;
      }
    }
    if (err.reason === "auth" && policy.signIn !== false) {
      await handleAuthErrors();
    }
    throw err;
  }
}

function headerRecord(headers: HeadersInit | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  if (!headers) return out;
  if (headers instanceof Headers) {
    headers.forEach((v, k) => { out[k] = v; });
  } else if (Array.isArray(headers)) {
    headers.forEach(([k, v]) => { out[k] = v; });
  } else {
    Object.assign(out, headers);
  }
  return out;
}

/**
 * The one request of the page: every fetch goes through it. It sends the session
 * cookie and the token header (the caller's, else the page's current token), and
 * answers the response of a request that worked; a failed one throws an `ApiError`
 * (status 0 and the reason `network` when no answer came) after `withFailurePolicy`
 * has acted on its reason. An aborted request throws its own `AbortError` (or
 * `TimeoutError`), untouched.
 */
async function request(url: string, init: RequestInit = {}, options: RequestOptions = {}): Promise<Response> {
  const once = async (freshToken?: string): Promise<Response> => {
    const headers = headerRecord(init.headers);
    if (freshToken) {
      headers[TOKEN_HEADER] = freshToken;
    } else if (!headers[TOKEN_HEADER]) {
      const token = await currentToken();
      if (token) headers[TOKEN_HEADER] = token;
    }
    const sent: RequestInit = { ...init, headers, credentials: 'include' };
    if (options.timeoutMs) sent.signal = AbortSignal.timeout(options.timeoutMs);
    let response: Response;
    try {
      response = await fetch(url, sent);
    } catch (err) {
      const name = (err as { name?: unknown } | null)?.name;
      if (name === "AbortError" || name === "TimeoutError") throw err;
      throw networkError();
    }
    if (!response.ok) {
      throw apiErrorOf(response.status, await response.text().catch(() => ""));
    }
    return response;
  };
  return withFailurePolicy(once, options);
}

/** `request`, answering the JSON body. */
async function apiFetch<T>(url: string, init?: RequestInit, timeoutMs?: number): Promise<T> {
  const response = await request(url, init, { timeoutMs });
  return response.json() as Promise<T>;
}

export function buildHeaders(token?: string | null): HeadersInit {
  const headers: HeadersInit = {
    Accept: "application/json",
  };
  if (token) {
    headers[TOKEN_HEADER] = token;
  }
  // NOTE: Do NOT include Authorization header - we use httpOnly cookies
  return headers;
}

async function verb<T>(method: string, url: string, body: unknown, headers: HeadersInit | undefined): Promise<{ data: T }> {
  const hasBody = body !== undefined;
  const response = await request(url, {
    method,
    headers: hasBody ? { 'Content-Type': 'application/json', ...headerRecord(headers) } : headerRecord(headers),
    body: hasBody && body ? JSON.stringify(body) : undefined,
  });
  return { data: (await response.json()) as T };
}

/**
 * Axios-style HTTP client over `request`: the cookie, the token header, the refresh
 * and the sign-in redirect are the same as every other request's.
 */
export const http = {
  get<T = any>(url: string, config?: { headers?: HeadersInit }): Promise<{ data: T }> {
    return verb<T>('GET', url, undefined, config?.headers);
  },
  post<T = any>(url: string, body?: any, config?: { headers?: HeadersInit }): Promise<{ data: T }> {
    return verb<T>('POST', url, body ?? null, config?.headers);
  },
  put<T = any>(url: string, body?: any, config?: { headers?: HeadersInit }): Promise<{ data: T }> {
    return verb<T>('PUT', url, body ?? null, config?.headers);
  },
  delete<T = any>(url: string, config?: { headers?: HeadersInit }): Promise<{ data: T }> {
    return verb<T>('DELETE', url, undefined, config?.headers);
  },
};

// The sign-in navigation in flight: failures that arrive together share it.
let _signInPromise: Promise<void> | null = null;

/**
 * The sign-in redirect: clear the signed-in user and go to the sign-in page through
 * the router, carrying the current route as `redirect`. On the sign-in page itself it
 * does not navigate, and failures that arrive together navigate once.
 */
async function handleAuthErrors(): Promise<void> {
  try {
    const { useAuthStore } = await import('@/stores/auth');
    useAuthStore().clearUser();

    const { default: router } = await import('@/router');
    const current = router.currentRoute.value;
    if (current.name === 'login') return;
    if (!_signInPromise) {
      _signInPromise = Promise.resolve(
        router.push({ name: 'login', query: { redirect: current.fullPath } }),
      ).then(() => undefined).finally(() => { _signInPromise = null; });
    }
    await _signInPromise;
  } catch (err) {
    console.error('Failed to handle 401 error:', err);
  }
}

export async function fetchBootstrap(): Promise<BootstrapPayload> {
  // The bootstrap call is the token refresh itself: a `token` failure is never refreshed.
  const response = await request(`${API_BASE}/bootstrap?_t=${Date.now()}`, {
    headers: buildHeaders(),
    cache: 'no-cache',
  }, { refresh: false });
  return response.json() as Promise<BootstrapPayload>;
}

/**
 * Whether the server accepts `token`: false on any failed answer or none. It neither
 * refreshes the token nor goes to the sign-in page; the caller decides.
 */
export async function tokenWorks(token: string): Promise<boolean> {
  try {
    await request(`${API_BASE}/boxes`, { headers: buildHeaders(token) }, { refresh: false, signIn: false });
    return true;
  } catch (err) {
    if (err instanceof ApiError) return false;
    throw err;
  }
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
  options: OverwriteOption = {},
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(`${API_BASE}/boxes/${encodeURIComponent(name)}/rename`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ path, new_name, ...overwriteField(options) }),
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

/**
 * Upload one file. It keeps `XMLHttpRequest` (that reports progress), and its failure is
 * the same `ApiError` as every request's, acted on the same way (`token` refreshes and
 * sends once more, `auth` goes to the sign-in page).
 */
export async function uploadFile(
  name: string,
  directory: string,
  file: File,
  token: string | null,
  onProgress?: (percent: number) => void,
  options: { overwrite?: boolean } = {},
): Promise<SimpleMessage> {
  const form = new FormData();
  form.append("directory", directory);
  if (options.overwrite) {
    form.append("overwrite", "true");
  }
  form.append("file", file);

  const once = async (freshToken?: string): Promise<SimpleMessage> => {
    // Build headers (excluding Content-Type for FormData)
    const headers = buildHeaders(freshToken ?? token ?? (await currentToken()));
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}/boxes/${encodeURIComponent(name)}/upload`, true);

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

      xhr.onerror = () => reject(networkError());
      xhr.onload = () => {
        const status = xhr.status;
        if (status >= 200 && status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText || "{}") as SimpleMessage);
          } catch {
            // An answer that worked but is not JSON: a failure with no reason.
            reject(apiErrorOf(status, ""));
          }
          return;
        }
        reject(apiErrorOf(status, xhr.responseText));
      };

      xhr.send(form);
    });
  };

  return withFailurePolicy(once, {});
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
  force = false,
): Promise<SimpleMessage> {
  const params = new URLSearchParams();
  if (killTmux) params.set("kill_tmux", "true");
  if (force) params.set("force", "true");
  const qs = params.toString() ? `?${params.toString()}` : "";
  return apiFetch<SimpleMessage>(
    `${API_BASE}/boxes/${encodeURIComponent(boxName)}/sessions/${encodeURIComponent(sessionId)}${qs}`,
    { method: "DELETE", headers: buildHeaders(token) },
  );
}

export async function forceKillSessionByName(
  boxName: string,
  sessionName: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(
    `${API_BASE}/boxes/${encodeURIComponent(boxName)}/sessions/by-name/${encodeURIComponent(sessionName)}/force-kill`,
    { method: "POST", headers: buildHeaders(token) },
  );
}

/**
 * Forcibly kill a native shell by session NAME (the "kill terminal" action).
 *
 * Closing a tab only detaches — the native ConPTY persists so the tab can
 * re-attach. This terminates that live shell, so a tab reopened
 * with the same name spawns a FRESH shell instead of re-attaching to the old one.
 */
export async function killTerminalSession(
  boxName: string,
  sessionName: string,
  token: string | null,
): Promise<SimpleMessage> {
  return apiFetch<SimpleMessage>(
    `${API_BASE}/boxes/${encodeURIComponent(boxName)}/terminal-sessions/${encodeURIComponent(sessionName)}`,
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
  const res = await request(url.toString().replace(window.location.origin, ""), {
    headers: buildHeaders(token),
  });
  return res.blob();
}

export async function directorySize(
  name: string,
  path: string,
  token: string | null,
): Promise<{ size_bytes: number | null }> {
  // null: the server could not measure the folder (neither `du -sb` nor `du -sk`).
  return apiFetch<{ size_bytes: number | null }>(
    `${API_BASE}/boxes/${encodeURIComponent(name)}/dir-size?path=${encodeURIComponent(path)}`,
    { headers: buildHeaders(token) },
  );
}

export async function exportPdf(
  html: string,
  filename: string,
  token: string | null,
): Promise<Blob> {
  const res = await request(`${API_BASE}/pdf/render`, {
    method: "POST",
    headers: {
      ...buildHeaders(token),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ html, filename }),
  });
  return res.blob();
}

/**
 * The file name a `Content-Disposition` header carries: the RFC 5987
 * `filename*=UTF-8''<percent-encoded>` form first (exact, any script), else the
 * plain `filename="..."` fallback; null when neither is present or decodable.
 */
export function filenameFromContentDisposition(header: string | null): string | null {
  if (!header) return null;
  const extended = /filename\*\s*=\s*UTF-8''([^;\s]+)/i.exec(header)?.[1];
  if (extended !== undefined) {
    try {
      return decodeURIComponent(extended);
    } catch {
      // Malformed percent-encoding: fall through to the plain form.
    }
  }
  const plain = /filename\s*=\s*(?:"([^"]*)"|([^;\s]+))/i.exec(header);
  return (plain?.[1] ?? plain?.[2]) || null;
}

/** Zip a folder on the box. `filename` is the server's name for the zip (for
 *  `~` it is the real home folder name), null when the response has none. */
export async function downloadDirectory(
  name: string,
  path: string,
  token: string | null,
): Promise<{ blob: Blob; filename: string | null }> {
  const url = new URL(`${API_BASE}/boxes/${encodeURIComponent(name)}/download-dir`, window.location.origin);
  url.searchParams.set("path", path);
  const res = await request(url.toString().replace(window.location.origin, ""), {
    headers: buildHeaders(token),
  });
  const filename = filenameFromContentDisposition(res.headers.get("Content-Disposition"));
  return { blob: await res.blob(), filename };
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
  options: OverwriteOption = {},
): Promise<BatchResult> {
  return apiFetch<BatchResult>(`${API_BASE}/boxes/${encodeURIComponent(name)}/batch/move`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ paths, destination, ...overwriteField(options) }),
  });
}

export async function batchCopy(
  name: string,
  paths: string[],
  destination: string,
  token: string | null,
  options: OverwriteOption = {},
): Promise<BatchResult> {
  return apiFetch<BatchResult>(`${API_BASE}/boxes/${encodeURIComponent(name)}/batch/copy`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ paths, destination, ...overwriteField(options) }),
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
  options: OverwriteOption = {},
): Promise<BatchResult> {
  if (mode === "move") {
    return batchMove(box, paths, destination, token, options);
  }
  return batchCopy(box, paths, destination, token, options);
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
  /** The text a person reads: an `ApiError`'s message for a failed request or a
   * failed transfer. */
  onError: (error: string) => void,
  options: CrossBoxOptions = {},
): AbortController {
  const controller = new AbortController();

  const run = async () => {
    try {
      const res = await request(`${API_BASE}/transfer`, {
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
          ...overwriteField(options),
        }),
        signal: controller.signal,
      });

      const reader = res.body?.getReader();
      if (!reader) {
        onError("No response body");
        return;
      }

      const decoder = new TextDecoder();
      let buffer = "";
      // The event named by the last `event:` line stays until its `data:` line comes,
      // however the reads cut the stream.
      let currentEvent = "";
      let finished = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Parse SSE events from buffer
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("event: ")) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            const data = line.slice(6);
            try {
              const parsed = JSON.parse(data);
              if (currentEvent === "progress") {
                onProgress(parsed as TransferProgress);
              } else if (currentEvent === "failed") {
                options.onItemFailed?.(parsed as TransferResult["failed"][number]);
              } else if (currentEvent === "done") {
                finished = true;
                onDone(parsed as TransferResult);
              } else if (currentEvent === "error") {
                finished = true;
                // The event carries the failure body: its `detail` and its `reason`.
                onError(apiErrorOfBody(0, parsed).message);
              }
            } catch {
              // Ignore malformed JSON
            }
            currentEvent = "";
          }
        }
      }
      if (!finished) onError("The transfer ended before it finished.");
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
  options: { root?: string | null; files?: boolean } = {},
): Promise<SearchResponse> {
  const url = new URL(`${API_BASE}/boxes/${encodeURIComponent(name)}/search`, window.location.origin);
  url.searchParams.set("q", query);
  url.searchParams.set("limit", String(limit));
  if (options.root) url.searchParams.set("root", options.root);
  if (options.files) url.searchParams.set("files", "true");
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

// --- Local HTML artifacts ---

export async function fetchArtifactProjects(
  token: string | null,
): Promise<ArtifactProjectList> {
  return apiFetch<ArtifactProjectList>(`${API_BASE}/artifact-projects`, {
    headers: buildHeaders(token),
  });
}

export async function createArtifactProject(
  name: string,
  token: string | null,
): Promise<ArtifactProject> {
  return apiFetch<ArtifactProject>(`${API_BASE}/artifact-projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...buildHeaders(token) },
    body: JSON.stringify({ name }),
  });
}

export async function renameArtifactProject(
  id: string,
  name: string,
  token: string | null,
): Promise<ArtifactProject> {
  return apiFetch<ArtifactProject>(
    `${API_BASE}/artifact-projects/${encodeURIComponent(id)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...buildHeaders(token) },
      body: JSON.stringify({ name }),
    },
  );
}

export async function deleteArtifactProject(
  id: string,
  cascade: boolean,
  token: string | null,
): Promise<{ ok: boolean; removed: boolean; registrations_removed: number }> {
  return apiFetch(
    `${API_BASE}/artifact-projects/${encodeURIComponent(id)}?cascade=${cascade}`,
    { method: "DELETE", headers: buildHeaders(token) },
  );
}

export interface ArtifactFilters {
  q?: string;
  project?: string;
  group?: string;
  mode?: ArtifactMode;
  exists?: boolean;
}

export async function fetchArtifacts(
  token: string | null,
  filters: ArtifactFilters = {},
): Promise<ArtifactList> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const query = params.size ? `?${params.toString()}` : "";
  return apiFetch<ArtifactList>(`${API_BASE}/artifacts${query}`, {
    headers: buildHeaders(token),
  });
}

export async function createArtifact(
  body: Record<string, unknown>,
  token: string | null,
): Promise<ArtifactCreateResult> {
  return apiFetch<ArtifactCreateResult>(`${API_BASE}/artifacts`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...buildHeaders(token) },
    body: JSON.stringify(body),
  });
}

export async function updateArtifact(
  id: string,
  body: Record<string, unknown>,
  token: string | null,
): Promise<ArtifactRegistration> {
  return apiFetch<ArtifactRegistration>(
    `${API_BASE}/artifacts/${encodeURIComponent(id)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...buildHeaders(token) },
      body: JSON.stringify(body),
    },
  );
}

export async function fetchArtifactPages(
  id: string,
  token: string | null,
): Promise<ArtifactPageList> {
  return apiFetch<ArtifactPageList>(
    `${API_BASE}/artifacts/${encodeURIComponent(id)}/pages`,
    { headers: buildHeaders(token) },
  );
}

export async function rescanArtifact(
  id: string,
  token: string | null,
): Promise<ArtifactPageList> {
  return apiFetch<ArtifactPageList>(
    `${API_BASE}/artifacts/${encodeURIComponent(id)}/rescan`,
    { method: "POST", headers: buildHeaders(token) },
  );
}

export async function deleteArtifact(
  id: string,
  token: string | null,
): Promise<ArtifactDeleteResult> {
  return apiFetch<ArtifactDeleteResult>(
    `${API_BASE}/artifacts/${encodeURIComponent(id)}`,
    { method: "DELETE", headers: buildHeaders(token) },
  );
}
