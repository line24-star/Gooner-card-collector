---
title: Why did the app stop by itself?
category: Troubleshooting
order: 10
---
The app shuts down automatically about 15 seconds after the last browser tab closes, so it
doesn't keep running in the background. Just run `start.bat` again.

If you don't want this, use `start-dev.bat`, or set `MCO_AUTO_CLOSE=false` in `.env`.
