const API = "http://127.0.0.1:47823";
const $ = (id) => document.getElementById(id);

function pick(urls) {
  // Prefer the master playlist.m3u8; otherwise whatever m3u8 was seen last (e.g. video.m3u8).
  const master = urls.filter((u) => /\/playlist\.m3u8(\?|#|$)/i.test(u));
  return (master.length ? master : urls).at(-1);
}

async function scanPage(tabId) {
  // Fallback for players loaded before the extension started listening.
  try {
    const res = await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      func: () => {
        const found = new Set();
        for (const e of performance.getEntriesByType("resource"))
          if (/\.m3u8(\?|#|$)/i.test(e.name)) found.add(e.name);
        const re = /https?:\/\/[^"'\s<>\\]+?\.m3u8[^"'\s<>\\]*/gi;
        for (const m of document.documentElement.outerHTML.matchAll(re)) found.add(m[0]);
        return [...found];
      },
    });
    return res.flatMap((r) => r.result || []);
  } catch { return []; }
}

async function main() {
  let status;
  try {
    const r = await fetch(`${API}/ping`, { signal: AbortSignal.timeout(1500) });
    status = await r.json();
  } catch {
    $("app").className = "err";
    $("app").innerHTML = "<b>VideoGrabber не запущен.</b><br>Запустите его из Программ (Launchpad / Spotlight → «VideoGrabber») и откройте это окно снова.";
    return;
  }
  const c = status.current;
  $("app").className = "muted";
  $("app").innerHTML = `<span class="ok">● запущен</span> · папка: ${status.download_dir}` +
    (c ? `<br>Сейчас: ${c.title} — ${c.percent}%` : "") +
    (status.queued ? `<br>В очереди: ${status.queued}` : "");

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const k = `tab_${tab.id}`;
  let urls = (await chrome.storage.session.get(k))[k] || [];
  for (const u of await scanPage(tab.id)) if (!urls.includes(u)) urls.unshift(u);

  if (!urls.length) {
    $("msg").innerHTML = "На странице не найдено m3u8. Запустите воспроизведение видео и откройте окно снова.";
    return;
  }
  $("main").hidden = false;
  $("title").value = tab.title || "video";
  const best = pick(urls);
  for (const u of [...urls].reverse()) {
    const o = document.createElement("option");
    o.value = u;
    o.textContent = new URL(u).pathname.split("/").slice(-2).join("/") + "  —  " + new URL(u).host;
    o.title = u;
    if (u === best) o.selected = true;
    $("urls").appendChild(o);
  }

  $("go").onclick = async () => {
    $("go").disabled = true;
    try {
      const r = await fetch(`${API}/download`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: $("urls").value, title: $("title").value, page_url: tab.url }),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error || "ошибка");
      $("msg").innerHTML = `<span class="ok">✓ Добавлено.</span> ` +
        (j.current && j.queued ? `В очереди: ${j.queued}` : "Загрузка началась.");
    } catch (e) {
      $("msg").innerHTML = `<span class="err">Не удалось: ${e.message}</span>`;
      $("go").disabled = false;
    }
  };
}
main();
