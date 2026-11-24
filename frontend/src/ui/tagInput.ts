/**
 * Tag suggestion dropdown for plain text inputs (no framework).
 *
 * attachTagSuggest(input, { onPick }) puts a <ul role="listbox"> right after the input (inside the
 * same parent, never in <body>: inside a modal <dialog> body content is hidden behind the dialog).
 * It shows related tags on focus and matching tags while typing. The server excludes tags the card
 * already has. Styles live in tagInput.css (imported here).
 */

import { esc } from '../cards/card.ts'
import { withClient } from '../data/api.ts'
import './tagInput.css'

export interface TagSuggestion { id: number; name: string; count: number }
export interface TagSuggestOptions {
  /** The card being tagged (for related tags); null/undefined when there is none (bulk tagging). */
  mediaId?: () => number | null
  /** Called when the player picks a suggestion (click, or Enter on a highlighted row). The caller applies the tag. */
  onPick: (name: string) => void
  /** Max rows shown. Default 8. */
  limit?: number
  /** Show the list only once something is typed (for a page that already shows quick tags under the field). */
  typedOnly?: boolean
}
export interface SuggestRow { name: string; count: number; group: 'match' | 'related' }

const DEBOUNCE_MS = 120
const BLUR_MS = 150

/** GET /api/tags/suggest. Throws on any failure (the caller closes the dropdown silently). */
export async function fetchSuggestions(
  q: string,
  mediaId: number | null,
  limit: number,
): Promise<{ matches: TagSuggestion[]; related: TagSuggestion[] }> {
  const p = new URLSearchParams({ q, limit: String(limit) })
  if (mediaId != null) p.set('media_id', String(mediaId))
  const r = await fetch(`/api/tags/suggest?${p.toString()}`, withClient())
  if (!r.ok) throw new Error(`suggest failed: ${r.status}`)
  const j = (await r.json()) as { matches?: TagSuggestion[]; related?: TagSuggestion[] }
  return { matches: j.matches ?? [], related: j.related ?? [] }
}

/** Matches first, then related; no name twice (case-insensitive); at most `limit` rows. Pure. */
export function suggestionRows(
  matches: TagSuggestion[],
  related: TagSuggestion[],
  typed: string,
  limit: number,
): SuggestRow[] {
  void typed
  const out: SuggestRow[] = []
  const seen = new Set<string>()
  const add = (list: TagSuggestion[], group: SuggestRow['group']) => {
    for (const t of list) {
      if (out.length >= limit) return
      const key = t.name.toLowerCase()
      if (seen.has(key)) continue
      seen.add(key)
      out.push({ name: t.name, count: t.count, group })
    }
  }
  if (limit > 0) { add(matches, 'match'); add(related, 'related') }
  return out
}

/** Escaped HTML with the first occurrence of the typed text wrapped in <mark> (case-insensitive). Pure. */
export function highlight(name: string, typed: string): string {
  const t = typed.trim()
  if (!t) return esc(name)
  const i = name.toLowerCase().indexOf(t.toLowerCase())
  if (i < 0) return esc(name)
  return `${esc(name.slice(0, i))}<mark>${esc(name.slice(i, i + t.length))}</mark>${esc(name.slice(i + t.length))}`
}

let uid = 0

