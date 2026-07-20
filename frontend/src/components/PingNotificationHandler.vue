<script setup lang="ts">
import { h, onUnmounted, watch } from "vue";
import { useNotification } from "naive-ui";
import type { NotificationReactive, NotificationType } from "naive-ui";
import { usePingStore } from "@/stores/ping";
import { useAppStore } from "@/stores/app";

const pingStore = usePingStore();
const appStore = useAppStore();
const notification = useNotification();

// Active notification handles keyed by ping id — lets us programmatically
// close a notification when another tab broadcasts a dismiss.
const handles = new Map<string, NotificationReactive>();

// Guard set: when WE call handle.destroy() in response to a broadcast, we
// add the id here so the onClose callback doesn't re-broadcast the dismiss.
const suppressBroadcast = new Set<string>();

const channel = new BroadcastChannel("sshler:ping");

channel.onmessage = (evt: MessageEvent) => {
  const { type, id } = evt.data ?? {};
  if (type === "dismiss" && id) {
    const handle = handles.get(id);
    if (handle) {
      suppressBroadcast.add(id);
      handle.destroy();
      // suppressBroadcast entry is cleared in onClose below
    }
  }
};

onUnmounted(() => {
  channel.close();
});

watch(
  () => pingStore.pendingPings,
  () => {
    const pings = pingStore.drainPings();
    for (const ping of pings) {
      const effectiveDuration = ping.duration ?? appStore.pingDefaultDuration ?? undefined;
      const handle = notification.create({
        title: ping.title,
        content: ping.body ?? undefined,
        type: ((ping.color ?? "info") as NotificationType),
        duration: effectiveDuration,
        closable: true,
        meta: ping.source ? `from ${ping.source}` : undefined,
        avatar: ping.icon
          ? () => h("span", { style: "font-size:1.4em;line-height:1" }, ping.icon!)
          : undefined,
        onClose: () => {
          handles.delete(ping.id);
          if (!suppressBroadcast.has(ping.id)) {
            channel.postMessage({ type: "dismiss", id: ping.id });
          }
          suppressBroadcast.delete(ping.id);
        },
      });
      handles.set(ping.id, handle);
    }
  },
);
</script>

<template></template>
