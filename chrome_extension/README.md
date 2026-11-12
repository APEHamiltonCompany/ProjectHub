# Project Hub Tab Groups

Chrome extension that turns a Project Hub project page into a Chrome tab group, the same way you keep a tab group per project by hand.

## Load it

1. Open `chrome://extensions` in Chrome.
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked**.
4. Select this folder (`C:\repos\Projects\ProjectHub\chrome_extension`).

## Use it

1. Start the hub (`run_hub.cmd`, or the `ProjectHub` scheduled task).
2. Open a project page, for example `http://localhost:8765/p/0207852`.
3. Click **Open as tab group**.

The extension asks the hub for the project's links, then either adds any missing links to the existing tab group whose title matches the project or creates a new group containing the hub page plus every link. Links that are already open as loose (ungrouped) tabs, for example after **Open all links**, are moved into the group instead of being skipped. Group titles look like `Amgen Inc. QTE-056793`.

Every click ends with a message at the bottom of the hub page: either "Tab group ... ready" or the reason it failed.

## After updating

When the files in this folder change, click **Reload** on *Project Hub Tab Groups* in `chrome://extensions`. Version 2.0.0 re-attaches itself to hub tabs that are already open. If the page says the old version is loaded, or that the extension isn't responding, press F5 on the hub tab.
