/** Shared client-side data shapes, built from the server's responses in data/api.ts. */

export type TreatmentId = 'normal' | 'foil' | 'holo' | 'special' | (string & {})

export interface TreatmentInfo {
  id: TreatmentId
  name: string
  tier: number
  multiplier: number
  pack_weight: number
  pack_odds: number
  /** Completeness score of a tagged card owned at this treatment (top treatment = 100%). */
  completeness_weight?: number
}

export interface Card {
  id: number
  title: string
  kind: 'image' | 'video'
  imageUrl: string // thumbnail / card art
  treatment: TreatmentId
  tags: string[]
  copies: number // how many times pulled from packs
  addedAt: string
  /** Server thumbnail URL (images only); null for videos. */
  thumbUrl: string | null
  /** Full-size file URL. */
  fileUrl: string | null
  /** Treatments owned (treatment id -> 1). Empty when not owned. */
  owned: Record<string, number>
  tagIds: number[]
  reviewAvailable: boolean
  /** Coins a review would pay (server-computed); null when unknown. */
  reviewReward: number | null
  upgradeCost: number | null
  /** What the market pays today for one copy of each treatment you own. */
  sellPrices: Record<string, number>
  /** What selling every copy but one of a treatment pays in all (the sale price times the copies: a sale does not move the price). */
  sellExtras: Record<string, number>
  /** Copies owned per treatment id. */
  copiesByTreatment: Record<string, number>
  /** Market value today of one copy in your best treatment (Normal when not owned). */
  value: number
  /** Copies of this card "issued": the smaller, the scarcer and the harder to pull. 0 = unknown. */
  printRun: number
  /** Name of the print tier, e.g. "Chase", "Limited", "Mass print". */
  printTier: string
  /** Price change over the last day, as a fraction (0.12 = up 12%). */
  trend: number
  /** v0.9.0: the price change over the last update, the last 24 hours, the week and the month (fractions). */
  changes?: { update: number; day: number; week: number; month: number }
  /** A collector pays extra for this card until the market moves again. */
  wanted: boolean
  /** The player gave this card its name (otherwise it is the cleaned file name, or "Card #000123" when that says nothing). */
  titleCustom: boolean
  /** The file's own name without its extension. The file is never renamed. */
  fileName: string
  /** The cheapest pack that holds this card (dearer packs hold it too), or null when every pack does. */
  minPack: string | null
  /** What one more tag on this card pays right now (0 = its tags are paid out). */
  nextTagReward: number
  /** The card's permanent number (v0.8.0), given when it was scanned. */
  number: number
  /** The number as shown: "#000123". */
  code: string
  /** The market price of one copy at every treatment id, held or not. */
  prices: Record<string, number>
  /** What one copy sells for without the wanted bonus, per treatment held (equal to sellPrices unless the card is wanted). */
  plainPrices: Record<string, number>
  /** The sale preview: saleQuotes[t][n - 1] is what selling n copies of treatment t pays in all. */
  saleQuotes: Record<string, number[]>
  /** Every fuse this card's copies allow or lack copies for, lowest treatment first. */
  fuseOptions: FuseOption[]
  /** The lowest treatment of which you own enough copies to fuse, or null. */
  fuseFrom: string | null
  /** Copies one fuse of this card takes today: 3 at the least, more for a card that is worth more. */
  fuseCopies: number
}

/** One possible fuse of a card (server-computed, for the confirmation before the fuse). */
export interface FuseOption {
  /** Treatment consumed and treatment gained. */
  from: string
  to: string
  /** Copies one fuse consumes, and copies of `from` held. */
  copies: number
  have: number
  /** Enough copies are held. */
  can: boolean
  /** The real change in versions held: +1, 0 or -1. */
  net_versions: number
  /** The fuse uses the last copies of a Foil, Holo or Special (its slot empties). */
  empties: boolean
  /** The copy gained fills an empty slot. */
  fills: boolean
  /** Market value of the copies consumed. */
  value: number
}

/** What an action did to the versions held (v0.8.0): in every reply that can change ownership. */
export interface SlotsChange {
  filled: number
  total: number
  /** Slots filled minus slots emptied by the action. */
  delta: number
  filled_now: { media_id: number; treatment: string }[]
  emptied: { media_id: number; treatment: string }[]
  /** How much the total moved (only a scan changes it). */
  total_delta: number
}

export interface PackType {
  id: string
  name: string
  blurb: string
  cost: number
  size: number // cards per pack
  hue: number // pack art color (generated, original)
  /** This pack's own chance per card slot for each treatment id (0..1); empty until the config loads. */
  odds: Record<string, number>
}

