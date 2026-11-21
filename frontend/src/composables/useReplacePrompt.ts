import { h, ref } from "vue";
import { NButton, useDialog } from "naive-ui";

import { useI18n } from "@/i18n";
import type { FailureReason } from "@/api/apiError";

/** Why the server refused one item of a batch or transfer: any reason of a failure. */
export type FailReason = FailureReason;

export interface FailedItem {
  path: string;
  error: string;
  reason?: FailReason;
}

export interface PlacementResult {
  succeeded: string[];
  failed: FailedItem[];
}

/** What a caller can show while a placement runs. */
export interface ReplaceHooks {
  /** The first call is back: its result, before anything is asked. */
  onFirst?: (first: PlacementResult) => void;
  /** The user's answer for one name that was taken, as soon as it is known. */
  onDecision?: (path: string, accepted: boolean) => void;
}

/** The outcome of a placement after every "Replace?" has been answered. */
export interface ReplaceOutcome {
  succeeded: string[];
  /** Names the user chose to keep as they were. */
  skipped: string[];
  failed: FailedItem[];
}

type Answer = "yes" | "no" | "yes_all" | "no_all";

/**
 * How long a prompt's "Replace" and "Replace all" stay disabled after it appears, so a
 * click aimed at the button that was under the pointer before cannot answer Yes for a
 * name the user has not read. The delay browsers put on their own permission dialogs.
 */
export const REPLACE_ARM_MS = 500;

