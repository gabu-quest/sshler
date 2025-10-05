import { createRouter, createWebHistory } from "vue-router";

const router = createRouter({
  history: createWebHistory("/app/"),
  routes: [
    {
      path: "/",
      name: "overview",
      component: () => import("@/views/OverviewView.vue"),
      meta: {
        title: "Overview",
        description: "SSH server overview and quick actions",
      },
    },
    {
      path: "/boxes",
      name: "boxes",
      component: () => import("@/views/BoxesView.vue"),
      meta: {
        title: "Boxes",
        description: "Manage SSH server connections",
      },
    },
    {
      path: "/files",
      name: "files",
      component: () => import("@/views/FilesView.vue"),
      meta: {
        title: "Files",
        description: "Browse and manage remote files",
      },
    },
    {
      path: "/terminal",
      name: "terminal",
      component: () => import("@/views/TerminalView.vue"),
      meta: {
        title: "Terminal",
        description: "Access remote terminal sessions",
      },
    },
    {
      path: "/multi-terminal",
      name: "multi-terminal",
      component: () => import("@/views/MultiTerminalView.vue"),
      meta: {
        title: "Multi-Terminal",
        description: "Multiple terminal sessions in a grid",
      },
    },
    {
      path: "/settings",
      name: "settings",
      component: () => import("@/views/SettingsView.vue"),
      meta: {
        title: "Settings",
        description: "Application preferences and configuration",
      },
    },
    {
      path: "/login",
      name: "login",
      component: () => import("@/views/LoginView.vue"),
      meta: {
        title: "Login",
        description: "Sign in to sshler",
        public: true, // No auth required for login page
      },
    },
    {
      path: "/:pathMatch(.*)*",
      name: "not-found",
      component: () => import("@/views/NotFoundView.vue"),
      meta: {
        title: "Page Not Found",
        description: "The requested page could not be found",
      },
    },
  ],
  scrollBehavior(to, _from, savedPosition) {
    // Restore scroll position when using browser back/forward
    if (savedPosition) {
      return savedPosition;
    }

    // Scroll to anchor if present
    if (to.hash) {
      return {
        el: to.hash,
        behavior: 'smooth',
      };
    }

    // Scroll to top for new routes
    return { top: 0 };
  },
});

// Navigation guards
router.beforeEach(async (to, _from, next) => {
  // Update document title
  const title = to.meta?.title as string;
  if (title) {
    document.title = `${title} - sshler`;
  } else {
    document.title = "sshler";
  }

  // Update meta description
  const description = to.meta?.description as string;
  if (description) {
    let metaDescription = document.querySelector('meta[name="description"]');
    if (!metaDescription) {
      metaDescription = document.createElement('meta');
      metaDescription.setAttribute('name', 'description');
      document.head.appendChild(metaDescription);
    }
    metaDescription.setAttribute('content', description);
  }

  // Authentication guard
  const isPublicRoute = to.meta?.public === true;

  if (!isPublicRoute) {
    try {
      // Check if server requires authentication
      const { useBootstrapStore } = await import('@/stores/bootstrap');
      const bootstrapStore = useBootstrapStore();

      // Ensure bootstrap is loaded
      if (!bootstrapStore.token && !bootstrapStore.loading) {
        await bootstrapStore.bootstrap();
      }

      // Check if basic auth is required
      if (bootstrapStore.basicAuthRequired) {
        const { useAuthStore } = await import('@/stores/auth');
        const authStore = useAuthStore();

        // If not authenticated, redirect to login
        if (!authStore.isAuthenticated) {
          next({
            name: 'login',
            query: { redirect: to.fullPath }
          });
          return;
        }
      }
    } catch (error) {
      console.error('Auth guard error:', error);
      // On error, allow navigation (fail open for better UX)
      // The API will reject the request if auth is actually required
    }
  }

  next();
});

// Handle navigation errors gracefully
router.onError((error) => {
  console.error('Router error:', error);

  // Redirect to overview on critical errors
  if (error.message.includes('Loading chunk')) {
    window.location.reload();
  }
});

export default router;
