<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch, nextTick } from "vue";

import { NButton, NIcon, NInput, NList, NListItem, NModal, NText } from "naive-ui";
import {
  PhCommand,
  PhFolderSimple,
  PhLightning,
  PhMoonStars,
  PhStar,
  PhSun,
  PhTerminal,
  PhGearSix,
  PhHouseLine,
  PhArrowClockwise,
  PhMagnifyingGlass,
} from "@phosphor-icons/vue";

import { useRouter } from "vue-router";
import { useAppStore } from "@/stores/app";
import { useI18n } from "@/i18n";

interface CommandAction {
  id: string;
  label: string;
  description?: string;
  icon: any;
  shortcut?: string;
  category: string;
  action: () => void;
  keywords?: string[];
}

const router = useRouter();
const appStore = useAppStore();
const { t } = useI18n();
const show = ref(false);
const query = ref("");
const selectedIndex = ref(0);

const actions = computed((): CommandAction[] => [
  {
    id: "nav-overview",
    label: t('palette.action_overview'),
    description: t('palette.action_overview_desc'),
    icon: PhHouseLine,
    shortcut: "Alt+H",
    category: t('nav.navigation'),
    action: () => router.push("/"),
    keywords: ["home", "dashboard", "main"],
  },
  {
    id: "nav-boxes",
    label: t('palette.action_boxes'),
    description: t('palette.action_boxes_desc'),
    icon: PhFolderSimple,
    shortcut: "Alt+B",
    category: t('nav.navigation'),
    action: () => router.push("/boxes"),
    keywords: ["servers", "ssh", "connections"],
  },
  {
    id: "nav-files",
    label: t('palette.action_files'),
    description: t('palette.action_files_desc'),
    icon: PhFolderSimple,
    shortcut: "Alt+F",
    category: t('nav.navigation'),
    action: () => router.push("/files"),
    keywords: ["browse", "sftp", "directory"],
  },
  {
    id: "nav-terminal",
    label: t('palette.action_terminal'),
    description: t('palette.action_terminal_desc'),
    icon: PhTerminal,
    shortcut: "Alt+T",
    category: t('nav.navigation'),
    action: () => router.push("/terminal"),
    keywords: ["shell", "tmux", "console"],
  },
  {
    id: "nav-settings",
    label: t('palette.action_settings'),
    description: t('palette.action_settings_desc'),
    icon: PhGearSix,
    shortcut: "Alt+S",
    category: t('nav.navigation'),
    action: () => router.push("/settings"),
    keywords: ["preferences", "config", "options"],
  },
  {
    id: "nav-favorites",
    label: t('palette.action_favorites'),
    description: t('palette.action_favorites_desc'),
    icon: PhStar,
    category: t('nav.navigation'),
    action: () => router.push({ path: "/files", hash: "#favorites" }),
    keywords: ["bookmarks", "starred", "saved", "file"],
  },
  {
    id: "theme-toggle",
    label: t('palette.action_toggle_theme'),
    description: t('palette.action_toggle_theme_desc'),
    icon: appStore.isDark ? PhSun : PhMoonStars,
    category: t('settings.appearance'),
    action: () => appStore.toggleTheme(),
    keywords: ["dark", "light", "appearance"],
  },
  {
    id: "theme-cycle",
    label: t('palette.action_cycle_theme'),
    description: t('palette.action_cycle_theme_desc'),
    icon: PhArrowClockwise,
    category: t('settings.appearance'),
    action: () => appStore.cycleTheme(),
    keywords: ["theme", "system", "auto"],
  },
  {
    id: "reload",
    label: t('palette.action_reload'),
    description: t('palette.action_reload_desc'),
    icon: PhLightning,
    category: "System",
    action: () => window.location.reload(),
    keywords: ["refresh", "restart"],
  },
  {
    id: "search-global",
    label: t('palette.action_search'),
    description: t('palette.action_search_desc'),
    icon: PhMagnifyingGlass,
    shortcut: "Ctrl+Shift+F",
    category: "Search",
    action: () => {
      // Global search coming soon
    },
    keywords: ["find", "locate"],
  },
]);

// Fuzzy search implementation
const fuzzyMatch = (text: string, query: string): { score: number; matches: number[] } => {
  const textLower = text.toLowerCase();
  const queryLower = query.toLowerCase();
  
  if (!queryLower) return { score: 0, matches: [] };
  
  let score = 0;
  let textIndex = 0;
  let queryIndex = 0;
  const matches: number[] = [];
  
  while (textIndex < textLower.length && queryIndex < queryLower.length) {
    if (textLower[textIndex] === queryLower[queryIndex]) {
      matches.push(textIndex);
      score += 1;
      queryIndex++;
    }
    textIndex++;
  }
  
  // Bonus for exact matches
  if (textLower.includes(queryLower)) {
    score += queryLower.length * 2;
  }
  
  // Bonus for word boundary matches
  if (textLower.startsWith(queryLower)) {
    score += queryLower.length * 3;
  }
  
  return queryIndex === queryLower.length ? { score, matches } : { score: 0, matches: [] };
};

