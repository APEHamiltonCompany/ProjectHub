(() => {
  if (window.__projectHubTabGroups) {
    return;
  }
  window.__projectHubTabGroups = true;
  document.documentElement.dataset.hubExtension = "2";

  function reply(requestId, payload) {
    window.postMessage(Object.assign({ source: "project-hub-ext", requestId }, payload), window.location.origin);
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== window.location.origin) {
      return;
    }
    const data = event.data;
    if (!data || data.source !== "project-hub-page" || data.type !== "openGroup" || !data.key) {
      return;
    }
    try {
      chrome.runtime.sendMessage({ type: "openGroup", key: data.key }, (response) => {
        if (chrome.runtime.lastError) {
          reply(data.requestId, { ok: false, error: chrome.runtime.lastError.message });
          return;
        }
        reply(data.requestId, response || { ok: false, error: "no response from extension" });
      });
    } catch (error) {
      reply(data.requestId, { ok: false, error: "Extension was reloaded - refresh this page (F5)", stale: true });
    }
  });

  reply("", { type: "ready" });
})();
