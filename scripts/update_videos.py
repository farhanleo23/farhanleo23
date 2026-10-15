"""Refresh school uploads and curated Microsoft tutorials using public YouTube data."""

import base64
from dataclasses import dataclass
from html import escape
import json
from pathlib import Path
import re
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


CHANNELS = (
    {"key": "school", "handle": "SchoolofMachineLearning", "name": "School of Machine Learning", "id": "UC6z5hBakH7U2NgISHtH--Xg"},
    {"key": "microsoft", "name": "Microsoft 365 Developer", "url": "https://www.youtube.com/@Microsoft365Developer",
     "video_ids": ("gyfE5fTcels", "gcYz-l6R6FQ")},
)
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")


@dataclass(frozen=True)
class Video:
    id: str
    title: str


def fetch(url):
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; ProfileVideoCards/1.0)",
        "Accept-Language": "en-US,en;q=0.9",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                body = response.read(8_000_001)
            if len(body) > 8_000_000:
                raise ValueError("Response exceeds size limit")
            return body
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def valid_video(video_id, title):
    if not VIDEO_ID.fullmatch(video_id or "") or not isinstance(title, str):
        return None
    title = " ".join(title.split())[:500]
    return Video(video_id, title) if title else None


def parse_feed(body, expected_channel):
    root = ET.fromstring(body)
    if root.findtext("yt:channelId", namespaces=NS) != expected_channel:
        raise ValueError("Feed channel does not match the configured channel")
    result = []
    for entry in root.findall("a:entry", NS):
        video = valid_video(entry.findtext("yt:videoId", namespaces=NS), entry.findtext("a:title", namespaces=NS))
        if video and video.id not in {v.id for v in result}:
            result.append(video)
    if not result:
        raise ValueError("No videos found in feed")
    return result[:2]


def parse_channel_page(body, expected_channel):
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    marker = re.search(r"(?:var\s+)?ytInitialData\s*=\s*", text)
    if not marker:
        raise ValueError("Public video listing is unavailable")
    data, _ = json.JSONDecoder().raw_decode(text[marker.end():])
    metadata = data.get("metadata", {}).get("channelMetadataRenderer", {})
    if metadata.get("externalId") != expected_channel:
        raise ValueError("Page channel does not match the configured channel")
    tabs = data.get("contents", {}).get("twoColumnBrowseResultsRenderer", {}).get("tabs", [])
    content = next((tab["tabRenderer"].get("content") for tab in tabs if tab.get("tabRenderer", {}).get("selected")), None)
    if not content:
        raise ValueError("The selected video tab is unavailable")
    result = []

    def visit(node):
        if len(result) >= 2:
            return
        if isinstance(node, dict):
            video = None
            if "videoRenderer" in node:
                item = node["videoRenderer"]
                title = item.get("title", {})
                video = valid_video(item.get("videoId"), title.get("simpleText") or "".join(run.get("text", "") for run in title.get("runs", [])))
            elif "lockupViewModel" in node:
                item = node["lockupViewModel"]
                title = item.get("metadata", {}).get("lockupMetadataViewModel", {}).get("title", {}).get("content")
                video = valid_video(item.get("contentId"), title)
            else:
                for child in node.values():
                    visit(child)
            if video and video.id not in {v.id for v in result}:
                result.append(video)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(content)
    if not result:
        raise ValueError("No videos found on public channel page")
    return result


