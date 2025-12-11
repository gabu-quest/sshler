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
      path: "/settings",
      name: "settings",
      component: () => import("@/views/SettingsView.vue"),
      meta: {
        title: "Settings",
        description: "Application preferences and configuration",
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
  scrollBehavior(to, from, savedPosition) {
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
router.beforeEach((to, from, next) => {
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