export interface PulledCard {
  card: Card
  isNew: boolean
  /** Roll landed just short of the next tier: UI plays an "almost" shimmer. */
  nearMiss: boolean
  /** The card as it now stands in the collection (best treatment). `card` shows the copy pulled. */
  collectionCard?: Card
  /** What the pull did: a card you didn't own, a rarer copy, a treatment you held no copy of (below your best one), a duplicate that upgraded your copy (older versions), or a duplicate kept as an extra copy. */
  outcome: 'new' | 'better' | 'version' | 'upgraded' | 'duplicate'
  /** Coins paid for a duplicate (0 since v0.4.0: a duplicate is kept as a copy). */
  coins: number
  /** What the copy just pulled is worth on the market today. */
  value: number
  /** The copy filled an empty version slot (a Foil, Holo or Special not held before). Never with `alreadyOwned`. */
  fillsSlot?: boolean
  /** A duplicate of a treatment already held while a higher slot of the card is still empty. */
  alreadyOwned?: boolean
  /** v0.9.0: the face-down glow decided by the server: a hint that can be wrong. */
  hint?: boolean
  /** The name of the treatment the revealed card shows ("Foil"). */
  treatmentName?: string
}

export interface NextMilestone { at: number; reward: { pack: string; count: number }[]; progress?: number; versionsNeeded?: number; cards?: number }

export interface Wallet {
  coins: number
  packsOpened: number
  /** Today's price per pack id (prices rise as you play). Missing id = use the pack's base cost. */
  packPrices: Record<string, number>
  streakDays: number
  /** ISO date of the last daily check-in, or null. */
  lastActiveDay: string | null
  /** Completeness, 0..1, as the server computes it: slotsFilled / slotsTotal. */
  completeness: number
  /** Versions above Normal held now (a card's Foil, Holo and Special each count once). */
  slotsFilled: number
  /** Three per card in the folder: Foil, Holo, Special. */
  slotsTotal: number
  /**
   * The next milestone that still pays. Milestones are measured against the largest library seen:
   * `progress` is that share (equal to `completeness` unless files went missing), `versionsNeeded`
   * how many more versions reach it, `cards` the size of that largest library.
   */
  nextMilestone: NextMilestone | null
  /** Sealed packs owned, per pack id: bought or claimed, not opened yet. */
  inventory: Record<string, number>
  /** Every card that exists (all media), how many of them you pulled, and how many of those are tagged. */
  cardsTotal: number
  cardsOwned: number
  cardsTagged: number
  /** Copies owned per treatment id, duplicates included. */
  copies: Record<string, number>
  /** How many of each pack the coins buy right now (the price rises per pack), at most 50. */
  packMax: Record<string, number>
  /** Per pack id: how many cards no cheaper pack holds, and how many of those you still miss. */
  packExclusives: Record<string, { cards: number; missing: number }>
  /** What every copy you own is worth at today's prices. */
  collectionValue: number
  /** The cards you hold now, priced at the last 6-hour update against the one before: a fraction (0.04 = up 4%) and the same in coins. */
  collectionChange: { change: number; coins: number }
  /** The market in one glance, and when it moves next. */
  market: { tick: number; index: number; mood: string; change: number; nextTickAt: string | null; tickHours: number }
  /** The shop deal of this window, or null. */
  deal: { pack: string; discount: number; fullPrice: number; price: number; left: number; endsAt: string } | null
  /** The daily tag rush. */
  rush: { active: boolean; endsAt: string | null; available: boolean; multiplier: number; minutes: number; /** v0.9.0: when the next rush can be started (ISO), or null when one is ready or running. */ availableAt?: string | null }
  /** A collector's open offer for one of your cards, or null. */
  offer: { mediaId: number; treatment: string; price: number; marketPrice: number; sellPrice: number; expiresAt: string; /** Copies of that treatment held, and whether taking the offer empties its version slot. */ copies?: number; emptiesSlot?: boolean } | null
  /** Copies of one card needed to fuse it into the next treatment. */
  fuseCopies: number
  /** Today's check-in is still to be claimed, and what it pays. */
  canCheckIn: boolean
  /** The daily free pack: which pack, and whether it can be opened right now. */
  freePack: { pack: string | null; available: boolean; claimedToday: boolean; needsCheckin: boolean }
  /** v0.9.0: packs in a row without a Holo or Special. For looks only (the face-down glow): it changes no odds. */
  dryStreak: number
  /** v0.9.0: milestones reached and waiting to be claimed, and their packs in all. */
  milestonesClaimable: { at: number; reward: { pack: string; count: number }[] }[]
  milestonesClaimablePacks: { pack: string; count: number }[]
}
