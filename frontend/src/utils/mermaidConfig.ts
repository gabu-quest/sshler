import type { MermaidConfig } from "mermaid";

/**
 * The one mermaid configuration. The on-screen preview and the print/PDF
 * document both call `mermaid.initialize(mermaidConfig(theme))`, so they cannot
 * drift apart. Each caller keeps its own lazy `import("mermaid")`.
 *
 * Mermaid 12 changed three defaults: the layout engine (ELK instead of dagre),
 * the look (`neo` instead of `classic`) and the default theme. `layout` and
 * `look` are pinned here to keep diagrams looking as they did on mermaid 11.
 * `securityLevel: "strict"` is what sanitises labels; do not loosen it.
 */
export function mermaidConfig(theme: string): MermaidConfig {
  return {
    startOnLoad: false,
    securityLevel: "strict",
    layout: "dagre",
    look: "classic",
    flowchart: { minNodeWidth: 0 },
    state: { minNodeWidth: 0 },
    theme: theme === "dark" ? "dark" : "default",
  };
}