def latest_videos(channel, fetcher):
    try:
        return parse_feed(fetcher(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel['id']}"), channel["id"])
    except (urllib.error.URLError, TimeoutError, ValueError, ET.ParseError):
        return parse_channel_page(fetcher(f"https://www.youtube.com/@{channel['handle']}/videos"), channel["id"])


def featured_videos(channel, fetcher):
    result = []
    for video_id in channel["video_ids"]:
        if not VIDEO_ID.fullmatch(video_id):
            raise ValueError("Invalid featured video ID")
        query = urllib.parse.urlencode({"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"})
        metadata = json.loads(fetcher("https://www.youtube.com/oembed?" + query))
        if metadata.get("author_url", "").rstrip("/").lower() != channel["url"].rstrip("/").lower():
            raise ValueError("Featured video publisher does not match the configured channel")
        video = valid_video(video_id, metadata.get("title"))
        if not video:
            raise ValueError("Featured video title is unavailable")
        result.append(video)
    return result


def card_svg(video, channel_name, thumbnail, theme):
    if not thumbnail.startswith(b"\xff\xd8"):
        raise ValueError("YouTube thumbnail is not a JPEG")
    palette = {"dark": ("#151b23", "#e6edf3", "#a5adb8", "#3d444d", "#c5b5ff"),
               "light": ("#ffffff", "#1f2328", "#59636e", "#d1d9e0", "#7045cb")}
    bg, fg, muted, border, accent = palette[theme]
    lines = textwrap.wrap(video.title, width=39, break_long_words=True, break_on_hyphens=False)
    if len(lines) > 2:
        lines = lines[:2]
        lines[1] = lines[1].rstrip(" .") + "…"
    title = "\n".join(f'<text x="22" y="{374 + i * 33}" font-size="25" font-weight="600">{escape(line)}</text>' for i, line in enumerate(lines))
    encoded = base64.b64encode(thumbnail).decode("ascii")
    return f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 600 474" role="img" aria-labelledby="title desc">
<title id="title">{escape(video.title)}</title><desc id="desc">{escape(channel_name)}. Watch this video on YouTube.</desc>
<defs><clipPath id="thumb"><rect x="1" y="1" width="598" height="337" rx="12"/></clipPath></defs>
<rect x="1" y="1" width="598" height="472" rx="12" fill="{bg}" stroke="{border}"/>
<image width="600" height="338" preserveAspectRatio="xMidYMid slice" clip-path="url(#thumb)" xlink:href="data:image/jpeg;base64,{encoded}"/>
<g fill="{fg}" font-family="Arial, Helvetica, sans-serif">{title}</g>
<text x="22" y="450" fill="{muted}" font-family="Arial, Helvetica, sans-serif" font-size="20">{escape(channel_name)}</text>
<text x="578" y="450" text-anchor="end" fill="{accent}" font-family="Arial, Helvetica, sans-serif" font-size="20" font-weight="600">WATCH ↗</text>
</svg>
'''


def section_pattern(key):
    start = f"<!-- BEGIN {key.upper()}-VIDEOS -->"
    end = f"<!-- END {key.upper()}-VIDEOS -->"
    return start, end, re.compile(re.escape(start) + r"(.*?)" + re.escape(end), re.S)


def section_markup(channel, videos):
    cards = []
    for index, video in enumerate(videos, 1):
        stem = f"assets/videos/{channel['key']}-{index}"
        cards.append(f'''<a href="https://www.youtube.com/watch?v={video.id}"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="{stem}-dark.svg">
  <img src="{stem}-light.svg" alt="{escape(video.title, quote=True)}" width="360">
</picture></a>''')
    return "<p>\n" + "\n".join(cards) + "\n</p>"


def refresh(root, fetcher=fetch, channels=CHANNELS):
    readme_path = root / "README.md"
    readme = readme_path.read_text()
    catalog_path = root / "assets/videos/catalog.json"
    catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {}
    updated = []
    for channel in channels:
        start, end, pattern = section_pattern(channel["key"])
        matches = list(pattern.finditer(readme))
        if len(matches) != 1:
            raise ValueError(f"Expected one video section for {channel['key']}")
        try:
            videos = featured_videos(channel, fetcher) if "video_ids" in channel else latest_videos(channel, fetcher)
            pending = {}
            for index, video in enumerate(videos, 1):
                thumbnail = fetcher(f"https://i.ytimg.com/vi/{video.id}/mqdefault.jpg")
                for theme in ("dark", "light"):
                    pending[f"assets/videos/{channel['key']}-{index}-{theme}.svg"] = card_svg(video, channel["name"], thumbnail, theme)
        except (urllib.error.URLError, TimeoutError, ValueError, ET.ParseError) as error:
            if "<a href=" not in matches[0].group(1):
                raise RuntimeError(f"Initial video cards failed for {channel['name']}") from error
            message = str(error).replace("\n", " ")[:160]
            print(f"::warning::Retained existing {channel['name']} cards: {message}")
            continue
        for relative, content in pending.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        replacement = start + "\n" + section_markup(channel, videos) + "\n" + end
        readme = pattern.sub(lambda _: replacement, readme, count=1)
        source = {"channel_url": channel["url"], "selection": "featured"} if "video_ids" in channel else {"channel_id": channel["id"]}
        catalog[channel["key"]] = {"channel": channel["name"], **source, "videos": [{"id": v.id, "title": v.title} for v in videos]}
        updated.append(channel["name"])
    readme_path.write_text(readme)
    if updated:
        catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    return updated


if __name__ == "__main__":
    names = refresh(Path(__file__).resolve().parents[1])
    print("Refreshed cards: " + (", ".join(names) if names else "existing cards retained"))