const filtered = computed(() => {
  const q = query.value.trim();
  if (!q) return actions.value;
  
  const results = actions.value
    .map(action => {
      // Search in label, description, and keywords
      const labelMatch = fuzzyMatch(action.label, q);
      const descMatch = action.description ? fuzzyMatch(action.description, q) : { score: 0, matches: [] };
      const keywordMatch = action.keywords 
        ? Math.max(...action.keywords.map(k => fuzzyMatch(k, q).score))
        : 0;
      
      const totalScore = labelMatch.score + descMatch.score * 0.5 + keywordMatch * 0.3;
      
      return {
        ...action,
        score: totalScore,
        labelMatches: labelMatch.matches,
      };
    })
    .filter(action => action.score > 0)
    .sort((a, b) => b.score - a.score);
  
  return results;
});

const groupedActions = computed(() => {
  const groups: Record<string, typeof filtered.value> = {};
  
  filtered.value.forEach(action => {
    if (!groups[action.category]) {
      groups[action.category] = [];
    }
    groups[action.category]!.push(action);
  });
  
  return groups;
});

function activate(item: CommandAction) {
  show.value = false;
  query.value = "";
  selectedIndex.value = 0;
  item.action();
}

function handleKeydown(e: KeyboardEvent) {
  if (!show.value) return;
  
  const items = filtered.value;
  if (!items.length) return;

  switch (e.key) {
    case "ArrowDown":
      e.preventDefault();
      selectedIndex.value = Math.min(selectedIndex.value + 1, Math.max(items.length - 1, 0));
      break;
    case "ArrowUp":
      e.preventDefault();
      selectedIndex.value = Math.max(selectedIndex.value - 1, 0);
      break;
    case "Enter":
      e.preventDefault();
      const target = items[selectedIndex.value] ?? items[0];
      if (target) activate(target);
      break;
    case "Escape":
      e.preventDefault();
      show.value = false;
      query.value = "";
      selectedIndex.value = 0;
      break;
  }
}

function onGlobalKey(e: KeyboardEvent) {
  const cmdKey = e.metaKey || e.ctrlKey;

  if (cmdKey && e.key.toLowerCase() === "k") {
    e.preventDefault();
    show.value = !show.value;
    if (show.value) {
      query.value = "";
      selectedIndex.value = 0;
    }
  }
  
  // Global search shortcut
  if (cmdKey && e.shiftKey && e.key.toLowerCase() === "f") {
    e.preventDefault();
    show.value = true;
    query.value = "";
    selectedIndex.value = 0;
    // Focus on search action
    nextTick(() => {
      const searchAction = filtered.value.find(a => a.id === "search-global");
      if (searchAction) {
        const index = filtered.value.indexOf(searchAction);
        selectedIndex.value = index >= 0 ? index : 0;
      }
    });
  }
}

// Reset selection when query changes
watch(query, () => {
  selectedIndex.value = 0;
});

onMounted(() => {
  document.addEventListener("keydown", onGlobalKey);
  document.addEventListener("keydown", handleKeydown);
});

onBeforeUnmount(() => {
  document.removeEventListener("keydown", onGlobalKey);
  document.removeEventListener("keydown", handleKeydown);
});

watch(
  () => show.value,
  (open) => {
    if (open) {
      // Trap focus in modal
      document.body.style.overflow = "hidden";
      nextTick(() => {
        const input = document.querySelector<HTMLInputElement>("#cmd-input");
        input?.focus();
      });
    } else {
      document.body.style.overflow = "";
      query.value = "";
      selectedIndex.value = 0;
    }
  },
);
</script>

