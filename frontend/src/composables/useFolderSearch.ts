import { computed, onBeforeUnmount, ref, watch, type Ref } from "vue";

import { searchDirectories } from "@/api/http";
import type { SearchResult } from "@/api/types";
import { errorText, failureText } from "@/api/apiError";

/** Fewest characters of text that start a folder search. */
export const FOLDER_SEARCH_MIN_LENGTH = 2;
/** How long the text must rest before the search is sent. */
export const FOLDER_SEARCH_DELAY_MS = 300;
/** Most folders asked for. */
export const FOLDER_SEARCH_LIMIT = 20;

export interface FolderSearchSource {
  box: () => string | null;
  token: () => string | null;
  /** Folder to scope the walk under (default: home). */
  root?: () => string | null | undefined;
  /** Include files in results, not just folders. */
  files?: () => boolean | undefined;
}

/**
 * The one owner of the folder search of a box: the request, its two-character minimum,
 * its delay and how a failed search reads. The Files page's input and the field on each
 * box card both use it. Only the newest search writes its answer: one the text has moved
 * past is dropped here.
 */
export function useFolderSearch(source: FolderSearchSource) {
  const query = ref("");
  const results = ref<SearchResult[]>([]);
  const loading = ref(false);
  const showDropdown = ref(false);
  // The message of a failed search, shown where "no matches" would be.
  const failureLine = ref<string | null>(null);
  // Why the walk of the box did not run; the folders found before it still show below.
  const walkLine = ref<string | null>(null);
  // The text the held results and failure answer; null while nothing has answered.
  const answeredText = ref<string | null>(null);
  let timer: ReturnType<typeof setTimeout> | null = null;
  let searchId = 0;

  function cancelTimer() {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
  }

  /** Forget the answer and any search under way; the text stays as it is. */
  function reset() {
    cancelTimer();
    searchId += 1;
    loading.value = false;
    showDropdown.value = false;
    results.value = [];
    failureLine.value = null;
    walkLine.value = null;
    answeredText.value = null;
  }

  async function performSearch(text: string) {
    const box = source.box();
    if (!box) return;
    const id = ++searchId;
    loading.value = true;
    showDropdown.value = true;
    try {
      const response = await searchDirectories(box, text, source.token(), FOLDER_SEARCH_LIMIT, {
        root: source.root?.() ?? undefined,
        files: source.files?.(),
      });
      if (id !== searchId) return;
      answeredText.value = text;
      results.value = response.results;
      failureLine.value = null;
      walkLine.value =
        response.error != null || response.reason != null ? failureText(response) : null;
    } catch (err) {
      if (id !== searchId || (err as { name?: unknown } | null)?.name === "AbortError") return;
      answeredText.value = text;
      results.value = [];
      walkLine.value = null;
      failureLine.value = errorText(err);
    } finally {
      if (id === searchId) loading.value = false;
    }
  }

  watch(query, (text) => {
    cancelTimer();
    if (!text || text.length < FOLDER_SEARCH_MIN_LENGTH) {
      reset();
      return;
    }
    // A new text makes the answer to the old one stale at once, not when its timer fires.
    searchId += 1;
    timer = setTimeout(() => {
      timer = null;
      void performSearch(text);
    }, FOLDER_SEARCH_DELAY_MS);
  });

  onBeforeUnmount(reset);

  /** True once the held results (or failure) answer the text now in `query`. */
  const answered = computed(() => answeredText.value !== null && answeredText.value === query.value);

  return { query: query as Ref<string>, results, answered, loading, showDropdown, failureLine, walkLine, reset };
}
