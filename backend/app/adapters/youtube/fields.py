import re
from collections.abc import Mapping

# no category inference: topicDetails is never requested and categoryId never leaves google
FORBIDDEN_PARTS = frozenset({"topicDetails"})
FORBIDDEN_FIELDS = frozenset({"topicDetails", "categoryId", "topicCategories", "relevantTopicIds"})

CHANNEL_PARTS = "snippet,contentDetails,statistics,status,brandingSettings"
CHANNEL_FIELDS = (
    "items(id,etag,snippet(title,description,customUrl,publishedAt,thumbnails,country,defaultLanguage),"
    "contentDetails/relatedPlaylists/uploads,"
    "statistics(viewCount,subscriberCount,hiddenSubscriberCount,videoCount),"
    "status(madeForKids,privacyStatus),"
    "brandingSettings/channel(keywords,description,country,defaultLanguage))"
)

VIDEO_PARTS = (
    "snippet,contentDetails,status,statistics,player,liveStreamingDetails,paidProductPlacementDetails"
)
VIDEO_ITEM_FIELDS = (
    "id,etag,"
    "snippet(publishedAt,channelId,channelTitle,title,description,thumbnails,tags,liveBroadcastContent,"
    "defaultLanguage,defaultAudioLanguage),"
    "contentDetails(duration,caption,licensedContent,regionRestriction,contentRating/ytRating),"
    "status(uploadStatus,privacyStatus,embeddable,publicStatsViewable,madeForKids),"
    "statistics(viewCount,likeCount,commentCount),"
    "player(embedWidth,embedHeight),"
    "liveStreamingDetails(actualStartTime,actualEndTime,scheduledStartTime),"
    "paidProductPlacementDetails"
)
VIDEO_FIELDS = f"items({VIDEO_ITEM_FIELDS})"
VIDEO_FIELDS_LOCALIZED = f"items({VIDEO_ITEM_FIELDS},localizations)"

BATCH_STATS_PARTS = "id,snippet,statistics,contentDetails"

PLAYLIST_ITEM_PARTS = "snippet,contentDetails,status"
PLAYLIST_ITEM_FIELDS = (
    "etag,nextPageToken,pageInfo/totalResults,"
    "items(snippet(position,resourceId/videoId),contentDetails(videoId,videoPublishedAt),status/privacyStatus)"
)

PLAYLIST_FIELDS = "items(id,snippet/channelId)"

SEARCH_FIELDS = "nextPageToken,pageInfo/totalResults,items(id(kind,videoId,channelId),snippet/channelId)"

COMMENT_PARTS = "snippet,replies"
COMMENT_FIELDS = (
    "nextPageToken,items(id,snippet(channelId,totalReplyCount,"
    "topLevelComment(id,snippet(textOriginal,likeCount,publishedAt,updatedAt,authorChannelId))),"
    "replies/comments(id,snippet(parentId,authorChannelId,textOriginal,likeCount,publishedAt)))"
)

_TOKEN = re.compile(r"[A-Za-z]+")


class PolicyViolation(ValueError):
    pass


def assert_policy_safe(params: Mapping[str, str]) -> None:
    parts = {part.strip() for part in params.get("part", "").split(",")}
    if parts & FORBIDDEN_PARTS:
        raise PolicyViolation(f"forbidden part requested: {sorted(parts & FORBIDDEN_PARTS)}")
    field_tokens = set(_TOKEN.findall(params.get("fields", "")))
    if field_tokens & FORBIDDEN_FIELDS:
        raise PolicyViolation(f"forbidden field requested: {sorted(field_tokens & FORBIDDEN_FIELDS)}")
