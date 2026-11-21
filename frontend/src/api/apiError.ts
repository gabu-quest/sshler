import { translate } from "@/i18n";

/**
 * The reasons a failed request can give, the same words as the server's
 * `STATUS_OF_REASON` (`sshler/api/failure.py`); `tests/test_page_failure_reasons.py`
 * fails when the two lists differ.
 */
export const FAILURE_REASONS = [
  "invalid",
  "auth",
  "token",
  "origin",
  "denied",
  "not_found",
  "exists",
  "cannot_replace",
  "too_large",
  "rate_limited",
  "failed",
  "internal",
  "program_missing",
  "unreachable",
  "timeout",
] as const;

export type FailureReason = (typeof FAILURE_REASONS)[number];

/** A failure's reason as the page knows it: one of the server's words, `network` when
 * no answer came, or null when an answer came without a reason the page knows. */
export type ApiErrorReason = FailureReason | "network" | null;

const KNOWN_REASONS: ReadonlySet<string> = new Set(FAILURE_REASONS);

/** The locale key of the line shown for a reason (`error.reason.none` for null). */
export function reasonKey(reason: ApiErrorReason): string {
  return `error.reason.${reason ?? "none"}`;
}

/** The names one request found taken (an extract onto existing files): how many, and the first few. */
export interface TakenNames {
  count: number;
  names: string[];
}

/**
 * Every failed request of the page. `status` is the answer's status (0 when no answer
 * came), `reason` what failed, `detail` the server's own text ("" when it gave none),
 * `taken` the names a refusal of many names is about (null for any other failure).
 * `message` is what a person reads: the translated line of the reason, then " — " and
 * the detail when there is one, or the status in brackets when there is neither a
 * reason nor a detail. A reader decides by `reason`, never by the words. `retryAfter` is
 * the answer's `Retry-After` in whole seconds, or null when it sent none.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly reason: ApiErrorReason;
  readonly detail: string;
  readonly retryAfter: number | null;
  readonly taken: TakenNames | null;

  constructor(
    status: number,
    reason: ApiErrorReason,
    detail: string,
    retryAfter: number | null = null,
    taken: TakenNames | null = null,
  ) {
    const line = translate(reasonKey(reason));
    // An answer with no reason and no text keeps its status, so it can still be told apart.
    const bare = reason === null && !detail && status > 0;
    super(detail ? `${line} — ${detail}` : bare ? `${line} (${status})` : line);
    this.name = "ApiError";
    this.status = status;
    this.reason = reason;
    this.detail = detail;
    this.retryAfter = retryAfter;
    this.taken = taken;
  }
}

function takenNames(taken: unknown): TakenNames | null {
  const fields = (typeof taken === "object" && taken !== null ? taken : {}) as { count?: unknown; names?: unknown };
  const { count, names } = fields;
  if (typeof count !== "number" || !Array.isArray(names) || !names.every((n) => typeof n === "string")) return null;
  return { count, names: names as string[] };
}

/** A `Retry-After` header's whole seconds; null when it is absent or not a number of
 * seconds (the date form is not sent by this server). */
export function retryAfterSeconds(header: string | null): number | null {
  if (header === null || !/^\d+$/.test(header.trim())) return null;
  return Number(header.trim());
}

function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  // FastAPI's own check of a request's shape answers a list of problems.
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const msg = (item as { msg?: unknown } | null)?.msg;
        return typeof msg === "string" && msg ? msg : String(item);
      })
      .join(", ");
  }
  return "";
}

/** The error of a failure body already read as JSON: `{"detail": ..., "reason": ...}`, and `taken` where it has one. */
export function apiErrorOfBody(status: number, body: unknown, retryAfter: number | null = null): ApiError {
  const fields = (typeof body === "object" && body !== null ? body : {}) as {
    detail?: unknown;
    reason?: unknown;
    taken?: unknown;
  };
  const reason =
    typeof fields.reason === "string" && KNOWN_REASONS.has(fields.reason)
      ? (fields.reason as FailureReason)
      : null;
  return new ApiError(status, reason, detailText(fields.detail), retryAfter, takenNames(fields.taken));
}

/** The error of a failed answer from its status and its body's text. A body that is not
 * JSON (a proxy's page) gives no reason and no detail: its markup is never shown. */
export function apiErrorOf(status: number, text: string, retryAfter: number | null = null): ApiError {
  let body: unknown = null;
  try {
    body = JSON.parse(text);
  } catch {
    // Not JSON: the error has no reason and no detail.
  }
  return apiErrorOfBody(status, body, retryAfter);
}

/** A failure that arrives inside a 200 answer: a box's status, a stats event of a box
 * that failed, a directory search whose walk did not run, a failed item of a batch. */
export interface FailureFields {
  error?: string | null;
  reason?: string | null;
}

/**
 * The text a person reads for a failure inside a 200 answer: the same words an
 * `ApiError` of that reason and detail has as its message. A reason the page does not
 * know reads as the no-reason line; an empty `error` leaves the line alone.
 */
export function failureText(failure: FailureFields): string {
  return apiErrorOfBody(0, { detail: failure.error ?? "", reason: failure.reason }).message;
}

/** The error of a request that got no answer at all. */
export function networkError(): ApiError {
  return new ApiError(0, "network", "");
}

/** What a caught value reads as: an `Error`'s message, else the value as text. */
export function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
