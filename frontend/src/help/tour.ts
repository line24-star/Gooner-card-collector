/**
 * Minimal guided-tour engine.
 *
 * A step points at an element via a CSS selector (convention: [data-tour="name"]).
 * The element gets a spotlight; everything else is dimmed; a popover explains it.
 * A step without a target is a centered popover. A step whose target is not on screen (or hidden)
 * is skipped: a tour never explains something the player cannot see. A tour with no step to show
 * ends as "skipped", so it pays nothing and can run again later.
 *
 * Keys: → / Enter = next, ← = back, Esc = skip.
 */

export interface TourStep {
  target?: string
  title: string
  body: string // plain text or simple HTML (authored by us, not user input)
}

export interface Tour {
  id: string
  title: string
  description: string
  /** Page (router id) the tour's targets live on; Start navigates there first. */
  page?: string
  steps: TourStep[]
}

export type TourResult = 'completed' | 'skipped'

/**
 * The index of the next step that can be shown, starting at `from` and walking in `dir` (1 or -1).
 * -1 when there is none in that direction. `visible(step)` says whether a step's target is on screen.
 */
export function nextShowable(steps: TourStep[], from: number, dir: 1 | -1, visible: (step: TourStep) => boolean): number {
  for (let i = from; i >= 0 && i < steps.length; i += dir) {
    if (!steps[i].target || visible(steps[i])) return i
  }
  return -1
}

/** Is this step's target on screen? An element inside a hidden parent (display: none) has no box. */
function targetVisible(step: TourStep): boolean {
  const el = step.target ? document.querySelector<HTMLElement>(step.target) : null
  return !!el && el.getClientRects().length > 0
}

let active = false

/** Where tour layers go: the topmost open modal dialog (top layer would hide them under body), else body. */
export function tourHost(doc: Document): Element {
  const open = doc.querySelectorAll('dialog[open]')
  return open.length ? open[open.length - 1] : doc.body
}

export function isTourActive(): boolean {
  return active
}

/** Calls onClose when a dialog host closes (or is removed after closing). Returns the unbind function. */
export function watchHostClose(host: Pick<EventTarget, 'addEventListener' | 'removeEventListener'>, onClose: () => void): () => void {
  host.addEventListener('close', onClose)
  return () => host.removeEventListener('close', onClose)
}

export function runTour(tour: Tour): Promise<TourResult> {
  if (active || tour.steps.length === 0) return Promise.resolve('skipped')
  const first = nextShowable(tour.steps, 0, 1, targetVisible)
  if (first < 0) return Promise.resolve('skipped') // nothing of this tour is on screen
  active = true

  return new Promise((resolve) => {
    let index = first

    const shade = document.createElement('div')
    shade.className = 'tour-shade'
    const spot = document.createElement('div')
    spot.className = 'tour-spotlight'
    const pop = document.createElement('div')
    pop.className = 'tour-pop'
    pop.setAttribute('role', 'dialog')
    pop.setAttribute('aria-modal', 'true')
    pop.setAttribute('aria-labelledby', 'tour-title')
    const host = tourHost(document)
    const returnFocus = document.activeElement as HTMLElement | null
    host.append(shade, spot, pop)
    const unwatch = host instanceof HTMLDialogElement ? watchHostClose(host, () => finish('skipped')) : () => {}

    const finish = (result: TourResult) => {
      unwatch()
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
      shade.remove()
      spot.remove()
      pop.remove()
      active = false
      if (returnFocus?.isConnected) returnFocus.focus()
      resolve(result)
    }

    const go = (next: number) => {
      const dir = next >= index ? 1 : -1
      const to = nextShowable(tour.steps, next, dir, targetVisible)
      if (to < 0) {
        if (dir === 1) return finish('completed') // no further step to show
        return // Back on the first step that can be shown: stay
      }
      index = to
      render()
    }

    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') finish('skipped')
      else if (e.key === 'ArrowRight' || e.key === 'Enter') go(index + 1)
      else if (e.key === 'ArrowLeft') go(index - 1)
      else return
      e.preventDefault()
      e.stopPropagation()
    }

    function target(): HTMLElement | null {
      const sel = tour.steps[index].target
      return sel ? document.querySelector<HTMLElement>(sel) : null
    }

    function place() {
      const el = target()
      const gap = 12
      const pw = pop.offsetWidth
      const ph = pop.offsetHeight
      if (!el) {
        spot.hidden = true
        shade.hidden = false
        pop.style.left = `${(innerWidth - pw) / 2}px`
        pop.style.top = `${(innerHeight - ph) / 2}px`
        return
      }
      shade.hidden = true // the spotlight's box-shadow does the dimming
      spot.hidden = false
      el.scrollIntoView({ block: 'nearest', inline: 'nearest' })
      const r = el.getBoundingClientRect()
      const pad = 6
      Object.assign(spot.style, {
        left: `${r.left - pad}px`,
        top: `${r.top - pad}px`,
        width: `${r.width + pad * 2}px`,
        height: `${r.height + pad * 2}px`,
      })
      const below = r.bottom + gap + ph <= innerHeight
      const top = below ? r.bottom + gap : Math.max(gap, r.top - gap - ph)
      const left = Math.min(Math.max(gap, r.left + r.width / 2 - pw / 2), innerWidth - pw - gap)
      pop.style.left = `${left}px`
      pop.style.top = `${top}px`
    }

    function render() {
      const step = tour.steps[index]
      const last = nextShowable(tour.steps, index + 1, 1, targetVisible) < 0
      const hasBack = nextShowable(tour.steps, index - 1, -1, targetVisible) >= 0
      const shown = tour.steps.filter((x) => !x.target || targetVisible(x)) // the count leaves out what is skipped
      pop.innerHTML = `
        <div class="tour-progress">${shown.indexOf(step) + 1} / ${shown.length}</div>
        <h3 id="tour-title">${step.title}</h3>
        <div class="tour-body">${step.body}</div>
        <div class="tour-actions">
          <button type="button" data-act="skip" class="tour-link">Skip tour</button>
          <span class="tour-spacer"></span>
          ${hasBack ? '<button type="button" data-act="back">Back</button>' : ''}
          <button type="button" data-act="next" class="tour-primary">${last ? 'Finish' : 'Next'}</button>
        </div>`
      pop.querySelector<HTMLButtonElement>('[data-act="next"]')!.focus()
      place()
    }

    pop.addEventListener('click', (e) => {
      const act = (e.target as HTMLElement).closest<HTMLElement>('[data-act]')?.dataset.act
      if (act === 'next') go(index + 1)
      else if (act === 'back') go(index - 1)
      else if (act === 'skip') finish('skipped')
    })
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    render()
  })
}
