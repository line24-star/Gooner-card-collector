/**
 * Tour definitions. Add a tour here when a feature ships, and tag the elements it
 * points at with data-tour="…". The id is what gets stored in tutorial progress.
 *
 * ONBOARDING_TOUR_ID runs automatically the first time the app opens.
 */

import type { Tour } from './tour.ts'

export const ONBOARDING_TOUR_ID = 'welcome'

/** The tours that count for the tour reward (one pack, once all of them are completed). Must equal [onboarding] tours in config/economy.toml. */
export const REWARDED_TOUR_IDS = ['welcome', 'organize', 'packs', 'card-panel', 'market'] as const

export const TOURS: Tour[] = [
  {
    id: ONBOARDING_TOUR_ID,
    title: 'Welcome tour',
    description: 'A one-minute look around the app.',
    steps: [
      {
        title: 'Welcome to your collection',
        body: `Every image and video in your media folder is a <b>collectible card</b>, and every card
               starts inside a pack. Open packs to pull your cards, tag the ones you pulled to earn
               coins, and spend the coins on more packs. Chase the rare Foil, Holo and Special copies.
               Your files are never moved or changed.`,
      },
      {
        target: '[data-tour="home-hero"]',
        title: 'What to do now',
        body: `The big button is always the next thing to do: open a sealed pack, buy one, or tag
               the cards you pulled. The counter shows how many of your cards you have pulled so far.`,
      },
      {
        target: '[data-tour="today"]',
        title: 'Every day',
        body: `This side bar holds everything you can claim. Do your <b>daily check-in</b>: it keeps
               your streak going and unlocks a <b>free pack</b>, once a day. It goes to your packs,
               sealed. Rewards are always sealed packs; coins come from tagging and from new files.`,
      },
      {
        target: '[data-tour="checklist"]',
        title: 'Your next steps',
        body: `A short checklist in the side bar to get you started. Tagging your first five cards and
               your first merge each pay a pack. Taking all five tours pays one more.`,
      },
      {
        target: '[data-tour="coins"]',
        title: 'Coins',
        body: `You start with welcome coins: enough for your first packs. Organizing your media earns more,
               and rarer cards pay more. Importing your library earns nothing; files you add later do.`,
      },
      {
        target: '[data-tour="versions-pill"]',
        title: 'Your versions',
        body: `Every card has a Foil, a Holo and a Special version. This counts the versions you hold, of
               three for every card in your folder. It moves the moment you open a pack, merge or sell.
               Click it for the cards that still have a missing version.`,
      },
      {
        target: '[data-tour="nav"]',
        title: 'Get around',
        body: `<b>Home</b> shows what to do next, <b>Collection</b> is your binder,
               <b>Organize</b> tags cards one after another, <b>Packs</b> is the shop,
               <b>Market</b> is where you sell and buy cards and follow their prices,
               <b>Stats</b> has your goals, sets and pack history, and <b>Player</b> is your
               own page: name, coin history, media folder and settings.`,
      },
      {
        target: '[data-tour="help"]',
        title: 'Help & tutorials',
        body: 'Open this any time (or press <kbd>?</kbd>) for FAQs, guides, and to replay tours.',
      },
      {
        target: '[data-tour="stop"]',
        title: 'Stopping the app',
        body: `Click here to shut everything down. The app also closes itself shortly after
               you close the last browser tab.`,
      },
    ],
  },
  {
    id: 'organize',
    title: 'Organize mode',
    description: 'Tag your untagged cards one after another, from the keyboard.',
    page: 'organize',
    steps: [
      {
        target: '[data-tour="organize-input"]',
        title: 'Type a tag',
        body: `Type a tag for the card on screen and press <kbd>Enter</kbd>. The card is tagged,
               you earn coins, and the next untagged card appears. Only cards you pulled from a pack
               show up here. Tags are lowercase and up to 40 characters.`,
      },
      {
        target: '[data-tour="organize-chips"]',
        title: 'Quick tags',
        body: `Your nine most used tags sit here. Click one, or press <kbd>1</kbd> to <kbd>9</kbd>,
               to tag without typing. After a run of very fast quick tags they rest for a few
               seconds: type the next tag, or wait. A wrong tag? Click it on the card's tag row to
               remove it; the coins it paid are taken back.`,
      },
      {
        target: '[data-tour="organize-skip"]',
        title: 'Skip',
        body: `Not sure about this card? Press <kbd>Tab</kbd> or <kbd>→</kbd> to move it to the back of
               the line. <kbd>Esc</kbd> returns to the Collection.`,
      },
      {
        target: '[data-tour="organize-counter"]',
        title: 'Progress and coins',
        body: `How many untagged cards are left and how many coins this visit has earned.
               Cards you own in a rarer version come first because they pay the most.`,
      },
    ],
  },
  {
    id: 'card-panel',
    title: 'The card panel',
    description: 'What you can do with one card: tag it, follow its price, sell copies and merge them.',
    steps: [
      {
        target: '[data-tour="panel-versions"]',
        title: 'Your versions',
        body: `Every card can be owned as Normal, Foil, Holo and Special. Each row shows one of them:
               how many copies you hold, what one sells for, and how far you are from a merge
               ("2 of 3 for a Foil"). A version you do not hold says where to find it. Click a
               miniature, or the big card, to see that version full screen; "Not owned · preview"
               shows a version you do not hold.`,
      },
      {
        target: '[data-tour="panel-value"]',
        title: 'What it is worth',
        body: `The card's value on the market today, how its price moved over the time the chart
               shows ("▼ 3% in 24 h", "▲ 12% this month"), and how many copies of it were printed. A
               card with a small print run is harder to pull and worth more, whatever its treatment.`,
      },
      {
        target: '[data-tour="panel-tags"]',
        title: 'Tags and categories',
        body: `Type a tag and press <kbd>Enter</kbd>. While you type, a list shows matching tags and
               tags that often go together with this card's tags. Every extra tag on a card pays
               a little less, down to 1 coin. Add a category in the second box to file the tag
               under a group, such as <i>beach</i> under <i>travel</i>. Click a tag to remove it:
               the coins it paid are taken back.`,
      },
      {
        target: '[data-tour="panel-price"]',
        title: 'Price history',
        body: `Today's price for the version you pick, how it moved over the last 24 hours, the week
               or the month, and a table of the last 30 days. The market moves every 6 hours.`,
      },
      {
        target: '[data-tour="panel-review"]',
        title: 'Review',
        body: `When you pull a shinier version of a card you had already tagged, this button appears
               with the bonus it pays. It pays once per card and rarity.`,
      },
      {
        // Only on screen while upgrade attempts are switched on (off since v0.7.1): a step whose target is missing is skipped.
        target: '[data-tour="panel-upgrade"]',
        title: 'Try an upgrade',
        body: `Pay coins for a chance to move the card up one rarity. If it fails, the coins are gone
               and the card stays as it was.`,
      },
      {
        target: '[data-tour="panel-versions"] .ver-acts',
        title: 'Sell or merge',
        body: `A duplicate from a pack stays as an extra copy. Sell copies on the market at
               today's price, or merge copies of one treatment into 1 copy of the next: 3 for most
               cards, up to 7 for the most valuable ones (the button says how many). When a sale
               or a merge would empty a version slot, the app asks first and shows what you get
               and what you lose.`,
      },
    ],
  },
  {
    id: 'packs',
    title: 'The pack shop',
    description: 'How to spend coins on packs and what you can pull.',
    page: 'packs',
    steps: [
      {
        target: '[data-tour="pack-inventory"]',
        title: 'Your packs',
        body: `The sealed packs you own. <b>Open</b> lets you pick one of three and tear it open; nothing
               is opened until you pick. A face-down card with a green edge may fill one of your
               versions: a hint that is usually right, not always. Flip the cards one by one, or
               reveal them all. Above the cards a line counts what the pack did for your versions
               as you turn them: "+2 versions" or "No new versions". Click a turned card to open
               its panel.`,
      },
      {
        target: '[data-tour="pack-tile"]',
        title: 'The shop',
        body: `Three packs on a ladder: better odds of a rare copy cost more. Each tile shows how
               often a pack holds a Foil, Holo or Special, today's price and how many cards it holds.
               Every pack pulls from all your photos and videos.`,
      },
      {
        target: '[data-tour="pack-open"]',
        title: 'Buy',
        body: `<b>Buy 1</b> or <b>Buy 10</b> puts sealed packs into your packs above, or type your own
               number: it can't go above what your coins buy. You open them when you want. The
               scarcest cards are only in the dearer packs; each tile says how many.`,
      },
      {
        target: '[data-tour="pack-rates"]',
        title: 'Odds for every pack',
        body: `Open this to compare the odds per card of all three packs, and how much each treatment
               multiplies your coins. The odds are the same for every pack you open.`,
      },
    ],
  },
  {
    id: 'market',
    title: 'The market',
    description: 'What your cards are worth, and how to sell and buy them.',
    page: 'market',
    steps: [
      {
        target: '[data-tour="market-head"]',
        title: 'What your collection is worth',
        body: `Every card has its own price. It follows how scarce the card is and how wanted.
               All prices change at the same moment, every 6 hours; the clock shows when. The
               number here is the value of the cards you hold now.`,
      },
      {
        target: '[data-tour="market-listings"]',
        title: 'Cards for sale',
        body: `Four lists, one per treatment: the 10 most valuable Normal, Foil, Holo and Special
               cards. Buying gives you a copy of that treatment, even if you never pulled the card.
               A mark says when it fills one of your empty version slots.`,
      },
      {
        target: '[data-tour="market-mine"]',
        title: 'Sell your cards',
        body: `Your cards, the most valuable first. Sell one copy, or all your duplicates at once.
               Now and then a collector makes an offer for one of your best cards. It slides in
               as a message and waits behind the ✉ button in the top bar: take it, or wait for
               a better price.`,
      },
      {
        target: '[data-tour="market-news"]',
        title: 'News',
        body: `Reprints push a price down, hype and buyouts push it up, and a card that jumped may
               keep rising or drop back at the next updates. Your own trades do not move a price.
               The news says what happened at each update; click a card's name to open it.`,
      },
    ],
  },
  // Future tours (add as features land): 'stats'.
]

export function findTour(id: string): Tour | undefined {
  return TOURS.find((t) => t.id === id)
}
