<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue";
import { NTabs, NTab, NSpin, NAlert } from "naive-ui";
import { fetchExcelPreview } from "@/api/http";
import type { ExcelPreview, ExcelSheet } from "@/api/types";

const props = defineProps<{
  box: string;
  path: string;
  token: string | null;
}>();

const loading = ref(false);
const error = ref<string | null>(null);
const data = ref<ExcelPreview | null>(null);
const activeTab = ref<string>("");
const selectedCell = ref<{ label: string; value: string } | null>(null);

const activeSheet = computed<ExcelSheet | null>(() => {
  if (!data.value) return null;
  return data.value.sheets.find((s) => s.name === activeTab.value) ?? data.value.sheets[0] ?? null;
});

const colCount = computed<number>(() => {
  const sheet = activeSheet.value;
  if (!sheet || sheet.rows.length === 0) return 0;
  return Math.max(...sheet.rows.map((r) => r.length));
});

function colLabel(index: number): string {
  let label = "";
  let n = index;
  do {
    label = String.fromCharCode(65 + (n % 26)) + label;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return label;
}

function selectCell(ri: number, ci: number, value: string) {
  selectedCell.value = { label: `${colLabel(ci)}${ri + 1}`, value };
}

async function load() {
  if (!props.box || !props.path) return;
  loading.value = true;
  error.value = null;
  data.value = null;
  selectedCell.value = null;
  try {
    const result = await fetchExcelPreview(props.box, props.path, props.token);
    data.value = result;
    activeTab.value = result.active_sheet || result.sheets[0]?.name || "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

watch(() => [props.box, props.path], load);
watch(activeTab, () => { selectedCell.value = null; });
onMounted(load);
</script>

<template>
  <div class="excel-preview">
    <div v-if="loading" class="excel-loading">
      <NSpin size="large" />
    </div>

    <NAlert v-else-if="error" type="error" :title="error" style="margin: 16px" />

    <NAlert
      v-else-if="data?.file_too_large"
      type="warning"
      title="File too large to preview (> 50 MB)"
      style="margin: 16px"
    />

    <template v-else-if="data && data.sheets.length">
      <!-- Sheet tabs -->
      <NTabs
        v-if="data.sheets.length > 1"
        v-model:value="activeTab"
        type="card"
        size="small"
        class="excel-tabs"
      >
        <NTab v-for="sheet in data.sheets" :key="sheet.name" :name="sheet.name">
          {{ sheet.name }}
        </NTab>
      </NTabs>

      <!-- Formula bar -->
      <div class="excel-formula-bar">
        <span class="excel-cell-ref">{{ selectedCell?.label ?? "" }}</span>
        <span class="excel-cell-value">{{ selectedCell?.value ?? "" }}</span>
      </div>

      <div class="excel-scroll-container">
        <div
          v-if="activeSheet && (activeSheet.truncated_rows || activeSheet.truncated_cols)"
          class="excel-truncation-notice"
        >
          Preview limited to {{ activeSheet.rows.length }} rows
          <template v-if="activeSheet.truncated_cols"> × {{ colCount }} cols</template>
        </div>

        <table v-if="activeSheet && activeSheet.rows.length" class="excel-table">
          <thead>
            <tr>
              <th class="excel-row-num"></th>
              <th v-for="c in colCount" :key="c" class="excel-col-header">
                {{ colLabel(c - 1) }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, ri) in activeSheet.rows" :key="ri">
              <td class="excel-row-num">{{ ri + 1 }}</td>
              <td
                v-for="ci in colCount"
                :key="ci"
                class="excel-cell"
                :class="{ 'excel-cell--selected': selectedCell?.label === `${colLabel(ci - 1)}${ri + 1}` }"
                :title="row[ci - 1] ?? ''"
                @click="selectCell(ri, ci - 1, row[ci - 1] ?? '')"
              >{{ row[ci - 1] ?? "" }}</td>
            </tr>
          </tbody>
        </table>

        <div v-else-if="activeSheet" class="excel-empty">(empty sheet)</div>
      </div>
    </template>

    <div v-else-if="data" class="excel-empty">No sheets found.</div>
  </div>
</template>

<style scoped>
.excel-preview {
  display: flex;
  flex-direction: column;
  height: 75vh;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--stroke);
  border-radius: 8px;
}

.excel-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.excel-tabs {
  flex-shrink: 0;
  padding: 6px 8px 0;
  border-bottom: 1px solid var(--stroke);
}

/* Formula bar */
.excel-formula-bar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0;
  border-bottom: 1px solid var(--stroke);
  background: var(--surface);
  min-height: 28px;
}

.excel-cell-ref {
  flex-shrink: 0;
  width: 64px;
  padding: 3px 8px;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--muted);
  text-align: center;
  border-right: 1px solid var(--stroke);
  background: var(--surface-variant);
  user-select: none;
}

.excel-cell-value {
  flex: 1;
  padding: 3px 10px;
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-all;
  min-height: 22px;
  max-height: 80px;
  overflow-y: auto;
}

.excel-scroll-container {
  flex: 1;
  overflow: auto;
}

.excel-truncation-notice {
  padding: 4px 12px;
  font-size: 11px;
  color: var(--muted);
  background: var(--surface-variant);
  border-bottom: 1px solid var(--stroke);
  position: sticky;
  top: 0;
  z-index: 1;
}

.excel-table {
  border-collapse: collapse;
  font-size: 12px;
  font-family: var(--font-mono);
  white-space: nowrap;
  width: max-content;
  min-width: 100%;
}

.excel-table th,
.excel-table td {
  border: 1px solid var(--stroke);
  padding: 3px 8px;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.excel-col-header {
  background: var(--surface-variant);
  color: var(--muted);
  font-weight: 600;
  font-family: var(--font-mono);
  font-size: 11px;
  text-align: center;
  position: sticky;
  top: 0;
  z-index: 2;
  min-width: 80px;
}

.excel-row-num {
  background: var(--surface-variant);
  color: var(--muted);
  font-size: 11px;
  text-align: right;
  padding: 3px 6px;
  user-select: none;
  position: sticky;
  left: 0;
  z-index: 1;
  min-width: 36px;
  border-right: 2px solid var(--stroke);
}

thead .excel-row-num {
  z-index: 3;
}

.excel-cell {
  color: var(--text);
  background: var(--surface);
  cursor: pointer;
}

.excel-cell:hover {
  background: var(--surface-variant);
}

.excel-cell--selected {
  background: color-mix(in srgb, var(--accent) 15%, var(--surface)) !important;
  outline: 1px solid var(--accent);
  outline-offset: -1px;
}

.excel-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--muted);
  font-size: 13px;
}
</style>
