import { describe, expect, it } from 'vitest'
import { cardHTML } from './card.ts'
import type { Card } from '../data/types.ts'

function make(over: Partial<Card> = {}): Card {
  return {
    id: 7,
    title: 'Sunset',
    kind: 'image',
    imageUrl: '',
    treatment: 'normal',
    tags: [],
    copies: 1,
    addedAt: '2026-01-01',
    thumbUrl: null,
    fileUrl: null,
    owned: {},
    tagIds: [],
    reviewAvailable: false,
    reviewReward: null,
    upgradeCost: null,
    sellPrices: {},
    sellExtras: {},
    copiesByTreatment: {},
    value: 0,
    printRun: 0,
    printTier: '',
    trend: 0,
    wanted: false,
    fuseFrom: null, fuseCopies: 3,
    number: 0, code: '', prices: {}, plainPrices: {}, saleQuotes: {}, fuseOptions: [],
    nextTagReward: 0,
    minPack: null,
    titleCustom: false,
    fileName: '',
    ...over,
  }
}

describe('cardHTML art', () => {
  it('renders thumbnail img for live image card', () => {
    const html = cardHTML(make({ thumbUrl: '/api/media/7/thumb', fileUrl: '/api/media/7/file' }))
    expect(html).toContain('<img src="/api/media/7/thumb"')
    expect(html).toContain('loading="lazy"')
    expect(html).not.toContain('<video')
  })

  it('renders muted video poster for video without thumb', () => {
    const html = cardHTML(make({ kind: 'video', fileUrl: '/api/media/7/file' }))
    expect(html).toContain('<video')
    expect(html).toContain('muted')
    expect(html).toContain('playsinline')
    expect(html).toContain('preload="metadata"')
    expect(html).toContain('src="/api/media/7/file#t=1"')
    expect(html).not.toContain('autoplay')
    // a large video card plays in its frame, muted, in a loop, with no player controls
    const big = cardHTML(make({ kind: 'video', fileUrl: '/api/media/7/file' }), { size: 'lg' })
    expect(big).toContain('<video autoplay loop muted playsinline')
    expect(big).not.toContain('controls')
    expect(html).toContain('card-art-ph')
  })

  it('lg and xl show the original image so large art stays sharp', () => {
    const c = make({ thumbUrl: '/api/media/7/thumb', fileUrl: '/api/media/7/file' })
    expect(cardHTML(c, { size: 'lg' })).toContain('<img src="/api/media/7/file"')
    expect(cardHTML(c, { size: 'xl' })).toContain('<img src="/api/media/7/file"')
    expect(cardHTML(c, { size: 'md' })).toContain('<img src="/api/media/7/thumb"')
    expect(cardHTML(c, { size: 'lg' })).not.toContain('loading="lazy"') // the card being looked at loads at once
    expect(cardHTML(c, { size: 'md' })).toContain('loading="lazy"')
  })

  it('Normal and Foil use the classic framed layout: title, art window, footer', () => {
    for (const treatment of ['normal', 'foil']) {
      const html = cardHTML(make({ thumbUrl: '/t.webp', treatment }))
      expect(html).not.toContain('card-fullart')
      const art = html.indexOf('class="card-art"')
      expect(html.indexOf('class="card-head"')).toBeLessThan(art)
      expect(html.indexOf('class="card-foot"')).toBeGreaterThan(art)
    }
  })

  it('Holo and Special are full art', () => {
    for (const treatment of ['holo', 'special']) {
      expect(cardHTML(make({ thumbUrl: '/t.webp', treatment }))).toContain('card-fullart')
    }
  })

  it('falls back to imageUrl when there is no thumb', () => {
    const html = cardHTML(make({ imageUrl: 'data:image/svg+xml,abc' }))
    expect(html).toContain('<img src="data:image/svg+xml,abc"')
  })

  it('shows only the placeholder when a card has no art', () => {
    const html = cardHTML(make())
    expect(html).toContain('card-art-ph')
    expect(html).not.toContain('<img')
    expect(html).not.toContain('<video')
  })

  it('escapes title and urls', () => {
    const html = cardHTML(
      make({ title: '<b>"x"</b>', thumbUrl: '/t.png" onerror="alert(1)' }),
    )
    expect(html).not.toContain('<b>')
    expect(html).toContain('&#60;b&#62;')
    expect(html).not.toContain('" onerror="alert(1)')
  })
})
