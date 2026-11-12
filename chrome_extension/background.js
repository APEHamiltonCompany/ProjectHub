const HUB = "http://localhost:8765";
const HUB_MATCHES = ["http://localhost:8765/*", "http://127.0.0.1:8765/*"];

chrome.runtime.onInstalled.addListener(injectIntoOpenHubTabs);
chrome.runtime.onStartup.addListener(injectIntoOpenHubTabs);

async function injectIntoOpenHubTabs() {
  const tabs = await chrome.tabs.query({ url: HUB_MATCHES });
  for (const tab of tabs) {
    try {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
    } catch (error) {
      console.warn("inject failed", tab.id, error);
    }
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== "openGroup") {
    return false;
  }
  const windowId = sender.tab ? sender.tab.windowId : chrome.windows.WINDOW_ID_CURRENT;
  const senderTabId = sender.tab ? sender.tab.id : null;
  openGroup(message.key, windowId, senderTabId)
    .then((result) => sendResponse(Object.assign({ ok: true }, result)))
    .catch((error) => sendResponse({ ok: false, error: String(error && error.message ? error.message : error) }));
  return true;
});

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function retry(fn, attempts = 8) {
  let lastError;
  for (let i = 0; i < attempts; i++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;
      await sleep(150 * (i + 1));
    }
  }
  throw lastError;
}

function tabUrl(tab) {
  return tab.pendingUrl || tab.url || "";
}

async function openGroup(key, windowId, senderTabId) {
  const response = await fetch(`${HUB}/api/project/${encodeURIComponent(key)}/links`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`hub returned ${response.status}`);
  }
  const data = await response.json();
  const urls = [data.hub_url].concat((data.links || []).map((link) => link.url)).filter(Boolean);

  const groups = await chrome.tabGroups.query({ windowId });
  const existing = groups.find((group) => group.title === data.title);
  const windowTabs = await chrome.tabs.query({ windowId });

  const inGroup = new Set();
  if (existing) {
    for (const tab of windowTabs) {
      if (tab.groupId === existing.id) {
        inGroup.add(tabUrl(tab));
      }
    }
  }

  const tabIds = [];
  let created = 0;
  let adopted = 0;
  for (const url of urls) {
    if (inGroup.has(url)) {
      continue;
    }
    const loose = windowTabs.find(
      (tab) => tab.id !== senderTabId && tab.groupId === -1 && tabUrl(tab) === url && !tabIds.includes(tab.id)
    );
    if (loose) {
      tabIds.push(loose.id);
      adopted += 1;
    } else {
      const tab = await chrome.tabs.create({ url, windowId, active: false });
      tabIds.push(tab.id);
      created += 1;
    }
    inGroup.add(url);
  }

  let groupId = existing ? existing.id : null;
  if (tabIds.length) {
    const options = { tabIds };
    if (groupId !== null) {
      options.groupId = groupId;
    } else {
      options.createProperties = { windowId };
    }
    groupId = await retry(() => chrome.tabs.group(options));
  }
  if (groupId === null) {
    return { groupId: null, created: 0, adopted: 0 };
  }
  await retry(() => chrome.tabGroups.update(groupId, { title: data.title, collapsed: false }));
  const members = await chrome.tabs.query({ groupId });
  if (members.length) {
    await retry(() => chrome.tabs.update(members[0].id, { active: true }));
  }
  return { groupId, created, adopted, title: data.title, existing: Boolean(existing) };
}
