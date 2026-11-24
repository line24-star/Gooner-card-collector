/** The card names a news line starts with, as links to the card panel (Market news and Messages). */

import { esc } from '../cards/card.ts'
import { newsCards, type NewsItem } from '../data/marketApi.ts'

/**
 * "Sunset (yours), Card #000412 and 2 more": each name is a button that opens that card's panel.
 * A card the player does not own opens too: the panel shows it read-only. The page listens for
 * clicks on `[data-news-card]`.
 */
export function newsNamesHTML(n: NewsItem): string {
  const c = newsCards(n)
  if (!c.shown.length) return ''
  const names = c.shown.map((x) =>
    `<button type="button" class="news-card" data-news-card="${x.id}" title="Open this card">${esc(x.title)}</button>${x.owned ? ' <span class="news-yours">(yours)</span>' : ''}`)
  return names.join(', ') + (c.more ? ` and ${c.more} more` : '')
}
