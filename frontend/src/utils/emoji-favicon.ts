/**
 * Emoji Favicon Utility
 *
 * Generates a deterministic emoji favicon based on a string (like directory name).
 * Uses a hash of the string to pick from a curated list of visually distinct emojis.
 */

// Curated list of visually distinct emojis that look good as favicons
const EMOJIS = [
  // Animals (30)
  '🦊', '🐼', '🦁', '🐸', '🦉', '🦋', '🐙', '🦈', '🐢', '🦄',
  '🐳', '🦩', '🦜', '🐝', '🦎', '🐲', '🦚', '🦀', '🐬', '🦅',
  '🐺', '🦇', '🐧', '🦔', '🐨', '🦥', '🦫', '🐹', '🦘', '🦬',
  // Nature & Weather (25)
  '🌸', '🌺', '🌻', '🍀', '🌴', '🌵', '🍄', '🌙', '⭐', '🌈',
  '❄️', '🔥', '💧', '🌊', '⚡', '☀️', '🌕', '🍁', '🌿', '🌾',
  '🪻', '🪷', '🌲', '🏔️', '🌋',
  // Food & Drink (30)
  '🍎', '🍊', '🍋', '🍇', '🍓', '🥑', '🌶️', '🍕', '🍔', '🧁',
  '🍩', '🍪', '🍦', '🧀', '🥐', '🍿', '🥝', '🍑', '🥭', '🫐',
  '🍒', '🍍', '🥥', '🌽', '🥕', '🧅', '🥨', '🥯', '🧇', '🥞',
  // Objects & Tech (30)
  '💎', '🎯', '🎨', '🎭', '🎪', '🎸', '🎺', '🎲', '🧩', '🔮',
  '💡', '🔧', '⚙️', '🧲', '🧪', '🔬', '📡', '🛸', '🚀', '⚓',
  '🎹', '🎻', '🎤', '📷', '💾', '📱', '⌚', '🔑', '🧭', '⏰',
  // Symbols & Accents (23)
  '💜', '💙', '💚', '💛', '🧡', '❤️', '🖤', '💗', '💝', '💫',
  '✨', '🌟', '💥', '🎆', '🎇', '🔶', '🔵', '🟥', '🟩',
  '♠️', '♥️', '♦️', '♣️',
  // Distinctive extras (12)
  '🏰', '🗼', '🧊', '🪐', '🛡️', '🎩',
  '🪁', '🦞', '🦑', '🐊', '🦦', '🪺',
];

/**
 * FNV-1a hash function - better distribution than simple hash
 * Uses 32-bit FNV prime and offset basis
 */
function fnv1aHash(str: string): number {
  let hash = 2166136261; // FNV offset basis
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash = Math.imul(hash, 16777619); // FNV prime
  }
  return hash >>> 0; // Convert to unsigned 32-bit
}

/**
 * Get a deterministic emoji for a given string
 */
export function getEmojiForString(str: string): string {
  if (!str) return '📁'; // Default for empty string
  const hash = fnv1aHash(str);
  return EMOJIS[hash % EMOJIS.length];
}

/**
 * Create a canvas-based favicon with an emoji
 */
export function createEmojiFavicon(emoji: string): string {
  const canvas = document.createElement('canvas');
  canvas.width = 32;
  canvas.height = 32;
  const ctx = canvas.getContext('2d');

  if (!ctx) return '';

  // Clear canvas
  ctx.clearRect(0, 0, 32, 32);

  // Draw emoji centered
  ctx.font = '26px serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(emoji, 16, 18);

  return canvas.toDataURL('image/png');
}

/**
 * Set the page favicon to an emoji based on a string
 */
export function setEmojiFavicon(str: string): void {
  const emoji = getEmojiForString(str);
  const faviconUrl = createEmojiFavicon(emoji);

  if (!faviconUrl) return;

  // Find or create favicon link element
  let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!link) {
    link = document.createElement('link');
    link.rel = 'icon';
    document.head.appendChild(link);
  }

  link.href = faviconUrl;
}

/**
 * Reset favicon to the default
 */
export function resetFavicon(): void {
  const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (link) {
    link.href = '/app/favicon.png';
  }
}
