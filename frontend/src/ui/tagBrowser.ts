/** Tag and category browser: pure tree building plus the panel markup. */

import { esc } from '../cards/card.ts'

export interface TagInput { id: number; name: string; parent_id: number | null; count: number }
export interface TagNode { id: number; name: string; count: number; children: TagNode[] }

const byName = (a: TagInput, b: TagInput) => a.name.localeCompare(b.name) || a.id - b.id

/**
 * Nest tags under their parents. Orphans and self-parents become roots. Parent cycles are broken:
 * every tag appears exactly once (the alphabetically first tag of a cycle becomes its root).
 */
export function buildTagTree(tags: TagInput[]): TagNode[] {
  const ids = new Set(tags.map((t) => t.id))
  const kids = new Map<number, TagInput[]>()
  const roots: TagInput[] = []
  for (const t of tags) {
    if (t.parent_id !== null && t.parent_id !== t.id && ids.has(t.parent_id)) {
      const list = kids.get(t.parent_id)
      if (list) list.push(t); else kids.set(t.parent_id, [t])
    } else roots.push(t)
  }
  const seen = new Set<number>()
  const build = (t: TagInput): TagNode => {
    seen.add(t.id)
    const children = (kids.get(t.id) ?? []).sort(byName).map((c) => (seen.has(c.id) ? null : build(c)))
    return { id: t.id, name: t.name, count: t.count, children: children.filter((c): c is TagNode => c !== null) }
  }
  const out = roots.sort(byName).map(build)
  // Whatever is still unseen sits on a parent cycle: promote the first of each, in name order.
  for (const t of [...tags].sort(byName)) if (!seen.has(t.id)) out.push(build(t))
  return out.sort((a, b) => a.name.localeCompare(b.name) || a.id - b.id)
}

/** Ids a tag filter matches: the tag plus its direct children (the server's one-level rule). */
export function tagFilterIds(tags: { id: number; parent_id: number | null }[], id: number): Set<number> {
  const out = new Set<number>([id])
  for (const t of tags) if (t.parent_id === id) out.add(t.id)
  return out
}

/** Per tag: how many distinct cards a click on it shows (the tag's own cards plus direct children's). */
export function filterCounts(tags: { id: number; parent_id: number | null }[], cards: { tagIds: number[] }[]): Map<number, number> {
  const parent = new Map(tags.map((t) => [t.id, t.parent_id]))
  const out = new Map<number, number>(tags.map((t) => [t.id, 0]))
  for (const c of cards) {
    const hit = new Set<number>()
    for (const id of c.tagIds) {
      if (!parent.has(id)) continue
      hit.add(id)
      const p = parent.get(id)
      if (p != null && p !== id && parent.has(p)) hit.add(p)
    }
    for (const id of hit) out.set(id, (out.get(id) ?? 0) + 1)
  }
  return out
}

export function tagTreeHTML(nodes: TagNode[], activeId: number | null): string {
  const item = (n: TagNode): string => `
    <li>
      <button class="tag-item" data-tag="${n.id}" aria-pressed="${n.id === activeId}">
        <span class="tag-name">${esc(n.name)}</span><span class="tag-count">${n.count}</span>
      </button>
      ${n.children.length ? `<ul>${n.children.map(item).join('')}</ul>` : ''}
    </li>`
  return nodes.length ? `<ul class="tag-tree">${nodes.map(item).join('')}</ul>` : '<p class="muted tag-empty">No tags yet. Tag a card and it shows up here.</p>'
}
