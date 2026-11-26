/**
 * The one owner of a path's shape on the page: its root, its parent, its segments.
 *
 * Paths here are in the form the box answers them: POSIX (`/home/me`), home-relative
 * (`~`, `~/x`), a Windows drive (`C:/Users/me`) or a share (`//server/share/dir`).
 * `\` separates segments only in a drive or share path; in a POSIX path it is an
 * ordinary character of a name.
 */

export interface PathSegment {
  label: string
  path: string
}

interface Split {
  /** The root as written in the answer, ending in `/` for a drive or share; "" if none. */
  root: string
  /** What the crumb for the root says. */
  rootLabel: string
  /** The names after the root. */
  names: string[]
}

/** A drive: a letter and a colon, then a separator or the end. The one spelling. */
const DRIVE = /^[A-Za-z]:(?:[\\/]|$)/
const SHARE = /^(?:\\\\|\/\/)[^\\/]/

/** A drive-letter (`C:\`, `C:/`) or UNC (`\\server\`) path. */
export const isWindowsPath = (p: string): boolean => DRIVE.test(p) || p.startsWith('\\\\')

const splitPath = (input: string): Split => {
  let p = input || ''
  if (isWindowsPath(p)) p = p.replace(/\\/g, '/')

  if (DRIVE.test(p)) {
    const root = `${p.slice(0, 2)}/`
    return { root, rootLabel: root, names: p.slice(2).split('/').filter(Boolean) }
  }
  if (SHARE.test(p)) {
    const [server = '', share = '', ...names] = p.slice(2).split('/').filter(Boolean)
    const label = share ? `//${server}/${share}` : `//${server}`
    return { root: share ? `${label}/` : label, rootLabel: label, names: share ? names : [] }
  }
  if (p === '~' || p.startsWith('~/')) {
    return { root: '~', rootLabel: '~', names: p.slice(1).split('/').filter(Boolean) }
  }
  if (p.startsWith('/')) {
    return { root: '/', rootLabel: '/', names: p.split('/').filter(Boolean) }
  }
  return { root: '', rootLabel: '', names: p.split('/').filter(Boolean) }
}

const join = (root: string, names: string[]): string => {
  if (!names.length) return root
  if (!root) return names.join('/')
  return root.endsWith('/') ? `${root}${names.join('/')}` : `${root}/${names.join('/')}`
}

/** True for a path with nothing above it: `/`, `~`, `C:/`, `//server/share/`. */
export const isPathRoot = (path: string): boolean => {
  const s = splitPath(path)
  return s.root !== '' && s.names.length === 0
}

/** The folder above *path*, or null at a root (and for a bare relative name). */
export const parentPath = (path: string): string | null => {
  const s = splitPath(path)
  if (!s.names.length) return null
  const above = s.names.slice(0, -1)
  if (!s.root && !above.length) return null
  return join(s.root, above)
}

/** The crumbs from the root down to *path*, each with the path it opens. */
export const pathSegments = (path: string): PathSegment[] => {
  const s = splitPath(path)
  const out: PathSegment[] = []
  if (s.root) out.push({ label: s.rootLabel, path: s.root })
  s.names.forEach((name, i) => {
    out.push({ label: name, path: join(s.root, s.names.slice(0, i + 1)) })
  })
  return out
}

/** True for POSIX (`/x`), Windows drive (`C:\x` / `C:/x`), or UNC (`\\srv`) absolutes. */
export const isAbsolutePath = (p: string): boolean => p.startsWith('/') || isWindowsPath(p)

/** Fold `.` and `..` into *names* under *root*; `..` at the root stays at the root. */
const fold = (root: string, names: string[], more: string[] = []): string => {
  const out: string[] = []
  for (const seg of [...names, ...more]) {
    if (seg === '.' || seg === '') continue
    if (seg === '..') { out.pop(); continue }
    out.push(seg)
  }
  return join(root || '/', out)
}

/** Collapse `.`/`..` segments, keeping the root (`C:/`, `//server/share/`, `~`, `/`).
 *  Output uses forward slashes: the local-Windows file API speaks POSIX (`C:/Users/x`). */
export const normalizeAbsolute = (absPath: string): string => {
  const s = splitPath(absPath)
  return fold(s.root, s.names)
}

/** Resolve a (possibly relative) path against a base directory. Handles POSIX,
 *  home-relative, drive and share bases, and never force-prepends `/` to a rooted path. */
export const resolvePathAgainst = (rawPath: string, base: string): string => {
  if (isAbsolutePath(rawPath)) return normalizeAbsolute(rawPath)
  const s = splitPath(base)
  const winBase = isWindowsPath(base)
  return fold(s.root, s.names, rawPath.split(winBase ? /[/\\]/ : '/'))
}
