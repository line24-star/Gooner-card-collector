import { describe, expect, it } from 'vitest'
import { loadArticles, parseArticle, searchArticles, sortArticles } from './content.ts'

const RAW = `---
title: Hello there
category: Basics
order: 5
---
Body **text**.`

describe('parseArticle', () => {
  it('reads frontmatter, id and kind from path', () => {
    const a = parseArticle('./content/faq/hello.md', RAW)
    expect(a).toEqual({ id: 'hello', kind: 'faq', title: 'Hello there', category: 'Basics', order: 5, body: 'Body **text**.' })
  })
  it('falls back to defaults without frontmatter', () => {
    const a = parseArticle('./content/guides/plain.md', 'Just text')
    expect(a).toMatchObject({ id: 'plain', kind: 'guide', title: 'plain', category: 'General', order: 100 })
  })
})

describe('search and sort', () => {
  const list = [
    parseArticle('./content/faq/b.md', '---\ntitle: Packs\ncategory: Z\norder: 1\n---\ncoins'),
    parseArticle('./content/faq/a.md', '---\ntitle: Tags\ncategory: A\norder: 2\n---\nlabels'),
  ]
  it('sorts by category then order', () => {
    expect(sortArticles(list).map((a) => a.id)).toEqual(['a', 'b'])
  })
  it('matches all words across title and body', () => {
    expect(searchArticles(list, 'packs coins').map((a) => a.id)).toEqual(['b'])
    expect(searchArticles(list, '').length).toBe(2)
  })
})

describe('bundled content', () => {
  it('every article has a real title and body', () => {
    const all = loadArticles()
    expect(all.length).toBeGreaterThan(0)
    for (const a of all) {
      expect(a.title, a.id).not.toBe(a.id)
      expect(a.body.length, a.id).toBeGreaterThan(0)
    }
  })
  it('every #help: link points at an existing article', () => {
    const ids = new Set(loadArticles().map((a) => a.id))
    for (const a of loadArticles()) {
      for (const [, id] of a.body.matchAll(/\(#help:([\w-]+)\)/g)) expect(ids.has(id), `${a.id} → ${id}`).toBe(true)
    }
  })
})