export function attachTagSuggest(
  input: HTMLInputElement,
  opts: TagSuggestOptions,
): { refresh(): void; close(): void; destroy(): void } {
  const limit = opts.limit ?? 8
  const id = `tag-suggest-${++uid}`
  const list = document.createElement('ul')
  list.className = 'tag-suggest'
  list.id = id
  list.setAttribute('role', 'listbox')
  list.hidden = true

  const parent = input.parentElement
  if (parent && getComputedStyle(parent).position === 'static') parent.classList.add('tag-suggest-anchor')
  input.after(list)

  input.removeAttribute('list')
  input.setAttribute('role', 'combobox')
  input.setAttribute('aria-autocomplete', 'list')
  input.setAttribute('aria-expanded', 'false')
  input.setAttribute('aria-controls', id)

  let rows: SuggestRow[] = []
  let active = -1
  let open = false
  let reqId = 0
  let debounce: ReturnType<typeof setTimeout> | undefined
  let blurTimer: ReturnType<typeof setTimeout> | undefined
  let destroyed = false

  const alive = () => !destroyed && input.isConnected

  function setActive(i: number) {
    active = i
    list.querySelectorAll<HTMLElement>('[role="option"]').forEach((el, n) => {
      const on = n === i
      el.setAttribute('aria-selected', on ? 'true' : 'false')
      el.classList.toggle('is-active', on)
      if (on) el.scrollIntoView?.({ block: 'nearest' })
    })
    if (i >= 0) input.setAttribute('aria-activedescendant', `${id}-${i}`)
    else input.removeAttribute('aria-activedescendant')
  }

  function close() {
    reqId++ // drop any request still in flight
    clearTimeout(debounce)
    open = false
    rows = []
    active = -1
    list.hidden = true
    list.innerHTML = ''
    input.setAttribute('aria-expanded', 'false')
    input.removeAttribute('aria-activedescendant')
  }

  function render(typed: string) {
    const t = typed.trim()
    let html = ''
    if (!rows.length) {
      if (!t) return close()
      html = `<li class="tag-suggest-empty" role="presentation">Press Enter to create &quot;${esc(t)}&quot;</li>`
    } else {
      let group = ''
      rows.forEach((r, i) => {
        if (r.group !== group) {
          group = r.group
          // Matches need no heading when the dropdown has only matches; related always get one.
          if (r.group === 'related') html += '<li class="tag-suggest-heading" role="presentation">Related tags</li>'
          else if (rows.some((x) => x.group === 'related')) html += '<li class="tag-suggest-heading" role="presentation">Matching tags</li>'
        }
        html += `<li class="tag-suggest-row" role="option" id="${id}-${i}" aria-selected="false" data-i="${i}">` +
          `<span class="tag-suggest-name">${highlight(r.name, typed)}</span><span class="tag-suggest-count">×${r.count}</span></li>`
      })
    }
    list.innerHTML = html
    list.hidden = false
    open = true
    input.setAttribute('aria-expanded', 'true')
    active = -1
    input.removeAttribute('aria-activedescendant')
  }

  async function load() {
    if (!alive()) return
    const typed = input.value
    if (opts.typedOnly && typed.trim() === '') return close()
    const my = ++reqId
    try {
      const r = await fetchSuggestions(typed.trim(), opts.mediaId?.() ?? null, limit)
      if (my !== reqId || !alive() || document.activeElement !== input) return
      rows = suggestionRows(r.matches, r.related, typed, limit)
      render(typed)
    } catch {
      if (my === reqId) close()
    }
  }

  function schedule() {
    clearTimeout(debounce)
    debounce = setTimeout(() => { void load() }, DEBOUNCE_MS)
  }

  function pick(i: number) {
    const r = rows[i]
    if (!r) return
    close()
    input.value = ''
    input.focus()
    opts.onPick(r.name)
    void load() // the related tags change once a tag is added
  }

  const onFocus = () => { clearTimeout(blurTimer); void load() }
  const onInput = () => schedule()
  const onBlur = () => {
    clearTimeout(blurTimer)
    clearTimeout(debounce)
    blurTimer = setTimeout(() => { if (!destroyed) close() }, BLUR_MS)
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!open || !rows.length) { if (e.key === 'ArrowDown') void load(); return }
      e.preventDefault()
      const n = rows.length
      setActive(e.key === 'ArrowDown' ? (active + 1) % n : (active - 1 + n) % n)
    } else if (e.key === 'Enter') {
      if (open && active >= 0 && rows[active]) {
        e.preventDefault()
        e.stopPropagation()
        pick(active)
      }
    } else if (e.key === 'Escape') {
      if (open) { e.stopPropagation(); e.preventDefault(); close() }
    } else if (e.key === 'Tab') {
      if (open) close()
    }
  }
  const onDown = (e: MouseEvent) => {
    const row = (e.target as HTMLElement | null)?.closest<HTMLElement>('[role="option"]')
    if (!row) return
    e.preventDefault() // keep focus in the input
    pick(Number(row.dataset.i))
  }

  input.addEventListener('focus', onFocus)
  input.addEventListener('input', onInput)
  input.addEventListener('blur', onBlur)
  input.addEventListener('keydown', onKey)
  list.addEventListener('mousedown', onDown)

  // Already focused when attached (e.g. a re-render that restores focus): show right away.
  if (document.activeElement === input) void load()

  return {
    refresh() { if (alive() && document.activeElement === input) void load() },
    close,
    destroy() {
      if (destroyed) return
      destroyed = true
      reqId++
      clearTimeout(debounce)
      clearTimeout(blurTimer)
      input.removeEventListener('focus', onFocus)
      input.removeEventListener('input', onInput)
      input.removeEventListener('blur', onBlur)
      input.removeEventListener('keydown', onKey)
      list.removeEventListener('mousedown', onDown)
      list.remove()
      for (const a of ['role', 'aria-autocomplete', 'aria-expanded', 'aria-controls', 'aria-activedescendant']) input.removeAttribute(a)
    },
  }
}
