import { h, ref } from "vue";
import { NButton, NCheckbox, useDialog } from "naive-ui";

import { useI18n } from "@/i18n";
import { ApiError, type FailureReason, type TakenNames, errorText } from "@/api/apiError";

/** Why the server refused one item of a batch or transfer: any reason of a failure. */
export type FailReason = FailureReason;

export interface FailedItem {
  path: string;
  error: string;
  reason?: FailReason;
}

/** An item that landed under another name than asked: its path as sent, and where it landed. */
export interface PlacedItem {
  path: string;
  placed: string;
}

export interface PlacementResult {
  succeeded: string[];
  failed: FailedItem[];
  /** Only a request sent with keep_both answers this. */
  placed?: PlacedItem[];
}

/** What a retry adds to a request: one word, never both. */
export interface PlaceOptions {
  overwrite?: boolean;
  keepBoth?: boolean;
}

/** The answer to a "Replace?" question: "no" is also Escape, a mask click and a view left. */
export type ReplaceChoice = "replace" | "keep_both" | "no";

/** What a caller can show while a placement runs. */
export interface ReplaceHooks {
  /** The first call is back: its result, before anything is asked. */
  onFirst?: (first: PlacementResult) => void;
  /** The user's answer for one name that was taken, as soon as it is known. */
  onDecision?: (path: string, choice: ReplaceChoice) => void;
  /** A retry is back: what it placed and what it failed, before the next retry is sent. */
  onRetried?: (succeeded: string[], failed: FailedItem[]) => void;
}

/** The outcome of a placement after every "Replace?" has been answered. */
export interface ReplaceOutcome {
  succeeded: string[];
  /** Names the user chose to keep as they were. */
  skipped: string[];
  /** Names that landed beside the taken one after "Keep both". */
  placed: PlacedItem[];
  failed: FailedItem[];
}

/** `all`: the tick box "Do the same for the other N" was ticked on a button press. */
type Answer = { choice: ReplaceChoice; all: boolean };

/**
 * How long a prompt's "Replace" stays disabled after it appears, so a
 * click aimed at the button that was under the pointer before cannot answer Yes for a
 * name the user has not read. The delay browsers put on their own permission dialogs.
 */
export const REPLACE_ARM_MS = 500;

