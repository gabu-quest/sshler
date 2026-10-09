import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect } from 'vitest'
import { generateSessionName } from './sessionName'

// Shared with tests/test_session_name_parity.py (ts_session_name).
// Mutation killed: reverting the unicode-aware filter in generateSessionName
// (back to [^A-Za-z0-9_-]) turns the 日本語 / café vectors red; dropping the
// `.`/`:` replacement turns the dotted vectors red.
const vectors: { directory: string; expected: string }[] = JSON.parse(
  readFileSync(resolve(__dirname, '../../../tests/fixtures/session_name_vectors.json'), 'utf-8'),
)

describe('generateSessionName(local) matches Python ts_session_name golden vectors', () => {
  // Mirrors _MUST_HAVE in tests/test_session_name_parity.py. Mutation killed:
  // deleting a vector class from the shared file, which would let parity pass
  // without covering astral emoji, CJK, accents or dots.
  it.each([
    ['/srv/a😀b', 'a_b'],
    ['/srv/日本語', '日本語'],
    ['/srv/café', 'café'],
    ['/srv/.hidden', '_hidden'],
    ['/srv/ver 1.2.3', 'ver_1_2_3'],
    ['..', 'home'],
    ['/srv/a\\b', 'a_b'],
    ['C:\\Users\\x\\proj', 'proj'],
    ['\\\\server\\share\\proj', 'proj'],
  ])('shared file contains the must-have vector %j -> %j', (directory, expected) => {
    expect(vectors).toContainEqual({ directory, expected })
  })

  it.each(vectors.map((v) => [v.directory, v.expected]))('%j -> %j', (directory, expected) => {
    expect(generateSessionName(directory, 'local')).toBe(expected)
  })
})
