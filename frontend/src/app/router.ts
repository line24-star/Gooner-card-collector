/** Minimal hash router: #/collection → the "collection" page. Pages mount into an outlet. */

/** Prefix for document.title, e.g. "[test] " on a non-main profile. */
let titlePrefix = ''
export function setTitlePrefix(p: string): void {
  titlePrefix = p
  document.title = p + document.title
}

export interface Page {
  id: string
  title: string
  icon: string
  /** Render into el. Return a cleanup function (optional). */
  mount: (el: HTMLElement) => void | (() => void)
}

export function parseRoute(hash: string, ids: string[], fallback: string): string {
  const id = hash.replace(/^#\/?/, '').split(/[/?]/)[0]
  return ids.includes(id) ? id : fallback
}

/**
 * `guard` may send a route somewhere else (it returns the page id to show instead, or null to allow
 * it). The app uses it to keep a player without any cards on the setup screen.
 */
export function startRouter(
  outlet: HTMLElement, pages: Page[], onChange: (p: Page) => void, guard?: (id: string) => string | null,
): void {
  let cleanup: void | (() => void)
  const ids = pages.map((p) => p.id)

  const render = () => {
    const id = parseRoute(location.hash, ids, pages[0].id)
    const forced = guard?.(id)
    if (forced && forced !== id && ids.includes(forced)) {
      location.replace(`#/${forced}`) // fires hashchange, which renders the allowed page
      return
    }
    const page = pages.find((p) => p.id === id)!
    if (typeof cleanup === 'function') cleanup()
    // Fresh host per page so listeners a page attaches die with it.
    const host = document.createElement('div')
    host.className = `page page-${id}`
    outlet.replaceChildren(host)
    outlet.dataset.page = id
    document.title = `${titlePrefix}${page.title} · Media Collection Organizer`
    cleanup = page.mount(host)
    onChange(page)
    outlet.focus({ preventScroll: true })
  }
  window.addEventListener('hashchange', render)
  render()
}

/**
 * Wrap a page's mount so it remounts (cleanup, empty, mount again) whenever watch fires.
 * Used with store.onLibraryChange so Collection and Organize follow newly imported cards.
 */
export function remountOn(watch: (fn: () => void) => () => void, mount: Page['mount']): Page['mount'] {
  return (el) => {
    let cleanup = mount(el)
    const off = watch(() => {
      if (typeof cleanup === 'function') cleanup()
      el.replaceChildren()
      cleanup = mount(el)
    })
    return () => {
      off()
      if (typeof cleanup === 'function') cleanup()
    }
  }
}

export function navigate(id: string): void {
  location.hash = `#/${id}`
}
