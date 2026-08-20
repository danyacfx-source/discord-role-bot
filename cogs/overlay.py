import logging
from datetime import datetime, timedelta, timezone

from discord.ext import commands
from aiohttp import web

from config import OVERLAY, CONFIG
from db import counter_list
from twitch_bot import overlay_state
from twitch_bot.raid_state import state as raid_state

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
</style>
</head>
<body>
  <div id="root"></div>
<script>
async function load() {
  try {
    const r = await fetch('/overlay/api');
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


class Overlay(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._runner = None
        self._site = None

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
        state = overlay_state.get_state()
        raid = raid_state()
        payload = {
            "stream": await self._stream_payload(),
            "map": state.get("map"),
            "quest": state.get("quest"),
            "allergy": state.get("allergy"),
            "counters": [{"name": n, "value": v} for n, v in counter_list("dendosicsh") if n != "tod"],
            "raid": {
                "status": raid.get("status"),
                "started_at": raid.get("started_at"),
                "streak": raid.get("streak", 0),
                "best_streak": raid.get("best_streak", 0),
                "total_raids": raid.get("total_raids", 0),
                "last_map": raid.get("last_map"),
            },
        }
        return web.json_response(payload)

    async def _page(self, request):
        prestream_min = (CONFIG.get("twitch") or {}).get("live") or {}
        prestream_min = prestream_min.get("prestream_timer_minutes", 5)
        html = PAGE.replace("__PRESTREAM_MINUTES__", str(prestream_min))
        return web.Response(text=html, content_type="text/html")

    async def cog_load(self):
        if not OVERLAY.get("enabled", False):
            log.info("Overlay отключён в конфиге")
            return
        app = web.Application()
        app.router.add_get("/overlay", self._page)
        app.router.add_get("/overlay/api", self._api)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        host = OVERLAY.get("host", "127.0.0.1")
        port = OVERLAY.get("port", 8765)
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        log.info("Overlay запущен на http://%s:%s/overlay", host, port)

    async def cog_unload(self):
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()


async def setup(bot: commands.Bot):
    await bot.add_cog(Overlay(bot))
