---
title: Adding media to your collection
category: Collection
order: 10
---
Everything in your collection comes from one folder. By default that is `media`, inside the app's folder.
You can pick another folder with **Choose folder…** on the setup screen, see [How do I change my media folder?](#help:change-media-folder).
(Advanced: `MCO_MEDIA_DIR` in the `.env` file sets the default; a folder chosen in the app wins.)

- Copy or move images and videos into that folder. Subfolders are fine.
- **The app looks at the folder only when you ask.** Choosing a folder scans it. After you add files
  later, press **⟳ Scan** in the top bar (or **Scan for new media** on your **Player** page, or
  **Scan folder** on the setup screen).
  Nothing runs in the background.
- **A scan that would add files asks first.** When you already have cards, a dialog says what the
  scan will do before it starts: "Adding 12 media will add 36 version slots. Versions: 23% → 19%",
  with **Scan** and **Cancel**. Every new file is a new card with three empty version slots, so the
  percentage drops a little. Your first import, and a scan that finds nothing new, ask nothing.
- **Afterwards** a message says "+12 media · +36 version slots" with the link **Open packs**, and
  the pill in the top bar steps to the new numbers ("+36 slots").
- Images: JPG, PNG, GIF, WebP and BMP. Videos: MP4, WebM, MOV, M4V, MKV and AVI. Other files are ignored.
- A file that is still being copied is added once it stops growing, at the end of the scan.
- While a scan runs, a thin **Scanning your folder** bar shows the progress at the top. You can
  keep using the app meanwhile. A scan only reads each file once; pictures for the cards are made
  later, when a card is first shown.
- **New files become cards inside the packs.** You don't see them in your Collection until you
  pull them. Each new card gets a print run, which sets how scarce and how valuable it is, and a
  permanent six-digit number such as #000412.
  See [The card market](#help:card-market).
- Each file is identified by its contents. The same file added twice only counts once.
- **Renaming or moving a file is safe.** The card keeps its tags and everything you own of it.
  If you delete a file, its card is hidden. If you put the file back, the card returns with its tags.
- Files that cannot be read (damaged, empty or unsupported) are skipped without any error.
- Your files are **never moved, renamed or modified**. The app only stores information *about* them.
  Thumbnails are kept in the app's `data/thumbs` folder.
- Videos show a still taken by your browser, because the app does not decode video itself.
- **The first import is free of coins.** The first scan of a folder pays nothing, and so does a
  scan within 5 minutes of it. Picking a different media folder imports that folder free too.
  After that, new files pay a little each, up to a daily limit. The question before a scan has a
  row **Coins for the new files**, and the result says what the scan paid and why it paid less
  ("0 coins: the first import pays nothing", "+200 coins (daily limit reached: 200 of 200)").

While the folder has no photos or videos, the app shows only the setup screen.
See [Why do I only see the setup screen?](#help:setup-screen)
