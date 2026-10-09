import { describe, it, expect } from 'vitest'
import { fnv1aHash } from './emoji-favicon'
import { generateSessionName, getColorForSession } from './sessionName'

describe('generateSessionName (local: ts parity)', () => {
  it('matches ts naming: basename, hyphens preserved, no hash', () => {
    // The whole point: sshler's local session == what `ts` creates, so `ts`
    // can attach the same session from a plain terminal if sshler is down.
    expect(generateSessionName('/work/repos/my-project', 'local')).toBe('my-project')
    expect(generateSessionName('/work/projects/cli-tool', 'local')).toBe('cli-tool')
  })

  it('applies the ts rule (. and : → _) plus the backend safe filter', () => {
    expect(generateSessionName('/srv/my.app', 'local')).toBe('my_app')
    expect(generateSessionName('/srv/my project (v2)', 'local')).toBe('my_project__v2_')
  })

  it('same basename in different dirs SHARES one session (intentional, ts behavior)', () => {
    expect(generateSessionName('/a/b/folder', 'local')).toBe('folder')
    expect(generateSessionName('/x/y/folder', 'local')).toBe('folder')
  })

  it('maps home / empty to "home"', () => {
    expect(generateSessionName('~', 'local')).toBe('home')
  })
})

describe('generateSessionName (remote: collision-safe hash)', () => {
  it('does NOT collide for same final dir name in different paths', () => {
    const a = generateSessionName('/work/a/b/c/d/folder', 'web')
    const b = generateSessionName('/work/x/y/z/folder', 'web')
    expect(a).not.toBe(b)
    expect(a.startsWith('folder-')).toBe(true)
    expect(b.startsWith('folder-')).toBe(true)
  })

  it('is deterministic — same box+path always yields the same name', () => {
    expect(generateSessionName('/srv/app/frontend', 'web')).toBe(
      generateSessionName('/srv/app/frontend', 'web'),
    )
  })

  it('disambiguates the same path across different boxes', () => {
    expect(generateSessionName('/srv/app', 'web1')).not.toBe(
      generateSessionName('/srv/app', 'web2'),
    )
  })
})

// User decision 2026-10-08: `\` separates path segments only in a
// Windows-looking path (drive letter or UNC); in a POSIX path it is an ordinary
// character, so `/srv/a\b` names the same session as ts_session_name ("a_b").
describe('generateSessionName: backslash is a separator only in Windows paths', () => {
  // Mutation killed: splitting on `\` regardless of the path's shape (the old
  // /[/\\]/ split) turns the POSIX cases red ("b" instead of "a_b").
  it.each([
    ['local', '/srv/a\\b', 'a_b'],
    ['web', '/srv/a\\b', `a_b-${fnv1aHash('web::/srv/a\\b').toString(36).slice(0, 4)}`],
  ])('POSIX path with a backslash on %s: %j -> %j', (box, directory, expected) => {
    expect(generateSessionName(directory, box)).toBe(expected)
  })

  // Mutation killed: never splitting on `\` turns these red (local gives
  // "C__Users_x_proj", remote "C__Users_x_proj-<hash>").
  it.each([
    ['local', 'C:\\Users\\x\\proj', 'proj'],
    ['local', 'c:\\work\\repo\\', 'repo'],
    ['local', '\\\\server\\share\\proj', 'proj'],
    ['local', 'C:/Users/x/proj', 'proj'],
    ['web', 'C:\\Users\\x\\proj', `proj-${fnv1aHash('web::C:\\Users\\x\\proj').toString(36).slice(0, 4)}`],
  ])('Windows path on %s: %j -> %j', (box, directory, expected) => {
    expect(generateSessionName(directory, box)).toBe(expected)
  })
})

describe('getColorForSession', () => {
  it('is deterministic for the same key', () => {
    expect(getColorForSession('my-project')).toBe(getColorForSession('my-project'))
  })

  it('returns a hex color', () => {
    expect(getColorForSession('anything')).toMatch(/^#[0-9a-f]{6}$/)
  })
})
