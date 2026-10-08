import { ref, watch, type Ref } from "vue";

/**
 * A ref backed by `localStorage`. Reads and validates the persisted value at
 * creation time — malformed JSON, or a value that fails `isValid`, silently
 * falls back to `defaultValue` (never throws). Every subsequent mutation is
 * written back to storage as JSON.
 *
 * `isValid` is a type guard so callers get a correctly-typed `Ref<T>` without
 * an unsafe cast.
 */
export function usePersistedRef<T>(
  key: string,
  defaultValue: T,
  isValid: (value: unknown) => value is T,
): Ref<T> {
  const state = ref(readPersisted(key, defaultValue, isValid)) as Ref<T>;

  watch(
    state,
    (value) => {
      writePersisted(key, value);
    },
    { deep: true },
  );

  return state;
}

function readPersisted<T>(
  key: string,
  defaultValue: T,
  isValid: (value: unknown) => value is T,
): T {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return defaultValue;
    const parsed: unknown = JSON.parse(raw);
    return isValid(parsed) ? parsed : defaultValue;
  } catch {
    return defaultValue;
  }
}

function writePersisted<T>(key: string, value: T): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage unavailable (private browsing, quota exceeded, disabled) —
    // persistence is best-effort and must never break the UI.
  }
}

export function isString(value: unknown): value is string {
  return typeof value === "string";
}

export function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}