<template>
  <NButton
    quaternary
    circle
    size="small"
    @click="show = true"
    :aria-label="t('palette.open')"
    :title="t('palette.tooltip')"
  >
    <NIcon size="18" aria-hidden="true">
      <PhCommand />
    </NIcon>
  </NButton>
  
  <NModal 
    v-model:show="show" 
    preset="card" 
    style="max-width: 600px; max-height: 70vh;"
    :trap-focus="true"
    :mask-closable="true"
    :close-on-esc="true"
    role="dialog"
    aria-labelledby="palette-title"
    aria-describedby="palette-description"
  >
    <template #header>
      <div class="palette-header">
        <NIcon size="18" aria-hidden="true">
          <PhCommand />
        </NIcon>
        <span id="palette-title">{{ t('palette.title') }}</span>
      </div>
    </template>
    
    <div class="palette">
      <div class="palette-search">
        <NInput
          id="cmd-input"
          v-model:value="query"
          :placeholder="t('palette.placeholder')"
          autofocus
          clearable
          size="large"
          :aria-describedby="'palette-description'"
        />
        <div id="palette-description" class="sr-only">
          {{ t('palette.nav_help') }}
        </div>
      </div>
      
      <div class="palette-results" v-if="filtered.length > 0">
        <div v-for="(group, category) in groupedActions" :key="category" class="palette-group">
          <div class="palette-group-title">{{ category }}</div>
          <NList>
            <NListItem 
              v-for="item in group" 
              :key="item.id"
              @click="activate(item)" 
              class="palette-item"
              :class="{ 
                'palette-item-selected': filtered.indexOf(item) === selectedIndex 
              }"
              :aria-selected="filtered.indexOf(item) === selectedIndex"
              role="option"
            >
              <div class="palette-item-content">
                <div class="palette-item-main">
                  <NIcon size="16" aria-hidden="true" class="palette-item-icon">
                    <component :is="item.icon" />
                  </NIcon>
                  <div class="palette-item-text">
                    <div class="palette-item-label">{{ item.label }}</div>
                    <div v-if="item.description" class="palette-item-description">
                      {{ item.description }}
                    </div>
                  </div>
                </div>
                <div v-if="item.shortcut" class="palette-item-shortcut">
                  <NText depth="3" size="small">{{ item.shortcut }}</NText>
                </div>
              </div>
            </NListItem>
          </NList>
        </div>
      </div>
      
      <div v-else-if="query && filtered.length === 0" class="palette-empty">
        <NIcon size="24" aria-hidden="true">
          <PhMagnifyingGlass />
        </NIcon>
        <div class="palette-empty-text">
          <div>{{ t('palette.no_results') }}</div>
          <div class="text-muted">{{ t('palette.no_results_hint') }}</div>
        </div>
      </div>
      
      <div v-else class="palette-help">
        <div class="palette-help-section">
          <strong>{{ t('palette.nav_label') }}</strong> {{ t('palette.nav_detail') }}
        </div>
        <div class="palette-help-section">
          <strong>{{ t('palette.shortcuts_label') }}</strong> {{ t('palette.shortcuts_detail') }}
        </div>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.palette {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-height: 60vh;
  overflow: hidden;
}

.palette-header {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.palette-search {
  position: relative;
}

.palette-results {
  overflow-y: auto;
  max-height: 400px;
}

.palette-group {
  margin-bottom: 16px;
}

.palette-group:last-child {
  margin-bottom: 0;
}

.palette-group-title {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--muted);
  margin-bottom: 8px;
  padding: 0 12px;
}

.palette-item {
  cursor: pointer;
  border-radius: 8px;
  margin: 2px 0;
  transition: all 0.15s ease;
  border: 1px solid transparent;
}

.palette-item:hover,
.palette-item-selected {
  background: rgba(124, 93, 255, 0.1);
  border-color: rgba(124, 93, 255, 0.2);
}

.palette-item-selected {
  background: rgba(124, 93, 255, 0.15);
  border-color: rgba(124, 93, 255, 0.3);
}

.palette-item-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 8px 12px;
}

.palette-item-main {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 0;
}

.palette-item-icon {
  flex-shrink: 0;
  color: var(--muted);
}

.palette-item-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.palette-item-label {
  font-weight: 500;
  color: var(--text);
}

.palette-item-description {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.3;
}

.palette-item-shortcut {
  flex-shrink: 0;
  padding: 2px 6px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 4px;
  font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Roboto Mono', monospace;
  font-size: 11px;
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.palette-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 32px 16px;
  color: var(--muted);
  text-align: center;
}

.palette-empty-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.palette-help {
  padding: 16px;
  background: rgba(255, 255, 255, 0.02);
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.05);
}

.palette-help-section {
  font-size: 13px;
  color: var(--muted);
  margin-bottom: 8px;
}

.palette-help-section:last-child {
  margin-bottom: 0;
}

.palette-help-section strong {
  color: var(--text);
}

/* Screen reader only content */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* Reduced motion support */
@media (prefers-reduced-motion: reduce) {
  .palette-item {
    transition: none;
  }
}

/* High contrast support */
@media (prefers-contrast: high) {
  .palette-item {
    border-color: currentColor;
  }
  
  .palette-item-selected {
    background: var(--accent);
    color: white;
  }
  
  .palette-item-shortcut {
    border-color: currentColor;
  }
}

/* Mobile optimizations */
@media (max-width: 768px) {
  .palette {
    max-height: 70vh;
  }
  
  .palette-item-content {
    padding: 12px;
  }
  
  .palette-item-main {
    gap: 16px;
  }
  
  .palette-item-shortcut {
    display: none; /* Hide shortcuts on mobile for cleaner UI */
  }
}
</style>