function baseName(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

/**
 * The one "Replace?" prompt every view uses. `askReplace` asks about one name;
 * `askReplaceMany` asks once about many names one request would replace; `askEach`
 * asks about several, one at a time, and while more than one name is left
 * offers a tick box that gives the answer to the rest; every question has "Keep both"; `withReplace` runs a batch or
 * transfer call and asks about every name the server found taken; `dismiss` takes an
 * open prompt down and ends whatever was waiting on it; `leave` does that for a view
 * that is going away, and nothing is asked after it.
 */
export function useReplacePrompt() {
  const dialog = useDialog();
  const { t } = useI18n();

  /** The prompt on screen, if any: how to answer it "no" and take it down. */
  let open: { resolve: (answer: ReplaceChoice) => void; close: () => void } | null = null;
  /** Counts `dismiss` calls; an operation that started before one is over. */
  let generation = 0;
  /** The view is gone: every question from now on is answered "no" without a prompt. */
  let left = false;

  /**
   * Take the open prompt down, answering "no" for it and for every name an operation
   * still has to ask about, and let no retry go out. For a view that is left or an
   * operation that is cancelled.
   */
  function dismiss(): void {
    generation += 1;
    const shown = open;
    shown?.resolve("no");
    shown?.close();
  }

  /**
   * `dismiss` for a view that is being left. Work the view started may still be running
   * (an upload of several files goes on to its next file); a name it finds taken after
   * this is kept, since there is no view left to ask in.
   */
  function leave(): void {
    left = true;
    dismiss();
  }

  /**
   * Ask once, with `content` as the question. "No", Escape, a mask click or the dialog
   * closing any other way is "no" for this name only. With `remaining` above one a tick
   * box "Do the same for the other N", under the buttons, lets the answer stand for the
   * rest. The dialog has no close button and the buttons come before the box, so its
   * focus trap lands on "No, keep it" (or "Skip") first and Enter presses it.
   * Only "Replace" waits for the arming delay; "Keep both" loses nothing.
   */
  function prompt(content: string, remaining: number): Promise<Answer> {
    if (left) return Promise.resolve({ choice: "no", all: false });
    return new Promise((resolve) => {
      const armed = ref(false);
      const same = ref(false);
      let instance: ReturnType<typeof dialog.warning> | undefined;
      let timer: ReturnType<typeof setTimeout> | undefined;
      let settled = false;
      const finish = (choice: ReplaceChoice, all = false) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (open === current) open = null;
        resolve({ choice, all });
      };
      const current = {
        resolve: (choice: ReplaceChoice) => finish(choice),
        close: () => instance?.destroy(),
      };
      open = current;
      timer = setTimeout(() => {
        armed.value = true;
      }, REPLACE_ARM_MS);

      const several = remaining > 1;
      const button = (label: string, choice: ReplaceChoice, type: "default" | "warning" = "default") =>
        h(
          NButton,
          {
            size: "small",
            type,
            disabled: choice === "replace" && !armed.value,
            style: "flex:1 1 auto;min-width:0",
            onClick: () => {
              if (choice === "replace" && !armed.value) return;
              finish(choice, several && same.value);
              instance?.destroy();
            },
          },
          () => label,
        );
      instance = dialog.warning({
        title: t("files.replace_title"),
        content,
        closable: false,
        style: "width:min(600px,calc(100vw - 32px))",
        onEsc: () => finish("no"),
        onMaskClick: () => finish("no"),
        onAfterLeave: () => finish("no"),
        action: () =>
          // The buttons come first, so the dialog's first focus is "No" or "Skip" and
          // Enter replaces nothing; the tick box sits under them.
          h("div", { class: "replace-dialog-actions", style: "display:flex;flex-direction:column;gap:12px;width:100%" }, [
            h("div", { class: "replace-actions", style: "display:flex;gap:8px;justify-content:flex-end;flex-wrap:nowrap" }, [
              button(t(several ? "files.skip" : "files.replace_no"), "no"),
              button(t("files.keep_both"), "keep_both"),
              button(t("files.replace_yes"), "replace", "warning"),
            ]),
            several
              ? h(
                  NCheckbox,
                  { checked: same.value, "onUpdate:checked": (value: boolean) => (same.value = value) },
                  () => t("files.replace_same_for_rest", { n: remaining - 1 }),
                )
              : null,
          ]),
      });
    });
  }

  /** Ask one question: the answer, "no" if nothing was answered or it was dismissed meanwhile. */
  async function askOnce(content: string): Promise<ReplaceChoice> {
    const started = generation;
    const answer = await prompt(content, 1);
    return started === generation ? answer.choice : "no";
  }

  async function askReplace(name: string): Promise<ReplaceChoice> {
    return askOnce(t("files.replace_confirm", { name }));
  }

  /**
   * One question for many names a single request would replace (an extract): the
   * server's count and the first names it sent, "…" when it sent fewer than it counted.
   * One answer covers them all.
   */
  async function askReplaceMany(taken: TakenNames): Promise<ReplaceChoice> {
    const more = taken.count > taken.names.length ? ", …" : "";
    return askOnce(t("files.replace_many_confirm", { count: taken.count, names: taken.names.join(", ") + more }));
  }

  /** One decision per name, in the order given. */
  async function askEach(names: string[]): Promise<ReplaceChoice[]> {
    return askEachSince(generation, names);
  }

  /** `askEach` for an operation that began at `started`: dismissed since, it asks nothing. */
  async function askEachSince(
    started: number,
    names: string[],
    onDecision?: (index: number, choice: ReplaceChoice) => void,
  ): Promise<ReplaceChoice[]> {
    const decisions: ReplaceChoice[] = [];
    const decide = (choice: ReplaceChoice) => {
      decisions.push(choice);
      onDecision?.(decisions.length - 1, choice);
    };
    let rest: ReplaceChoice | null = null;
    for (let i = 0; i < names.length; i++) {
      if (started !== generation) {
        decide("no");
        continue;
      }
      if (rest !== null) {
        decide(rest);
        continue;
      }
      const answer = await prompt(t("files.replace_confirm", { name: names[i]! }), names.length - i);
      if (answer.all) rest = answer.choice;
      decide(started === generation ? answer.choice : "no");
    }
    return decisions;
  }

  /**
   * Run `send` for `paths`. Each failed item with reason "exists" is asked about, and the
   * accepted ones are sent again in at most two calls: one with overwrite for "Replace",
   * one with keep_both for "Keep both" (never both words in one request). An item the
   * server could not place, or one a retry fails, keeps the server's own text; a retry
   * is never asked about twice.
   */
  async function withReplace(
    paths: string[],
    send: (paths: string[], options: PlaceOptions) => Promise<PlacementResult>,
    hooks: ReplaceHooks = {},
  ): Promise<ReplaceOutcome> {
    const started = generation;
    const first = await send(paths, {});
    hooks.onFirst?.(first);
    const taken = first.failed.filter((item) => item.reason === "exists");
    const outcome: ReplaceOutcome = {
      succeeded: [...first.succeeded],
      skipped: [],
      placed: [],
      failed: first.failed.filter((item) => item.reason !== "exists"),
    };
    if (!taken.length) return outcome;

    const decisions = await askEachSince(
      started,
      taken.map((item) => baseName(item.path)),
      (i, choice) => hooks.onDecision?.(taken[i]!.path, choice),
    );
    // A dismissed operation sends nothing more, whatever was answered before.
    const live = started === generation;
    const chosen = (choice: ReplaceChoice) => taken.filter((_, i) => live && decisions[i] === choice);
    const replaced = chosen("replace");
    const kept = chosen("keep_both");
    outcome.skipped = taken.filter((item) => !replaced.includes(item) && !kept.includes(item)).map((item) => item.path);

    const retry = async (items: FailedItem[], options: PlaceOptions) => {
      if (!items.length) return;
      const failedBefore = outcome.failed.length;
      let succeeded: string[] = [];
      try {
        const again = await send(items.map((item) => item.path), options);
        succeeded = again.succeeded;
        outcome.succeeded.push(...again.succeeded);
        outcome.placed.push(...(again.placed ?? []));
        for (const item of again.failed) {
          outcome.failed.push(item.reason === "exists" ? { ...item, reason: "failed" } : item);
        }
      } catch (err) {
        // A retry the server refused keeps the refusal's own reason and text, like an item
        // the server refused; any other failure is its message under the reason "failed".
        const failure =
          err instanceof ApiError && err.reason !== null && err.reason !== "network"
            ? { error: err.detail, reason: err.reason }
            : { error: errorText(err), reason: "failed" as const };
        for (const item of items) {
          outcome.failed.push({ path: item.path, ...failure });
        }
      }
      hooks.onRetried?.(succeeded, outcome.failed.slice(failedBefore));
    };
    await retry(replaced, { overwrite: true });
    await retry(kept, { keepBoth: true });
    return outcome;
  }

  return { askReplace, askReplaceMany, askEach, withReplace, dismiss, leave };
}
