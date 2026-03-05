<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from 'vue'
import {
  NDrawer, NDrawerContent, NButton, NIcon, NInput, NInputNumber,
  NEmpty, NSpace, NTag, NPopconfirm, NRadioGroup, NRadio, NSpin,
  useMessage,
} from 'naive-ui'
import { PhTrash, PhPlus } from '@phosphor-icons/vue'
import { useTunnelsStore } from '@/stores/tunnels'
import { useBootstrapStore } from '@/stores/bootstrap'

interface Props {
  show: boolean
  boxName: string
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
}>()

const message = useMessage()
const tunnelsStore = useTunnelsStore()
const bootstrapStore = useBootstrapStore()

const token = computed(() => bootstrapStore.token || bootstrapStore.payload?.token || null)

const showAddForm = ref(false)
const creating = ref(false)

// Add form state
const newType = ref<'local' | 'remote'>('local')
const newLocalHost = ref('127.0.0.1')
const newLocalPort = ref<number | null>(null)
const newRemoteHost = ref('127.0.0.1')
const newRemotePort = ref<number | null>(null)

// Auto-refresh timer
let refreshTimer: ReturnType<typeof setInterval> | null = null

const startRefresh = () => {
  stopRefresh()
  refreshTimer = setInterval(() => {
    if (props.show && props.boxName) {
      tunnelsStore.load(props.boxName, token.value)
    }
  }, 10_000)
}

const stopRefresh = () => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}

watch(() => props.show, (visible) => {
  if (visible) {
    tunnelsStore.load(props.boxName, token.value)
    startRefresh()
  } else {
    stopRefresh()
  }
})

onUnmounted(stopRefresh)

const close = () => emit('update:show', false)

const resetAddForm = () => {
  newType.value = 'local'
  newLocalHost.value = '127.0.0.1'
  newLocalPort.value = null
  newRemoteHost.value = '127.0.0.1'
  newRemotePort.value = null
  showAddForm.value = false
}

const handleAdd = async () => {
  if (!newLocalPort.value || !newRemotePort.value) return
  creating.value = true
  const result = await tunnelsStore.create(
    props.boxName,
    {
      tunnel_type: newType.value,
      local_host: newLocalHost.value,
      local_port: newLocalPort.value,
      remote_host: newRemoteHost.value,
      remote_port: newRemotePort.value,
    },
    token.value,
  )
  creating.value = false
  if (result) {
    message.success('Tunnel created')
    resetAddForm()
  } else {
    message.error(tunnelsStore.error || 'Failed to create tunnel')
  }
}

const handleDelete = async (tunnelId: string) => {
  const ok = await tunnelsStore.remove(props.boxName, tunnelId, token.value)
  if (ok) {
    message.success('Tunnel closed')
  } else {
    message.error(tunnelsStore.error || 'Failed to close tunnel')
  }
}

const typeLabel = (t: string) => t === 'local' ? 'L' : 'R'
const typeColor = (t: string) => t === 'local' ? 'success' : 'warning'
</script>

<template>
  <NDrawer :show="props.show" width="min(400px, calc(100vw - 16px))" placement="right" @update:show="emit('update:show', $event)">
    <NDrawerContent title="Port Forwarding" closable>
      <template #header-extra>
        <NButton size="small" type="primary" aria-label="Add tunnel" @click="showAddForm = !showAddForm">
          <NIcon size="14"><PhPlus weight="bold" /></NIcon>
        </NButton>
      </template>

      <!-- Add form -->
      <div v-if="showAddForm" class="tunnel-form" @keydown.esc.stop="resetAddForm">
        <NRadioGroup v-model:value="newType" size="small">
          <NRadio value="local">Local (-L) &mdash; access remote port locally</NRadio>
          <NRadio value="remote">Remote (-R) &mdash; expose local port remotely</NRadio>
        </NRadioGroup>

        <div class="tunnel-ports-row">
          <div class="tunnel-port-group">
            <label class="tunnel-port-label">Local</label>
            <NInput v-model:value="newLocalHost" size="small" placeholder="127.0.0.1" />
            <NInputNumber v-model:value="newLocalPort" size="small" placeholder="Port" :min="1" :max="65535" :show-button="false" />
          </div>
          <span class="tunnel-arrow">&#8596;</span>
          <div class="tunnel-port-group">
            <label class="tunnel-port-label">Remote</label>
            <NInput v-model:value="newRemoteHost" size="small" placeholder="127.0.0.1" />
            <NInputNumber v-model:value="newRemotePort" size="small" placeholder="Port" :min="1" :max="65535" :show-button="false" />
          </div>
        </div>

        <NSpace size="small">
          <NButton
            size="small"
            type="primary"
            @click="handleAdd"
            :disabled="!newLocalPort || !newRemotePort"
            :loading="creating"
          >
            Create
          </NButton>
          <NButton size="small" @click="resetAddForm">Cancel</NButton>
        </NSpace>
      </div>

      <!-- Loading state -->
      <div v-if="tunnelsStore.loading && tunnelsStore.items.length === 0" class="tunnel-loading">
        <NSpin size="small" />
      </div>

      <!-- Error state -->
      <div v-else-if="tunnelsStore.error && tunnelsStore.items.length === 0" class="tunnel-empty">
        <NEmpty :description="tunnelsStore.error">
          <template #extra>
            <NButton size="small" @click="tunnelsStore.load(props.boxName, token)">Retry</NButton>
          </template>
        </NEmpty>
      </div>

      <!-- Empty state -->
      <div v-else-if="tunnelsStore.items.length === 0" class="tunnel-empty">
        <NEmpty description="No active tunnels" />
      </div>

      <!-- Tunnel list -->
      <div
        v-for="tunnel in tunnelsStore.items"
        :key="tunnel.id"
        class="tunnel-item"
      >
        <div class="tunnel-info">
          <NTag size="small" :type="typeColor(tunnel.tunnel_type)">
            {{ typeLabel(tunnel.tunnel_type) }}
          </NTag>
          <span class="tunnel-endpoint">
            {{ tunnel.local_host }}:{{ tunnel.local_port }}
          </span>
          <span class="tunnel-arrow-small">&#8596;</span>
          <span class="tunnel-endpoint">
            {{ tunnel.remote_host }}:{{ tunnel.remote_port }}
          </span>
        </div>
        <NPopconfirm @positive-click="handleDelete(tunnel.id)">
          <template #trigger>
            <NButton size="tiny" quaternary aria-label="Close tunnel">
              <NIcon size="14"><PhTrash weight="duotone" /></NIcon>
            </NButton>
          </template>
          Close this tunnel?
        </NPopconfirm>
      </div>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.tunnel-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  background: var(--surface-variant);
  border-radius: 6px;
  margin-bottom: 12px;
}

.tunnel-ports-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tunnel-port-group {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tunnel-port-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
}

.tunnel-arrow {
  font-size: 18px;
  color: var(--muted);
  margin-top: 16px;
}

.tunnel-loading {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}

.tunnel-empty {
  padding: 24px 0;
}

.tunnel-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-radius: 6px;
  border: 1px solid var(--stroke);
  margin-bottom: 6px;
}

.tunnel-item:hover {
  border-color: var(--accent);
}

.tunnel-info {
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: var(--font-mono);
  font-size: 13px;
}

.tunnel-endpoint {
  white-space: nowrap;
}

.tunnel-arrow-small {
  color: var(--muted);
}
</style>
