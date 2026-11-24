/**
 * Help content loader.
 *
 * Every Markdown file under ./content/faq or ./content/guides becomes a HelpArticle.
 * Files start with a small frontmatter block:
 *
 *   ---
 *   title: Where are my files stored?
 *   category: Basics
 *   order: 10
 *   ---
 *
 * The id is the filename without extension. Links inside articles can use
 *   [text](#tour:welcome)   → start a tour
 *   [text](#help:some-id)   → open another article
 */

export type ArticleKind = 'faq' | 'guide'

export interface HelpArticle {
  id: string
  kind: ArticleKind
  title: string
  category: string
  order: number
  body: string // Markdown
}

const FRONTMATTER = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/

export function parseArticle(path: string, raw: string): HelpArticle {
  const kind: ArticleKind = path.includes('/faq/') ? 'faq' : 'guide'
  const id = path.split('/').pop()!.replace(/\.md$/, '')
  const meta: Record<string, string> = {}
  let body = raw
  const m = raw.match(FRONTMATTER)
  if (m) {
    for (const line of m[1].split(/\r?\n/)) {
      const i = line.indexOf(':')
      if (i > 0) meta[line.slice(0, i).trim()] = line.slice(i + 1).trim()
    }
    body = raw.slice(m[0].length)
  }
  return {
    id,
    kind,
    title: meta.title ?? id,
    category: meta.category ?? 'General',
    order: Number(meta.order ?? 100),
    body: body.trim(),
  }
}

export function sortArticles(list: HelpArticle[]): HelpArticle[] {
  return [...list].sort(
    (a, b) => a.category.localeCompare(b.category) || a.order - b.order || a.title.localeCompare(b.title),
  )
}

export function searchArticles(list: HelpArticle[], query: string): HelpArticle[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return list
  return list.filter((a) => {
    const hay = `${a.title} ${a.category} ${a.body}`.toLowerCase()
    return words.every((w) => hay.includes(w))
  })
}

let cache: HelpArticle[] | null = null

export function loadArticles(): HelpArticle[] {
  if (!cache) {
    const files = import.meta.glob('./content/**/*.md', {
      query: '?raw',
      import: 'default',
      eager: true,
    }) as Record<string, string>
    cache = sortArticles(Object.entries(files).map(([p, raw]) => parseArticle(p, raw)))
  }
  return cache
}
