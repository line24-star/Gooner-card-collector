import { describe, expect, it } from 'vitest'
import { buildTagTree, filterCounts, tagFilterIds, type TagNode } from './tagBrowser.ts'

const t = (id: number, name: string, parent_id: number | null = null, count = 0) => ({ id, name, parent_id, count })
const flat = (nodes: TagNode[]): number[] => nodes.flatMap((n) => [n.id, ...flat(n.children)])

describe('buildTagTree', () => {
  it('nests children under their parent', () => {
    const tree = buildTagTree([t(1, 'travel', null, 2), t(2, 'beach', 1, 5)])
    expect(tree).toHaveLength(1)
    expect(tree[0]).toMatchObject({ id: 1, name: 'travel', count: 2 })
    expect(tree[0].children.map((c) => c.id)).toEqual([2])
    expect(tree[0].children[0].count).toBe(5)
  })

  it('makes an orphan (missing parent) a root', () => {
    const tree = buildTagTree([t(5, 'lost', 99)])
    expect(tree.map((n) => n.id)).toEqual([5])
  })

  it('sorts roots and children by name', () => {
    const tree = buildTagTree([t(1, 'zoo'), t(2, 'art'), t(3, 'mid'), t(4, 'b', 1), t(5, 'a', 1)])
    expect(tree.map((n) => n.name)).toEqual(['art', 'mid', 'zoo'])
    expect(tree[2].children.map((n) => n.name)).toEqual(['a', 'b'])
  })

  it('terminates on a cycle and keeps every tag once (R7)', () => {
    const tree = buildTagTree([t(1, 'travel', 2), t(2, 'beach', 1)])
    expect(flat(tree).sort()).toEqual([1, 2])
  })

  it('handles a longer cycle plus a branch hanging off it', () => {
    const tree = buildTagTree([t(1, 'a', 3), t(2, 'b', 1), t(3, 'c', 2), t(4, 'd', 2)])
    expect(flat(tree).sort()).toEqual([1, 2, 3, 4])
  })

  it('treats a self-parent as a root', () => {
    const tree = buildTagTree([t(1, 'me', 1)])
    expect(tree.map((n) => n.id)).toEqual([1])
    expect(tree[0].children).toEqual([])
  })
})

describe('tagFilterIds', () => {
  it('includes the tag and its direct children only', () => {
    const tags = [t(1, 'travel'), t(2, 'beach', 1), t(3, 'sand', 2)]
    expect([...tagFilterIds(tags, 1)].sort()).toEqual([1, 2])
    expect([...tagFilterIds(tags, 3)]).toEqual([3])
  })
})

describe('filterCounts', () => {
  it('counts distinct cards a click shows, equal to the filter result', () => {
    const tags = [t(1, 'travel'), t(2, 'beach', 1), t(3, 'sand', 2)]
    const cards = [{ tagIds: [1, 2] }, { tagIds: [2] }, { tagIds: [3] }, { tagIds: [] }, { tagIds: [1] }]
    const m = filterCounts(tags, cards)
    expect(m.get(1)).toBe(3) // the card with both parent and child counts once
    expect(m.get(2)).toBe(3)
    expect(m.get(3)).toBe(1)
    for (const tag of tags) {
      const ids = tagFilterIds(tags, tag.id)
      expect(m.get(tag.id)).toBe(cards.filter((c) => c.tagIds.some((x) => ids.has(x))).length)
    }
  })
  it('self-parent and cycles do not double count', () => {
    const m = filterCounts([t(1, 'a', 1), t(2, 'b', 3), t(3, 'c', 2)], [{ tagIds: [1] }, { tagIds: [2, 3] }])
    expect([m.get(1), m.get(2), m.get(3)]).toEqual([1, 1, 1])
  })
})
