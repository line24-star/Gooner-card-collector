/**
 * Help Center modal: Guides, FAQ, and Tours tabs with search.
 * Open with openHelp() or the "?" key.
 */

import { marked } from 'marked'
import { loadArticles, searchArticles, type HelpArticle } from './content.ts'
import { fetchProgress, type TutorialStatus } from './progress.ts'
import { TOURS } from './tours.ts'
import { fetchOnboarding, toursRewardText } from '../data/onboardingApi.ts'
import { overlayOpen } from '../ui/overlay.ts'

type Tab = 'guides' | 'faq' | 'tours'

export interface HelpCenterOptions {
  /** Called when the user starts a tour from the Help Center or an article link. */
  startTour: (id: string) => void
}

let dialog: HTMLDialogElement | null = null
let opts: HelpCenterOptions
let tab: Tab = 'guides'
let query = ''
let openArticleId: string | null = null
let progress = new Map<string, TutorialStatus>()

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)
}

function md(src: string): string {
  // Content is authored in-repo, not user input, so no sanitizer is needed yet.
  return marked.parse(src, { async: false })
}

function byCategory(list: HelpArticle[]): Map<string, HelpArticle[]> {
  const out = new Map<string, HelpArticle[]>()
  for (const a of list) out.set(a.category, [...(out.get(a.category) ?? []), a])
  return out
}

let toursLine = ''

function renderBody(): string {
  if (openArticleId) {
    const a = loadArticles().find((x) => x.id === openArticleId)
    if (a) {
      return `<button type="button" class="help-back" data-act="back">← Back</button>
              <article class="help-article"><h2>${esc(a.title)}</h2>${md(a.body)}</article>`
    }
  }

  if (tab === 'tours') {
    return `${toursLine ? `<p class="help-tours-reward" data-el="tours-reward">🎓 ${esc(toursLine)}. Skipping a tour does not count.</p>` : ''}<ul class="help-tours">${TOURS.map((t) => {
      const st = progress.get(t.id)
      const badge = st === 'completed' ? '<span class="help-badge done">Taken</span>'
        : st === 'skipped' ? '<span class="help-badge">Skipped</span>' : ''
      return `<li><div><b>${esc(t.title)}</b> ${badge}<p>${esc(t.description)}</p></div>
              <button type="button" data-tour-id="${t.id}">${st ? 'Replay' : 'Start'}</button></li>`
    }).join('')}</ul>`
  }

  const kind = tab === 'faq' ? 'faq' : 'guide'
  const list = searchArticles(loadArticles().filter((a) => a.kind === kind), query)
  if (!list.length) return `<p class="help-empty">Nothing matches “${esc(query)}”.</p>`

  return [...byCategory(list)].map(([cat, items]) => `
    <section><h3 class="help-cat">${esc(cat)}</h3>
    ${kind === 'faq'
      ? items.map((a) => `<details class="help-faq"><summary>${esc(a.title)}</summary>${md(a.body)}</details>`).join('')
      : `<ul class="help-list">${items.map((a) => `<li><a href="#help:${a.id}">${esc(a.title)}</a></li>`).join('')}</ul>`}
    </section>`).join('')
}

function render() {
  if (!dialog) return
  dialog.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((b) => {
    b.setAttribute('aria-selected', String(b.dataset.tab === tab && !openArticleId))
  })
  dialog.querySelector<HTMLDivElement>('.help-search')!.hidden = tab === 'tours' || !!openArticleId
  dialog.querySelector<HTMLDivElement>('.help-body')!.innerHTML = renderBody()
}

function build(): HTMLDialogElement {
  const d = document.createElement('dialog')
  d.className = 'help-center'
  d.setAttribute('aria-label', 'Help')
  d.innerHTML = `
    <header class="help-head">
      <h2>Help</h2>
      <nav role="tablist">
        <button type="button" role="tab" data-tab="guides">Guides</button>
        <button type="button" role="tab" data-tab="faq">FAQ</button>
        <button type="button" role="tab" data-tab="tours">Tours</button>
      </nav>
      <button type="button" class="help-close" data-act="close" aria-label="Close">✕</button>
    </header>
    <div class="help-search"><input type="search" placeholder="Search help…" aria-label="Search help"></div>
    <div class="help-body"></div>`

  d.addEventListener('click', (e) => {
    const el = e.target as HTMLElement
    if (el === d) return d.close() // backdrop click
    const tabBtn = el.closest<HTMLElement>('[data-tab]')
    if (tabBtn) { tab = tabBtn.dataset.tab as Tab; openArticleId = null; return render() }
    const act = el.closest<HTMLElement>('[data-act]')?.dataset.act
    if (act === 'close') return d.close()
    if (act === 'back') { openArticleId = null; return render() }
    const tourBtn = el.closest<HTMLElement>('[data-tour-id]')
    if (tourBtn) { d.close(); return opts.startTour(tourBtn.dataset.tourId!) }
    const link = el.closest<HTMLAnchorElement>('a[href^="#"]')
    if (link) {
      const href = link.getAttribute('href')!
      if (href.startsWith('#tour:')) { e.preventDefault(); d.close(); opts.startTour(href.slice(6)) }
      else if (href.startsWith('#help:')) { e.preventDefault(); openArticleId = href.slice(6); render() }
    }
  })
  d.querySelector('input')!.addEventListener('input', (e) => {
    query = (e.target as HTMLInputElement).value
    render()
  })
  document.body.append(d)
  return d
}

export function initHelpCenter(options: HelpCenterOptions): void {
  opts = options
  window.addEventListener('keydown', (e) => {
    const typing = (e.target as HTMLElement).closest('input, textarea, [contenteditable]')
    if (e.key === '?' && !typing && !dialog?.open && !overlayOpen()) { e.preventDefault(); openHelp() }
  })
}

/** Open the Help Center, optionally on a tab or a specific article id. */
export async function openHelp(target?: Tab | string): Promise<void> {
  dialog ??= build()
  if (target === 'guides' || target === 'faq' || target === 'tours') { tab = target; openArticleId = null }
  else if (target) openArticleId = target
  try { progress = await fetchProgress() } catch { /* offline: show tours without badges */ }
  // v0.9.0: the tours pay their packs once, when all of them are taken. Say how far that is.
  try { toursLine = toursRewardText((await fetchOnboarding()).tours) } catch { toursLine = '' }
  render()
  if (!dialog.open) dialog.showModal()
}
