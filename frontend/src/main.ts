import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "./App.vue";
import router from "./router";
import "./assets/fonts.css";
import "./styles/main.css";
import { createI18n } from "./i18n";
import { useAppStore } from "./stores/app";
import { useBootstrapStore } from "./stores/bootstrap";
import { tokenWorks } from "./api/http";

async function initApp() {
  const app = createApp(App);
  const pinia = createPinia();

  app.use(pinia);
  app.use(router);
  createI18n(app);

  // Initialize app store for system preference detection
  const appStore = useAppStore();
  appStore.init();

  // Initialize bootstrap to get token BEFORE mounting
  const bootstrapStore = useBootstrapStore();

  // Clear a cached token the server no longer accepts
  const cachedToken = bootstrapStore.token;
  if (cachedToken && !(await tokenWorks(cachedToken))) {
    bootstrapStore.setToken(null);
  }

  await bootstrapStore.bootstrap();

  // Cleanup on app unmount
  const originalUnmount = app.unmount;
  app.unmount = function () {
      appStore.destroy();
      bootstrapStore.cleanup();
      originalUnmount.call(this);
  };

  app.mount("#app");
}

initApp();
