import logging

from twitch_bot.youtube_token import get_token_manager

log = logging.getLogger("youtube_api")

API_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeAPI:
    def __init__(self, config):
        self.config = config or {}

    async def close(self):
        pass

    async def request(self, method, endpoint, params=None, json_body=None):
        tm = get_token_manager()
        kwargs = {}
        if params:
            kwargs["params"] = params
        if json_body:
            kwargs["json"] = json_body
        return await tm.api(method, endpoint, **kwargs)

    async def get_channel_id(self, handle):
        data = await self.request("GET", "/channels", params={
            "part": "id,snippet,statistics",
            "forHandle": handle,
        })
        if data and data.get("items"):
            return data["items"][0]
        return None

    async def get_channel_stats(self, channel_id):
        data = await self.request("GET", "/channels", params={
            "part": "statistics,snippet",
            "id": channel_id,
        })
        if data and data.get("items"):
            return data["items"][0]
        return None

    async def get_recent_videos(self, channel_id, max_results=5):
        data = await self.request("GET", "/search", params={
            "part": "id,snippet",
            "channelId": channel_id,
            "order": "date",
            "maxResults": max_results,
            "type": "video",
        })
        if data:
            return data.get("items", [])
        return []

    async def get_video_stats(self, video_ids):
        data = await self.request("GET", "/videos", params={
            "part": "statistics,snippet",
            "id": ",".join(video_ids) if isinstance(video_ids, list) else video_ids,
        })
        if data:
            return data.get("items", [])
        return []

    async def get_live_broadcasts(self, status="all"):
        data = await self.request("GET", "/liveBroadcasts", params={
            "part": "id,snippet,status,contentDetails",
            "broadcastStatus": status,
            "maxResults": 10,
        })
        if data:
            return data.get("items", [])
        return []

    async def create_live_broadcast(self, title, description="", scheduled_start=None, privacy="unlisted"):
        body = {
            "snippet": {
                "title": title,
                "description": description,
            },
            "status": {
                "privacyStatus": privacy,
            },
            "contentDetails": {
                "enableAutoStart": False,
                "enableAutoStop": False,
            },
        }
        if scheduled_start:
            body["snippet"]["scheduledStartTime"] = scheduled_start
        return await self.request("POST", "/liveBroadcasts", params={"part": "snippet,status,contentDetails"}, json_body=body)

    async def delete_live_broadcast(self, broadcast_id):
        return await self.request("DELETE", "/liveBroadcasts", params={"id": broadcast_id})

    async def transition_broadcast(self, broadcast_id, status):
        return await self.request("POST", "/liveBroadcasts/transition", params={
            "broadcastStatus": status,
            "id": broadcast_id,
        })

    async def bind_broadcast_to_stream(self, broadcast_id, stream_id):
        return await self.request("POST", "/liveBroadcasts/bind", params={
            "id": broadcast_id,
            "streamId": stream_id,
        })

    async def get_live_streams(self):
        data = await self.request("GET", "/liveStreams", params={
            "part": "id,snippet,status,cfg",
            "maxResults": 10,
        })
        if data:
            return data.get("items", [])
        return []

    async def create_live_stream(self, title, resolution="1080p"):
        res_map = {"1080p": (1920, 1080), "720p": (1280, 720), "480p": (854, 480)}
        w, h = res_map.get(resolution, (1920, 1080))
        body = {
            "snippet": {"title": title},
            "cdn": {
                "frameRate": "60fps",
                "ingestionType": "rtmp",
            },
            "contentDetails": {
                "isReusable": True,
            },
        }
        return await self.request("POST", "/liveStreams", params={"part": "snippet,cdn,contentDetails"}, json_body=body)

    async def get_video_comments(self, video_id, max_results=20):
        data = await self.request("GET", "/commentThreads", params={
            "part": "snippet",
            "videoId": video_id,
            "maxResults": max_results,
            "order": "relevance",
        })
        if data:
            return data.get("items", [])
        return []

    async def delete_comment(self, comment_id):
        return await self.request("DELETE", "/comments", params={"id": comment_id})

    async def mark_comment_as_spam(self, comment_id):
        return await self.request("POST", "/comments/markAsSpam", params={"id": comment_id})

    async def set_moderation_status(self, comment_id, status, moderationLevel="publishedForReview"):
        return await self.request("POST", "/comments/setModerationStatus", params={
            "id": comment_id,
            "status": status,
            "moderationLevel": moderationLevel,
        })

    async def get_comment_threads(self, video_id, max_results=20):
        data = await self.request("GET", "/commentThreads", params={
            "part": "snippet",
            "videoId": video_id,
            "maxResults": max_results,
            "order": "relevance",
        })
        if data:
            return data.get("items", []), data.get("nextPageToken")
        return [], None

    async def moderate_video_comments(self, video_id, banned_words, max_results=50):
        if not banned_words:
            return 0
        import re
        pattern = re.compile(r"(?i)\b(" + "|".join(re.escape(w) for w in banned_words) + r")\b")
        deleted = 0
        items, _ = await self.get_comment_threads(video_id, max_results=max_results)
        for item in items:
            snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            text = snippet.get("textDisplay", "")
            if pattern.search(text):
                cid = item.get("id")
                if cid:
                    await self.delete_comment(cid)
                    deleted += 1
        return deleted
