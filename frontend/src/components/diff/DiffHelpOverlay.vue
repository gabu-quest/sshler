<script setup lang="ts">
import { NModal, NCard, NButton, NIcon } from "naive-ui";
import { PhX } from "@phosphor-icons/vue";
import { useI18n } from "@/i18n";

const show = defineModel<boolean>({ default: false });

const { t } = useI18n();

const commands = [
  { cmd: ":add", desc: "diff.help.add" },
  { cmd: ":add <left>", desc: "diff.help.add_one" },
  { cmd: ":add <left> <right>", desc: "diff.help.add_two" },
  { cmd: ":rm <n>", desc: "diff.help.rm" },
  { cmd: ":swap <n>", desc: "diff.help.swap_sides" },
  { cmd: ":swap <n> <m>", desc: "diff.help.swap_cells" },
  { cmd: ":repo <box> <dir>", desc: "diff.help.repo" },
  { cmd: ":clear", desc: "diff.help.clear" },
  { cmd: "?", desc: "diff.help.help" },
];

const shortcuts = [
  { key: "c", desc: "diff.help.kbd_focus" },
  { key: "?", desc: "diff.help.kbd_help" },
  { key: "j / k", desc: "diff.help.kbd_nav" },
  { key: "Esc", desc: "diff.help.kbd_blur" },
];
</script>

<template>
  <NModal v-model:show="show" :auto-focus="false">
    <NCard
      :title="t('diff.help.title')"
      style="max-width: 640px"
      :bordered="false"
      role="dialog"
      :aria-modal="true"
    >
      <template #header-extra>
        <NButton quaternary circle size="small" @click="show = false" :aria-label="t('common.close')">
          <NIcon size="14"><PhX weight="bold" /></NIcon>
        </NButton>
      </template>

      <section class="help-section">
        <h3 class="help-heading">{{ t("diff.help.commands_title") }}</h3>
        <dl class="help-list">
          <template v-for="row in commands" :key="row.cmd">
            <dt><code>{{ row.cmd }}</code></dt>
            <dd>{{ t(row.desc) }}</dd>
          </template>
        </dl>
      </section>

      <section class="help-section">
        <h3 class="help-heading">{{ t("diff.help.shortcuts_title") }}</h3>
        <dl class="help-list">
          <template v-for="row in shortcuts" :key="row.key">
            <dt><kbd>{{ row.key }}</kbd></dt>
            <dd>{{ t(row.desc) }}</dd>
          </template>
        </dl>
      </section>

      <section class="help-section">
        <h3 class="help-heading">{{ t("diff.help.side_syntax_title") }}</h3>
        <p>{{ t("diff.help.side_syntax_body") }}</p>
        <pre class="side-examples">{{ t("diff.help.side_syntax_examples") }}</pre>
      </section>
    </NCard>
  </NModal>
</template>

<style scoped>
.help-section {
  margin-bottom: 16px;
}

.help-heading {
  margin: 0 0 8px 0;
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  color: var(--muted);
}

.help-list {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 6px 16px;
  margin: 0;
}

.help-list dt {
  font-family: "JetBrains Mono", monospace;
  font-size: 12px;
  color: var(--accent);
}

.help-list dt code,
.help-list dt kbd {
  background: var(--hover-overlay);
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid var(--stroke);
}

.help-list dd {
  margin: 0;
  font-size: 13px;
  color: var(--text-color, inherit);
}

.side-examples {
  font-family: "JetBrains Mono", monospace;
  font-size: 12px;
  background: var(--hover-overlay);
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid var(--stroke);
  white-space: pre-wrap;
  margin: 0;
}
</style>
