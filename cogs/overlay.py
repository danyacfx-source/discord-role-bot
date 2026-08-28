import logging
import secrets
from datetime import datetime, timedelta, timezone

from discord.ext import commands
from aiohttp import web

from config import OVERLAY, CONFIG
from db import counter_list
from twitch_bot import overlay_state
from twitch_bot.chat_overlay import get_chat_messages
from twitch_bot.raid_state import state as raid_state
from twitch_bot.song_queue import SongQueue
from twitch_bot.youtube_chat import get_yt_chat_messages

log = logging.getLogger("overlay")

MSK = timezone(timedelta(hours=3))

PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Overlay</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: transparent;
    font-family: 'Segoe UI', Roboto, Arial, sans-serif;
    color: #fff;
    width: 420px;
    overflow: hidden;
  }
  .card {
    background: rgba(20, 22, 31, 0.82);
    border-left: 4px solid #9146ff;
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 12px;
    box-shadow: 0 4px 18px rgba(0,0,0,.45);
    backdrop-filter: blur(4px);
  }
  .card h3 {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #a7a9be;
    margin-bottom: 6px;
  }
  .card .value { font-size: 16px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
  .card .empty { color: #6a6c82; font-size: 14px; }
  .badge { display:inline-block; font-size:12px; font-weight:700; padding:2px 9px; border-radius:20px; margin-right:6px; }
  .badge.live { background:#e74c3c; }
  .badge.off { background:#555768; }
  .stream-line { font-size:14px; margin-bottom:2px; }
  .counters { font-size:14px; line-height:1.6; }
  .counters b { color:#f1c40f; }
  .raid-timer { font-size:34px; font-weight:800; letter-spacing:2px; }
  .raid-ok { color:#2ecc71; }
  .raid-bad { color:#e74c3c; }
  .raid-run { color:#f1c40f; }
  .raid-stats { font-size:13px; color:#a7a9be; margin-top:4px; }
  .uptime { font-size:14px; color:#a7a9be; margin-top:2px; }
  .prestream { text-align:center; }
  .prestream-timer { font-size:42px; font-weight:800; letter-spacing:2px; color:#9146ff; margin:6px 0; }
  .prestream-label { font-size:15px; color:#a7a9be; }
  .song-current { font-size:15px; color:#1db954; margin-bottom:6px; }
  .song-queue { font-size:13px; color:#a7a9be; }
  .song-queue div { margin-bottom:2px; }
  .song-note { font-size:12px; color:#6a6c82; margin-top:4px; }
</style>
</head>
<body>
  <div id="root"></div>
<script>
async function load() {
  try {
    const r = await fetch('/overlay/api?token=__OVERLAY_TOKEN__');
    const d = await r.json();
    let h = '';
    const PRESTREAM_MIN = __PRESTREAM_MINUTES__;
    if (d.stream && d.stream.live && d.stream.started_at) {
      var elapsed = (Date.now() / 1000) - Date.parse(d.stream.started_at) / 1000;
      var remain = PRESTREAM_MIN * 60 - elapsed;
      if (remain > 0) {
        window._prestreamEnd = Date.parse(d.stream.started_at) / 1000 + PRESTREAM_MIN * 60;
        h += '<div class="card prestream"><h3>🎬 Стрим скоро начнётся</h3>';
        h += '<div class="prestream-timer" id="prestreamTimer">--:--</div>';
        h += '<div class="prestream-label">приносим покой и уют</div></div>';
      }
    }
    if (d.map) {
      h += '<div class="card"><h3>🎒 Рандом</h3><div class="value">' + esc(d.map) + '</div></div>';
    }
    if (d.quest) {
      h += '<div class="card"><h3>🗺️ Квест</h3><div class="value">' + esc(d.quest) + '</div></div>';
    }
    if (d.counters && d.counters.length) {
      h += '<div class="card"><h3>📦 Лут / счётчики</h3><div class="counters">';
      for (const c of d.counters) h += '<div><b>' + esc(c.name) + '</b>: ' + c.value + '</div>';
      h += '</div></div>';
    }
    if (d.raid) {
      h += '<div class="card"><h3>🎮 Рейд</h3>';
      h += '<div class="raid-stats">Всего рейдов: <b>' + (d.raid.total_raids || 0) + '</b>';
      if (d.raid.last_map) h += ' · Последняя: ' + esc(d.raid.last_map);
      h += '</div>';
      if (d.raid.status === 'raid') {
        h += '<div class="raid-timer raid-run" id="raidTimer">--:--</div>';
        h += '<div class="raid-stats">Серия: <b>' + d.raid.streak + '</b> · Рекорд: ' + d.raid.best_streak + '</div>';
        window._raidStart = d.raid.started_at;
      } else if (d.raid.status === 'extract') {
        h += '<div class="raid-timer raid-ok">✅ Выжил</div>';
        h += '<div class="raid-stats">Серия: <b>' + d.raid.streak + '</b> · Рекорд: ' + d.raid.best_streak + '</div>';
      } else if (d.raid.status === 'dead') {
        h += '<div class="raid-timer raid-bad">💀 Умер</div>';
        h += '<div class="raid-stats">Рекорд: ' + d.raid.best_streak + '</div>';
      } else {
        h += '<div class="raid-stats">Нет активного рейда</div>';
      }
      h += '</div>';
    }
    if (d.song && (d.song.current || d.song.queue.length)) {
      h += '<div class="card"><h3>🎵 Музыка</h3>';
      if (d.song.current) {
        h += '<div class="song-current">▶️ ' + esc(d.song.current.title) + '</div>';
        h += '<div class="song-note">заказал: ' + esc(d.song.current.requester) + '</div>';
      }
      if (d.song.queue.length) {
        h += '<div class="song-queue">';
        for (var i = 0; i < d.song.queue.length; i++) {
          h += '<div>' + (i+1) + '. ' + esc(d.song.queue[i].title) + ' (' + esc(d.song.queue[i].requester) + ')</div>';
        }
        if (d.song.total > 5) h += '<div>... и ещё ' + (d.song.total - 5) + '</div>';
        h += '</div>';
      }
      h += '</div>';
    }
    document.getElementById('root').innerHTML = h;
  } catch (e) { /* ignore */ }
}
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}
load();
setInterval(load, 4000);
setInterval(function () {
  var el = document.getElementById('raidTimer');
  if (!el || !window._raidStart) return;
  var s = Math.floor((Date.now() / 1000) - window._raidStart);
  if (s < 0) s = 0;
  var m = Math.floor(s / 60);
  var sec = s % 60;
  el.textContent = (m < 10 ? '0' + m : m) + ':' + (sec < 10 ? '0' + sec : sec);
}, 1000);
setInterval(function () {
  var el = document.getElementById('prestreamTimer');
  if (!el || !window._prestreamEnd) return;
  var remain = Math.floor(window._prestreamEnd - Date.now() / 1000);
  if (remain <= 0) {
    el.parentElement.style.display = 'none';
    return;
  }
  var m = Math.floor(remain / 60);
  var sec = remain % 60;
  el.textContent = (m < 10 ? '0' + m : m) + ':' + (sec < 10 ? '0' + sec : sec);
}, 1000);
</script>
</body>
</html>
"""


YT_CHAT_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>YT Chat</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: transparent;
    font-family: 'Segoe UI', Roboto, Arial, sans-serif;
    color: #fff;
    width: 360px;
    height: 100vh;
    overflow: hidden;
  }
  #chat {
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    height: 100%;
    padding: 8px;
  }
  .msg {
    background: rgba(20, 22, 31, 0.0);
    border-left: 3px solid #FF0000;
    border-radius: 8px;
    padding: 8px 10px;
    margin-bottom: 6px;
    animation: fadeIn 0.3s ease;
  }
  .msg.owner { border-left-color: #FFD700; }
  .msg.mod   { border-left-color: #5e84f1; }
  .msg-header {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 3px;
  }
  .msg-avatar {
    width: 20px; height: 20px; border-radius: 50%;
    object-fit: cover;
  }
  .msg-author {
    font-size: 12px;
    font-weight: 700;
    color: #a7a9be;
  }
  .msg-author.owner { color: #FFD700; }
  .msg-author.mod   { color: #5e84f1; }
  .msg-badge {
    font-size: 10px;
    padding: 1px 5px;
    border-radius: 3px;
    font-weight: 700;
  }
  .msg-badge.owner { background: #FFD700; color: #000; }
  .msg-badge.mod   { background: #5e84f1; color: #fff; }
  .msg-text {
    font-size: 14px;
    line-height: 1.4;
    word-break: break-word;
    color: #e8e8e8;
  }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
  }
</style>
</head>
<body>
  <div id="chat"></div>
<script>
let lastId = 0;
async function load() {
  try {
    const r = await fetch('/yt-chat/api?token=__OVERLAY_TOKEN__');
    const d = await r.json();
    const box = document.getElementById('chat');
    const msgs = d.messages || [];
    const newMsgs = msgs.filter(m => m.id > lastId);
    for (const m of newMsgs) {
      const div = document.createElement('div');
      div.className = 'msg' + (m.is_owner ? ' owner' : m.is_mod ? ' mod' : '');
      let badgeHtml = '';
      if (m.is_owner) badgeHtml = '<span class="msg-badge owner">OWNER</span>';
      else if (m.is_mod) badgeHtml = '<span class="msg-badge mod">MOD</span>';
      let avatarHtml = m.avatar ? '<img class="msg-avatar" src="' + esc(m.avatar) + '" onerror="this.style.display=none">' : '';
      div.innerHTML =
        '<div class="msg-header">' + avatarHtml +
        '<span class="msg-author' + (m.is_owner ? ' owner' : m.is_mod ? ' mod' : '') + '">' + esc(m.author) + '</span>' +
        badgeHtml + '</div>' +
        '<div class="msg-text">' + esc(m.text) + '</div>';
      box.appendChild(div);
    }
    if (newMsgs.length) lastId = Math.max(...newMsgs.map(m => m.id));
    while (box.children.length > 30) box.removeChild(box.firstChild);
  } catch (e) {}
}
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}
load();
setInterval(load, 2000);
</script>
</body>
</html>
"""


POPUP_CHAT_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Pop-up Chat</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: transparent; font-family: 'Segoe UI', Roboto, Arial, sans-serif; width: 380px; overflow: hidden; }
  #wrap { transition: opacity .6s ease; }
  #wrap.idle { opacity: .12; }
  #chat { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; padding: 6px; }
  .msg {
    max-width: 100%;
    background: rgba(12, 14, 20, .58);
    border: 1px solid rgba(255,255,255,.06);
    border-left: 3px solid #9146ff;
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 13px;
    line-height: 1.35;
    color: #e8e8ee;
    animation: pop .22s ease-out;
    word-break: break-word;
  }
  .msg.src-yt { border-left-color: #ff1f1f; }
  .msg .av { width: 18px; height: 18px; border-radius: 50%; margin-right: 8px; vertical-align: middle; object-fit: cover; display: none; }
  .msg .nick { font-weight: 700; margin-right: 6px; }
  .msg .nick.twitch { color: #b070ff; }
  .msg .nick.yt { color: #ff6b6b; }
  .msg .nick.owner { color: #ffd700; }
  .msg .nick.mod { color: #5e84f1; }
  .msg .nick.vip { color: #e46bff; }
  .msg .nick.sub { color: #9b59b6; }
  .msg .tag { display: inline-block; font-size: 10px; font-weight: 700; padding: 0 5px; border-radius: 4px; margin-right: 6px; vertical-align: middle; }
  .msg .tag.owner { background: #ffd700; color: #000; }
  .msg .tag.mod { background: #5e84f1; color: #fff; }
  @keyframes pop { from { opacity: 0; transform: translateY(10px) scale(.98); } to { opacity: 1; transform: none; } }
</style>
</head>
<body>
  <div id="wrap"><div id="chat"></div></div>
<script>
let lastId = 0;
let lastNew = 0;
async function load() {
  try {
    const r = await fetch('/chat/api?token=__OVERLAY_TOKEN__');
    const d = await r.json();
    const ms = d.messages || [];
    let max = 0;
    for (const m of ms) if (m.id > max) max = m.id;
    if (ms.length && max < lastId) lastId = 0;
    const newMs = ms.filter(m => m.id > lastId);
    for (const m of newMs) { lastId = m.id; append(m); lastNew = Date.now(); }
  } catch (e) {}
}
function append(m) {
  const box = document.getElementById('chat');
  const div = document.createElement('div');
  div.className = 'msg src-' + m.source;
  let av = m.avatar ? '<img class="av" src="' + esc(m.avatar) + '" onload="this.style.display=\'inline-block\'" onerror="this.style.display=\'none\'">' : '';
  let tag = '';
  if (m.role === 'owner') tag = '<span class="tag owner">OWNER</span>';
  else if (m.role === 'mod') tag = '<span class="tag mod">MOD</span>';
  div.innerHTML = av + tag + '<span class="nick ' + m.role + ' ' + m.source + '">' + esc(m.author) + ':</span><span class="text"> ' + esc(m.text) + '</span>';
  box.appendChild(div);
  while (box.children.length > 10) box.removeChild(box.firstChild);
}
function esc(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
setInterval(load, 2000);
setInterval(function () {
  const w = document.getElementById('wrap');
  if (Date.now() - lastNew > 12000) w.classList.add('idle');
  else w.classList.remove('idle');
}, 1000);
load();
</script>
</body>
</html>
"""


class Overlay(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._runner = None
        self._site = None
        self._auth_token = OVERLAY.get("token") or secrets.token_urlsafe(32)

    async def _get_live(self):
        try:
            cog = self.bot.get_cog("Twitch")
            if cog is None or cog.live is None:
                return None
            return cog.live
        except Exception:
            return None

    async def _stream_payload(self):
        live = await self._get_live()
        if live is None:
            return {"live": False, "viewers": 0, "title": None, "next": None}
        info = await live.get_live_stream_info()
        if info:
            peak = getattr(live, "_peak_viewers", 0) or 0
            return {
                "live": True,
                "viewers": await live.get_viewer_count() or 0,
                "peak": peak,
                "title": info.get("title"),
                "started_at": info.get("created_at"),
                "next": None,
            }
        next_start = await live.get_next_schedule_start()
        next_text = None
        if next_start is not None:
            delta = next_start - datetime.now(timezone.utc)
            secs = int(delta.total_seconds())
            if secs > 0:
                hours, rem = divmod(secs, 3600)
                minutes = rem // 60
                if hours > 0:
                    next_text = f"{hours} ч {minutes} мин"
                else:
                    next_text = f"{max(1, minutes)} мин"
        return {"live": False, "viewers": 0, "title": None, "next": next_text}

    async def _api(self, request):
        token = request.headers.get("X-Overlay-Token") or request.query.get("token")
        if not secrets.compare_digest(token or "", self._auth_token):
            return web.json_response({"error": "unauthorized"}, status=401)
        state = overlay_state.get_state()
        raid = raid_state()
        song_queue = SongQueue()
        current_track = song_queue.get_current()
        upcoming = song_queue.get_queue()[:5]
        payload = {
            "stream": await self._stream_payload(),
            "map": state.get("map"),
            "quest": state.get("quest"),
            "allergy": state.get("allergy"),
            "counters": [{"name": n, "value": v} for n, v in counter_list((CONFIG.get("twitch") or {}).get("channel", "")) if n != "tod"],
            "raid": {
                "status": raid.get("status"),
                "started_at": raid.get("started_at"),
                "streak": raid.get("streak", 0),
                "best_streak": raid.get("best_streak", 0),
                "total_raids": raid.get("total_raids", 0),
                "last_map": raid.get("last_map"),
            },
            "song": {
                "current": current_track,
                "queue": upcoming,
                "total": song_queue.length(),
            },
        }
        return web.json_response(payload)

    async def _page(self, request):
        prestream_min = (CONFIG.get("twitch") or {}).get("live") or {}
        prestream_min = prestream_min.get("prestream_timer_minutes", 5)
        html = PAGE.replace("__PRESTREAM_MINUTES__", str(prestream_min))
        html = html.replace("__OVERLAY_TOKEN__", self._auth_token)
        return web.Response(text=html, content_type="text/html")

    async def _yt_chat_page(self, request):
        html = YT_CHAT_PAGE.replace("__OVERLAY_TOKEN__", self._auth_token)
        return web.Response(text=html, content_type="text/html")

    async def _yt_chat_api(self, request):
        token = request.headers.get("X-Overlay-Token") or request.query.get("token")
        if not secrets.compare_digest(token or "", self._auth_token):
            return web.json_response({"error": "unauthorized"}, status=401)
        messages = get_yt_chat_messages()
        payload = {
            "messages": [
                {
                    "id": i,
                    "author": m["author"],
                    "text": m["text"],
                    "badge": m["badge"],
                    "is_owner": m["is_owner"],
                    "is_mod": m["is_mod"],
                    "avatar": m["avatar"],
                }
                for i, m in enumerate(messages)
            ],
        }
        return web.json_response(payload)

    async def _chat_page(self, request):
        html = POPUP_CHAT_PAGE.replace("__OVERLAY_TOKEN__", self._auth_token)
        return web.Response(text=html, content_type="text/html")

    async def _chat_api(self, request):
        token = request.headers.get("X-Overlay-Token") or request.query.get("token")
        if not secrets.compare_digest(token or "", self._auth_token):
            return web.json_response({"error": "unauthorized"}, status=401)
        payload = {
            "messages": [
                {
                    "id": m["id"],
                    "source": m["source"],
                    "author": m["author"],
                    "text": m["text"],
                    "role": m["role"],
                    "avatar": m["avatar"],
                }
                for m in get_chat_messages()
            ],
        }
        return web.json_response(payload)

    async def cog_load(self):
        if not OVERLAY.get("enabled", False):
            log.info("Overlay отключён в конфиге")
            return
        app = web.Application()
        app.router.add_get("/overlay", self._page)
        app.router.add_get("/overlay/api", self._api)
        app.router.add_get("/yt-chat", self._yt_chat_page)
        app.router.add_get("/yt-chat/api", self._yt_chat_api)
        app.router.add_get("/chat", self._chat_page)
        app.router.add_get("/chat/api", self._chat_api)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        host = OVERLAY.get("host", "127.0.0.1")
        port = OVERLAY.get("port", 8765)
        try:
            self._site = web.TCPSite(self._runner, host, port)
            await self._site.start()
            log.info("Overlay запущен на http://%s:%s/overlay", host, port)
        except OSError as e:
            log.error("Overlay: не удалось занять порт %s — %s", port, e)
            await self._runner.cleanup()
            self._runner = None

    async def cog_unload(self):
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()


async def setup(bot: commands.Bot):
    await bot.add_cog(Overlay(bot))
