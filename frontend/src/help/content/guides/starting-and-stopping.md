---
title: Starting and stopping the app
category: Basics
order: 20
---
**To start:** double-click `start.bat` in the app folder. A console window opens and your
browser opens the app. The first launch installs dependencies and takes a minute or two.

**To stop**, do any one of these:

- Click **Stop** in the top-right corner.
- Close every browser tab with the app. It shuts down about 15 seconds later.
- Press `Ctrl+C` in the console window, or close the window.

Reloading the page is safe. The short delay exists so a reload doesn't stop the app.

Developers can use `start-dev.bat`, which keeps running when tabs close.
See also: [Why did the app stop by itself?](#help:why-did-it-stop)

## Another program uses the same port

Some programs use the same address as this app (many local apps and dev servers also use port 8000).
You can run both. When the usual port is taken, the app starts on the next free one and opens
your browser at the right address. The console window says which port it used.
