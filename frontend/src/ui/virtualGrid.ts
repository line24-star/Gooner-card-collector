/**
 * Windowed grid: only the rows in (or near) the viewport are in the DOM, so the gallery
 * stays fast with thousands of cards (design doc §13). Fixed-size cells.
 */

export interface VirtualGridOptions<T> {
  items: T[]
  cellWidth: number
  cellHeight: number
  gap: number
  render: (item: T) => string
  overscanRows?: number
}

export interface VirtualGrid<T> {
  /** A different list (new filter): replaces the items and scrolls back to the top. */
  setItems(items: T[]): void
  /** The same list with updated cards: redraws in place and keeps the scroll position. */
  refresh(items: T[]): void
  destroy(): void
}

export function visibleRange(scrollTop: number, viewport: number, rowHeight: number, rows: number, overscan: number) {
  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan)
  const last = Math.min(rows, Math.ceil((scrollTop + viewport) / rowHeight) + overscan)
  return { first, last }
}

export function createVirtualGrid<T>(scroller: HTMLElement, opts: VirtualGridOptions<T>): VirtualGrid<T> {
  let items = opts.items
  const overscan = opts.overscanRows ?? 2
  const rowH = opts.cellHeight + opts.gap
  const spacer = document.createElement('div')
  spacer.className = 'vgrid-spacer'
  const layer = document.createElement('div')
  layer.className = 'vgrid-layer'
  spacer.append(layer)
  scroller.append(spacer)
  let lastKey = ''

  const columns = () => Math.max(1, Math.floor((scroller.clientWidth + opts.gap) / (opts.cellWidth + opts.gap)))

  function update() {
    const cols = columns()
    const rows = Math.ceil(items.length / cols)
    spacer.style.height = `${rows * rowH}px`
    const top = Math.max(0, scroller.scrollTop - spacer.offsetTop)
    const { first, last } = visibleRange(top, scroller.clientHeight, rowH, rows, overscan)
    const key = `${cols}:${first}:${last}:${items.length}`
    if (key === lastKey) return
    lastKey = key
    layer.style.transform = `translateY(${first * rowH}px)`
    layer.style.gridTemplateColumns = `repeat(${cols}, ${opts.cellWidth}px)`
    layer.style.gap = `${opts.gap}px`
    layer.innerHTML = items.slice(first * cols, last * cols).map(opts.render).join('')
  }

  // Redraw on the next frame: changing the layout inside the observer's own callback makes the browser
  // report "ResizeObserver loop completed with undelivered notifications".
  let frame = 0
  const ro = new ResizeObserver(() => {
    cancelAnimationFrame(frame)
    frame = requestAnimationFrame(() => { lastKey = ''; update() })
  })
  ro.observe(scroller)
  scroller.addEventListener('scroll', update, { passive: true })
  update()

  return {
    setItems(next) { items = next; lastKey = ''; scroller.scrollTop = 0; update() },
    refresh(next) { items = next; lastKey = ''; update() },
    destroy() { cancelAnimationFrame(frame); ro.disconnect(); scroller.removeEventListener('scroll', update); spacer.remove() },
  }
}
