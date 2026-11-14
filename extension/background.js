// Collects .m3u8 requests per tab (including ones from cross-origin player iframes).
const key = (tabId) => `tab_${tabId}`;

async function addUrl(tabId, url) {
  const k = key(tabId);
  const data = (await chrome.storage.session.get(k))[k] || [];
  const i = data.indexOf(url);
  if (i !== -1) data.splice(i, 1);
  data.push(url); // most recent last
  await chrome.storage.session.set({ [k]: data.slice(-50) });
  chrome.action.setBadgeText({ tabId, text: String(data.length) });
  chrome.action.setBadgeBackgroundColor({ tabId, color: "#2e7d32" });
}

chrome.webRequest.onBeforeRequest.addListener(
  (d) => {
    if (d.tabId >= 0 && /\.m3u8(\?|#|$)/i.test(d.url)) addUrl(d.tabId, d.url);
  },
  { urls: ["<all_urls>"] }
);

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.url) chrome.storage.session.remove(key(tabId)); // new page in tab
});
chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove(key(tabId)));
