import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "./App.vue";
import router from "./router";
import "./styles/main.css";
import { createI18n } from "./i18n";
import { useAppStore } from "./stores/app";

const app = createApp(App);
const pinia = createPinia();

app.use(pinia);
app.use(router);
createI18n(app);

// Initialize app store for system preference detection
const appStore = useAppStore();
appStore.init();

// Cleanup on app unmount
const originalUnmount = app.unmount;
app.unmount = function () {
    appStore.destroy();
    originalUnmount.call(this);
};

app.mount("#app");