function baseName(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/**
 * The one "Replace?" prompt every view uses. `askReplace` asks about one name;
 * `askEach` asks about several, one at a time, and while more than one name is left
 * offers "Replace all" and "Skip all" for the rest; `withReplace` runs a batch or
 * transfer call and asks about every name the server found taken; `dismiss` takes an
 * open prompt down and ends whatever was waiting on it; `leave` does that for a view
 * that is going away, and nothing is asked after it.
 */
export function useReplacePrompt() {
  const dialog = useDialog();
  const { t } = useI18n();

  /** The prompt on screen, if any: how to answer it "no" and take it down. */
  let open: { resolve: (answer: Answer) => void; close: () => void } | null = null;
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
   * Ask once. "No", Escape, a mask click or the dialog closing any other way is "no".
   * With `offerAll` the two buttons are joined by "Skip all" and "Replace all"; the
   * dialog has no close button, so its focus trap lands on "No, keep it" first and
   * Enter presses it.
   */
  function prompt(name: string, offerAll: boolean): Promise<Answer> {
    if (left) return Promise.resolve("no");
    return new Promise((resolve) => {
      const armed = ref(false);
      let instance: ReturnType<typeof dialog.warning> | undefined;
      let timer: ReturnType<typeof setTimeout> | undefined;
      const finish = (answer: Answer) => {
        clearTimeout(timer);
        if (open === current) open = null;
        resolve(answer);
      };
      const current = {
        resolve: finish,
        close: () => instance?.destroy(),
      };
      open = current;
      timer = setTimeout(() => {
        armed.value = true;
        if (instance && !offerAll) instance.positiveButtonProps = { disabled: false };
      }, REPLACE_ARM_MS);

      const options: Parameters<typeof dialog.warning>[0] = {
        title: t("files.replace_title"),
        content: t("files.replace_confirm", { name }),
        closable: false,
        onEsc: () => finish("no"),
        onMaskClick: () => finish("no"),
        onAfterLeave: () => finish("no"),
      };
      if (!offerAll) {
        options.positiveText = t("files.replace_yes");
        options.negativeText = t("files.replace_no");
        options.positiveButtonProps = { disabled: true };
        // A click before the button is armed changes nothing (false keeps the dialog open).
        options.onPositiveClick = () => {
          if (!armed.value) return false;
          finish("yes");
        };
        options.onNegativeClick = () => finish("no");
        instance = dialog.warning(options);
        return;
      }
      const answer = (value: Answer, needsArming: boolean) => () => {
        if (needsArming && !armed.value) return;
        finish(value);
        instance?.destroy();
      };
      const button = (label: string, value: Answer, type: "default" | "warning" = "default") => {
        const replaces = value === "yes" || value === "yes_all";
        return h(
          NButton,
          { size: "small", type, disabled: replaces && !armed.value, onClick: answer(value, replaces) },
          () => label,
        );
      };
      options.action = () =>
        h("div", { class: "replace-actions", style: "display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap" }, [
          button(t("files.replace_no"), "no"),
          button(t("files.replace_yes"), "yes", "warning"),
          button(t("files.skip_all"), "no_all"),
          button(t("files.replace_all"), "yes_all", "warning"),
        ]);
      instance = dialog.warning(options);
    });
  }

  async function askReplace(name: string): Promise<boolean> {
    const started = generation;
    const answer = await prompt(name, false);
    return answer === "yes" && started === generation;
  }

  /** One decision per name, in the order given. */
  async function askEach(names: string[]): Promise<boolean[]> {
    return askEachSince(generation, names);
  }

  /** `askEach` for an operation that began at `started`: dismissed since, it asks nothing. */
  async function askEachSince(
    started: number,
    names: string[],
    onDecision?: (index: number, accepted: boolean) => void,
  ): Promise<boolean[]> {
    const decisions: boolean[] = [];
    const decide = (accepted: boolean) => {
      decisions.push(accepted);
      onDecision?.(decisions.length - 1, accepted);
    };
    let rest: boolean | null = null;
    for (let i = 0; i < names.length; i++) {
      if (started !== generation) {
        decide(false);
        continue;
      }
      if (rest !== null) {
        decide(rest);
        continue;
      }
      const answer = await prompt(names[i]!, names.length - i > 1);
      if (answer === "yes_all") rest = true;
      if (answer === "no_all") rest = false;
      decide((answer === "yes" || answer === "yes_all") && started === generation);
    }
    return decisions;
  }

  /**
   * Run `send` for `paths`. Each failed item with reason "exists" is asked about, and the
   * accepted ones are sent again, in one call, with overwrite. An item the server could
   * not replace, or one the retry fails, keeps the server's own text; the retry is never
   * asked about twice.
   */
  async function withReplace(
    paths: string[],
    send: (paths: string[], overwrite: boolean) => Promise<PlacementResult>,
    hooks: ReplaceHooks = {},
  ): Promise<ReplaceOutcome> {
    const started = generation;
    const first = await send(paths, false);
    hooks.onFirst?.(first);
    const taken = first.failed.filter((item) => item.reason === "exists");
    const outcome: ReplaceOutcome = {
      succeeded: [...first.succeeded],
      skipped: [],
      failed: first.failed.filter((item) => item.reason !== "exists"),
    };
    if (!taken.length) return outcome;

    const decisions = await askEachSince(
      started,
      taken.map((item) => baseName(item.path)),
      (i, accepted) => hooks.onDecision?.(taken[i]!.path, accepted),
    );
    // A dismissed operation sends nothing more, whatever was answered before.
    const live = started === generation;
    const accepted = taken.filter((_, i) => live && decisions[i]);
    outcome.skipped = taken.filter((item) => !accepted.includes(item)).map((item) => item.path);
    if (!accepted.length) return outcome;

    try {
      const retry = await send(accepted.map((item) => item.path), true);
      outcome.succeeded.push(...retry.succeeded);
      for (const item of retry.failed) {
        outcome.failed.push(item.reason === "exists" ? { ...item, reason: "failed" } : item);
      }
    } catch (err) {
      for (const item of accepted) {
        outcome.failed.push({ path: item.path, error: errorText(err), reason: "failed" });
      }
    }
    return outcome;
  }

  return { askReplace, askEach, withReplace, dismiss, leave };
}
