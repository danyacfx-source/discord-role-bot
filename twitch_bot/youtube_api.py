import json
import logging
import os
import time

import aiohttp

log = logging.getLogger("youtube_api")

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeAPI:
    def __init__(self, config):
        self.config = config or {}
        self._session = None
        self._access_token = None
        self._token_expires = 0

    async def _get_session(self):
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _ensure_token(self):
        if self._access_token and time.time() < self._token_expires - 60:
            return self._access_token

        client_id = self.config.get("client_id", "")
        client_secret = self.config.get("client_secret", "")
        refresh_token = self.config.get("refresh_token", "")
        if not all([client_id, client_secret, refresh_token]):
            return None

        session = await self._get_session()
        try:
            async with session.post(TOKEN_URL, data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }) as resp:
                data = await resp.json()
                if "access_token" in data:
                    self._access_token = data["access_token"]
                    self._token_expires = time.time() + data.get("expires_in", 3600)
                    return self._access_token
                log.error("YouTube OAuth: %s", data.get("error_description", "?"))
        except Exception:
            log.exception("YouTube OAuth: ошибка")
        return None

    async def request(self, method, endpoint, params=None, json_body=None):
        token = await self._ensure_token()
        if not token:
            return None

        session = await self._get_session()
        url = f"{API_BASE}{endpoint}"
        headers = {"Authorization": f"Bearer {token}"}
        if json_body:
            headers["Content-Type"] = "application/json; charset=UTF-8"

        try:
            if method == "GET":
                async with session.get(url, params=params, headers=headers) as resp:
                    return await resp.json()
            elif method == "POST":
                async with session.post(url, params=params, headers=headers, json=json_body) as resp:
                    return await resp.json()
            elif method == "DELETE":
                async with session.delete(url, params=params, headers=headers) as resp:
                    return await resp.json()
            elif method == "PUT":
                async with session.put(url, params=params, headers=headers, json=json_body) as resp:
                    return await resp.json()
        except Exception:
            log.exception("YouTube API: ошибка %s %s", method, endpoint)
        return None

    async def get_channel_id(self, handle):
        data = await self.request("GET", "/channels", params={
            "part": "id,snippet,statistics",
            "forHandle": handle,
        })
        if not data:
            return None
        items = data.get("items", [])
        return items[0] if items else None

    async def get_channel_stats(self, channel_id=None, handle=None):
        if not channel_id and handle:
            item = await self.get_channel_id(handle)
            if not item:
                return None
            channel_id = item["id"]

        data = await self.request("GET", "/channels", params={
            "part": "snippet,statistics,contentDetails",
            "id": channel_id,
        })
        if not data:
            return None
        items = data.get("items", [])
        return items[0] if items else None

    async def get_recent_videos(self, channel_id, max_results=5):
        data = await self.request("GET", "/search", params={
            "part": "snippet",
            "channelId": channel_id,
            "order": "date",
            "type": "video",
            "maxResults": max_results,
        })
        if not data:
            return []
        return data.get("items", [])

    async def get_video_stats(self, video_ids):
        data = await self.request("GET", "/videos", params={
            "part": "statistics,snippet",
            "id": ",".join(video_ids),
        })
        if not data:
            return []
        return data.get("items", [])

    async def get_live_broadcasts(self, status="all"):
        data = await self.request("GET", "/liveBroadcasts", params={
            "part": "snippet,status,contentDetails",
            "broadcastStatus": status,
            "maxResults": 5,
        })
        if not data:
            return []
        return data.get("items", [])

    async def create_live_broadcast(self, title, description="", scheduled_start=None, privacy="unlisted"):
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "scheduledStartTime": scheduled_start,
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
            "contentDetails": {
                "enableAutoStart": True,
                "enableAutoStop": True,
                "enableContentEncryption": False,
                "enableDvr": True,
                "enableEmbed": True,
                "recordFromStart": True,
                "enableClosedCaptions": False,
            },
        }
        data = await self.request("POST", "/liveBroadcasts", params={"part": "snippet,status,contentDetails"}, json_body=body)
        if not data:
            return None
        if "error" in data:
            log.error("Создание стрима: %s", data["error"].get("message", "?"))
            return None
        return data

    async def delete_live_broadcast(self, broadcast_id):
        return await self.request("DELETE", "/liveBroadcasts", params={"id": broadcast_id})

    async def transition_broadcast(self, broadcast_id, status):
        return await self.request("POST", "/liveBroadcasts/transition", params={
            "broadcastStatus": status,
            "id": broadcast_id,
        }, json_body={})

    async def bind_broadcast_to_stream(self, broadcast_id, stream_id):
        return await self.request("POST", "/liveBroadcasts/bind", params={
            "id": broadcast_id,
            "streamId": stream_id,
        }, json_body={})

    async def get_live_streams(self, status="all"):
        data = await self.request("GET", "/liveStreams", params={
            "part": "snippet,status,cdn",
            "maxResults": 5,
        })
        if not data:
            return []
        return data.get("items", [])

    async def create_live_stream(self, title, resolution="1080p"):
        stream_key = f"ytbot_{int(time.time())}"
        body = {
            "snippet": {"title": title},
            "cdn": {
                "frameRate": "60fps",
                "ingestionType": "rtmp",
                "resolution": resolution,
            },
            "contentDetails": {
                "isReusable": True,
            },
        }
        data = await self.request("POST", "/liveStreams", params={"part": "snippet,status,cdn,contentDetails"}, json_body=body)
        if not data or "error" in data:
            log.error("Создание стрима: %s", data)
            return None
        return data

    async def get_video_comments(self, video_id, max_results=50):
        data = await self.request("GET", "/commentThreads", params={
            "part": "snippet",
            "videoId": video_id,
            "maxResults": max_results,
            "order": "relevance",
            "textFormat": "plainText",
        })
        if not data:
            return []
        return data.get("items", [])

    async def delete_comment(self, comment_id):
        return await self.request("DELETE", "/comments", params={"id": comment_id})

    async def mark_comment_as_spam(self, comment_id):
        return await self.request("POST", "/comments/markAsSpam", params={"id": comment_id}, json_body={})

    async def set_moderation_status(self, comment_id, status):
        return await self.request("POST", "/comments/setModerationStatus", params={
            "id": comment_id,
            "moderationStatus": status,
        }, json_body={})

    async def get_comment_threads(self, video_id, max_results=100, page_token=None):
        params = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": max_results,
            "order": "time",
            "textFormat": "plainText",
        }
        if page_token:
            params["pageToken"] = page_token
        data = await self.request("GET", "/commentThreads", params=params)
        if not data:
            return [], None
        return data.get("items", []), data.get("nextPageToken")

    async def moderate_video_comments(self, video_id, banned_words=None, max_results=100):
        if not banned_words:
            banned_words = []

        items, _ = await self.get_comment_threads(video_id, max_results)
        deleted = 0

        for item in items:
            snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            text = snippet.get("textDisplay", "").lower()
            comment_id = item.get("id", "")
            author = snippet.get("authorDisplayName", "???")

            for word in banned_words:
                if word.lower() in text:
                    log.warning("YouTube комментарий спам: %s — %s", author, snippet.get("textDisplay", "")[:60])
                    await self.delete_comment(comment_id)
                    deleted += 1
                    break

        return deleted
