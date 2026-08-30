import json, asyncio, aiohttp

cfg = json.load(open("config.json", encoding="utf-8"))
yt = cfg.get("youtube", {})
print("client_id:", yt.get("client_id", "")[:10] + "...")
print("secret:", bool(yt.get("client_secret")))
print("refresh:", bool(yt.get("refresh_token")))

async def test():
    async with aiohttp.ClientSession() as s:
        async with s.post("https://oauth2.googleapis.com/token", data={
            "client_id": yt["client_id"],
            "client_secret": yt["client_secret"],
            "refresh_token": yt["refresh_token"],
            "grant_type": "refresh_token",
        }) as r:
            d = await r.json()
            if "access_token" in d:
                print("TOKEN OK, expires:", d.get("expires_in"))
                tk = d["access_token"]
                async with s.get(
                    "https://www.googleapis.com/youtube/v3/liveBroadcasts",
                    headers={"Authorization": "Bearer " + tk},
                    params={"part": "id,snippet,status", "broadcastStatus": "all", "maxResults": 5},
                ) as r2:
                    bd = await r2.json()
                    items = bd.get("items", [])
                    print("Broadcasts:", len(items))
                    for b in items:
                        st = b.get("status", {})
                        sn = b.get("snippet", {})
                        print(" ", b.get("id", "?")[:20], "bs=", st.get("broadcastStatus"), "lc=", st.get("lifeCycleStatus"), "title=", sn.get("title", "?")[:50])
            else:
                print("TOKEN FAIL:", d)

asyncio.run(test())
