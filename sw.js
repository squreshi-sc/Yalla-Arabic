/* Yalla Arabic offline helper (v2.3).
   - App files (page, lessons, icons): network first, saved copy when offline.
   - Audio: saved copy first. Audio file names contain a hash, so a saved file never goes stale.
   - Audio range requests (seeking, car mode) are answered from the saved copy with 206 responses. */
const SHELL = "ya-shell-v1", AUDIO = "ya-audio-v1", FONTS = "ya-fonts-v1";
const SHELL_FILES = ["./", "index.html", "lessons.json", "offline.json", "manifest.webmanifest", "icon.svg"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_FILES)).catch(() => {}).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => ![SHELL, AUDIO, FONTS].includes(k)).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function timeout(ms){ return new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), ms)); }

async function networkFirst(req){
  const cache = await caches.open(SHELL);
  try {
    const res = await Promise.race([fetch(req, {cache: "no-cache"}), timeout(5000)]);
    if (res && res.ok) cache.put(req.mode === "navigate" ? "index.html" : req, res.clone());
    return res;
  } catch (e) {
    const hit = await cache.match(req.mode === "navigate" ? "index.html" : req, {ignoreSearch: true});
    if (hit) return hit;
    throw e;
  }
}

async function rangeFrom(res, range){
  const buf = await res.arrayBuffer(), size = buf.byteLength;
  const m = /bytes=(\d*)-(\d*)/.exec(range || "");
  let start = m && m[1] ? +m[1] : 0, end = m && m[2] ? +m[2] : size - 1;
  if (m && !m[1] && m[2]) { start = Math.max(0, size - +m[2]); end = size - 1; }
  end = Math.min(end, size - 1);
  if (start >= size || start > end) return new Response(null, {status: 416, headers: {"Content-Range": `bytes */${size}`}});
  return new Response(buf.slice(start, end + 1), {status: 206, headers: {
    "Content-Type": res.headers.get("Content-Type") || "audio/mpeg",
    "Content-Range": `bytes ${start}-${end}/${size}`, "Content-Length": String(end - start + 1), "Accept-Ranges": "bytes"}});
}

async function audio(req){
  const url = req.url.split("#")[0].split("?")[0];
  const cache = await caches.open(AUDIO);
  const hit = await cache.match(url);
  const range = req.headers.get("range");
  if (hit) return range ? rangeFrom(hit, range) : hit;
  return fetch(req);   // not downloaded: stream from the internet as usual
}

async function fonts(req){
  const cache = await caches.open(FONTS);
  const hit = await cache.match(req);
  const net = fetch(req).then(res => { if (res && (res.ok || res.type === "opaque")) cache.put(req, res.clone()); return res; }).catch(() => hit);
  return hit || net;
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") { e.respondWith(fonts(req)); return; }
  if (url.origin !== location.origin) return;
  if (url.pathname.includes("/audio/")) { e.respondWith(audio(req)); return; }
  e.respondWith(networkFirst(req));
});
