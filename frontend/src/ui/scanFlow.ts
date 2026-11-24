/**
 * The scan as the player meets it (the Scan button, the Player page, the setup screen):
 * a look ahead first, a question when the scan would add media to a library that already has
 * cards (every new file adds three empty version slots, so the percentage drops), then the scan,
 * then a message with the way forward: the packs.
 */

import { scanPreviewApi, type ScanPreview, type ScanStatus } from '../data/api.ts'
import { requestScan, scanCoinsText, scanSummary, type ScanCoins } from '../data/scan.ts'
import { applySlots, pctText } from '../data/store.ts'
import type { SlotsChange } from '../data/types.ts'
import { confirmDialog, type ConfirmOptions } from './confirm.ts'
import { toast } from './toast.ts'

/** The question before a scan, or null when there is nothing to ask (no new files, or the first import). */
export function scanQuestion(p: ScanPreview): ConfirmOptions | null {
  if (p.new_media < 1 || p.slots_total < 1) return null
  const n = p.new_media
  return {
    title: `Add ${n.toLocaleString()} new media?`,
    text: `Adding ${n.toLocaleString()} media will add ${p.new_slots.toLocaleString()} version slots. Versions: ${pctText(p.completeness_now)} → ${pctText(p.completeness_after)}`,
    rows: [
      { label: 'Version slots now', value: `${p.slots_filled.toLocaleString()} / ${p.slots_total.toLocaleString()}`, tone: 'plain' },
      { label: 'After the scan', value: `${p.slots_filled_after.toLocaleString()} / ${p.slots_total_after.toLocaleString()}`, tone: 'plain' },
      // v0.9.0: what the scan pays, and why when that is less than the full rate
      ...(scanCoinsText(p) ? [{ label: 'Coins for the new files', value: scanCoinsText(p), tone: 'plain' as const }] : []),
    ],
    note: 'New cards wait in the packs until you pull them. The versions you hold stay yours.',
    confirm: 'Scan',
    cancel: 'Cancel',
  }
}

/** "+12 media · +36 version slots · +45 coins" for a scan that added media (with the reason when it paid less), else null. */
export function scanAddedText(report: Record<string, unknown> | null | undefined): string | null {
  const n = Number(report?.new_media ?? 0)
  if (!(n > 0)) return null
  const slots = Number(report?.new_slots ?? n * 3)
  const coins = scanCoinsText(report as ScanCoins | null | undefined)
  return `+${n.toLocaleString()} media · +${slots.toLocaleString()} version slots${coins ? ` · ${coins}` : ''}`
}

/**
 * Scan the folder now. Resolves the scan's last status, or 'cancelled' when the player said no.
 * Throws when the scan could not be started.
 */
export async function runScan(): Promise<ScanStatus | null | 'cancelled'> {
  let preview: ScanPreview | null = null
  try { preview = await scanPreviewApi() } catch { /* no look ahead: scan as before */ }
  const question = preview ? scanQuestion(preview) : null
  if (question && !(await confirmDialog(question))) return 'cancelled'
  const st = await requestScan()
  const added = scanAddedText(st?.last_report)
  if (added) {
    applySlots(st?.last_report?.slots as SlotsChange | undefined) // the pill steps to the new total
    toast(added, { kind: 'good', ms: 12000, action: { label: 'Open packs', href: '#/packs' } })
  } else {
    toast(scanSummary(st))
  }
  return st
}
