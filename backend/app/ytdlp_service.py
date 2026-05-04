from __future__ import annotations

import copy
import json
import math
import re
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests
from fastapi import HTTPException
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from app.config import settings


@dataclass(frozen=True)
class PlatformStrategyRule:
    download_strategy: str = "server"
    direct_link_strategy: str = "server"


PLATFORM_STRATEGY_RULES: dict[str, PlatformStrategyRule] = {
    "XiaoHongShu": PlatformStrategyRule(download_strategy="server", direct_link_strategy="server"),
    "BiliBili": PlatformStrategyRule(download_strategy="server", direct_link_strategy="server"),
}


def _build_base_options() -> dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "format": "b/bv*+ba/best",
        "ignorenoformatserror": True,
        "socket_timeout": 20,
        "extractor_retries": 1,
        "retries": 1,
        "fragment_retries": 1,
    }


class YtDlpService:
    @lru_cache(maxsize=64)
    def _parse_video_cached(self, url: str) -> dict[str, Any]:
        try:
            info = self.extract_info(url, process=True)
            formats = self._normalize_formats(info.get("formats", []))
            extractor = self._safe_text(info.get("extractor_key") or info.get("extractor"))
            return {
                "title": self._safe_text(info.get("title")) or "Untitled",
                "webpage_url": self._safe_text(info.get("webpage_url")) or url,
                "thumbnail": self._select_thumbnail(info.get("thumbnail"), info.get("thumbnails")),
                "duration": self._safe_float(info.get("duration")),
                "extractor": extractor,
                "uploader": self._resolve_uploader(info, url),
                "description": self._safe_text(info.get("description")),
                "download_strategy": self._recommend_strategy(formats, extractor),
                "formats": formats,
            }
        except HTTPException as exc:
            if self._should_use_douyin_fallback(url, exc):
                return self._parse_douyin_share_video(url)
            raise

    def extract_info(self, url: str, *, process: bool = True) -> dict[str, Any]:
        options = _build_base_options()
        try:
            with YoutubeDL(options) as ydl:
                return ydl.extract_info(url, download=False, process=process)
        except DownloadError as exc:
            message = self._format_download_error(exc)
            raise HTTPException(status_code=400, detail=message) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"解析视频时出现异常：{exc}") from exc

    def parse_video(self, url: str) -> dict[str, Any]:
        return copy.deepcopy(self._parse_video_cached(url))

    def get_direct_link(self, url: str, format_id: str) -> dict[str, Any]:
        info = self.extract_info(url)
        extractor = self._safe_text(info.get("extractor_key") or info.get("extractor"))
        if self._resolve_platform_rule(extractor).direct_link_strategy == "server":
            return {
                "strategy": "server_download",
                "direct_url": None,
                "headers": {},
                "reason": f"{extractor or '当前平台'}已优先切换为服务端下载，以保证下载稳定性。",
            }
        selected = next((item for item in info.get("formats", []) if str(item.get("format_id")) == format_id), None)
        if not selected:
            raise HTTPException(status_code=404, detail="未找到所选格式")

        direct_url = self._safe_text(selected.get("url"))
        protocol = (self._safe_text(selected.get("protocol")) or "").lower()
        http_headers = info.get("http_headers") or {}

        has_video = selected.get("vcodec") not in (None, "none")
        has_audio = selected.get("acodec") not in (None, "none")

        if direct_url and any(key in protocol for key in ["http", "https"]) and has_video and has_audio:
            return {
                "strategy": "redirect",
                "direct_url": direct_url,
                "headers": {k: str(v) for k, v in http_headers.items() if v},
                "reason": None,
            }

        return {
            "strategy": "server_download",
            "direct_url": None,
            "headers": {},
            "reason": "当前平台或格式不适合直接跳转下载，已建议使用服务端下载。",
        }

    def download_video(self, url: str, format_id: str) -> dict[str, Any]:
        if format_id == "douyin-share":
            return self._download_douyin_share_video(url)
        download_id = uuid.uuid4().hex[:10]
        output_template = str(settings.download_dir / f"%(title).80s-{download_id}.%(ext)s")
        effective_format = self._build_download_format(url, format_id)
        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": effective_format,
            "outtmpl": output_template,
            "restrictfilenames": True,
            "windowsfilenames": True,
            "merge_output_format": "mp4",
        }
        before_files = {path for path in settings.download_dir.iterdir() if path.is_file()}
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
                file_path = self._resolve_downloaded_file(before_files)
                if not file_path:
                    prepared = ydl.prepare_filename(info)
                    file_path = Path(prepared)
                if not file_path.exists():
                    raise HTTPException(status_code=500, detail="下载完成但未找到文件")
                return {
                    "file_name": file_path.name,
                    "file_size": file_path.stat().st_size,
                }
        except DownloadError as exc:
            http_exc = HTTPException(status_code=400, detail=f"下载失败：{exc}")
            if self._should_use_douyin_fallback(url, http_exc):
                return self._download_douyin_share_video(url)
            raise http_exc from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"服务端下载异常：{exc}") from exc

    def _resolve_downloaded_file(self, before_files: set[Path]) -> Path | None:
        after_files = [path for path in settings.download_dir.iterdir() if path.is_file() and path not in before_files]
        if not after_files:
            return None
        after_files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return after_files[0]

    def _recommend_strategy(self, formats: list[dict[str, Any]], extractor: str | None = None) -> str:
        rule = self._resolve_platform_rule(extractor)
        if rule.download_strategy == "server":
            return "server"
        if any((item.get("protocol") or "").startswith(("http", "https")) and item.get("has_video") and item.get("has_audio") for item in formats[:5]):
            return "direct"
        return "server"

    def _resolve_platform_rule(self, extractor: str | None) -> PlatformStrategyRule:
        if not extractor:
            return PlatformStrategyRule()
        return PLATFORM_STRATEGY_RULES.get(extractor, PlatformStrategyRule())

    def _build_download_format(self, url: str, format_id: str) -> str:
        if format_id == "douyin-share":
            return format_id
        info = self.extract_info(url)
        formats = info.get("formats", [])
        selected = next((item for item in formats if self._safe_text(item.get("format_id")) == format_id), None)
        if not selected:
            return format_id

        has_video = selected.get("vcodec") not in (None, "none")
        has_audio = selected.get("acodec") not in (None, "none")
        if has_video and has_audio:
            return format_id
        if has_audio and not has_video:
            return format_id

        audio_formats = [
            item for item in formats
            if item.get("acodec") not in (None, "none") and item.get("vcodec") in (None, "none")
        ]
        audio_formats.sort(key=lambda item: self._safe_int(item.get("abr") or item.get("tbr")) or 0, reverse=True)
        best_audio = self._safe_text(audio_formats[0].get("format_id")) if audio_formats else None
        return f"{format_id}+{best_audio}" if best_audio else format_id

    def _should_use_douyin_fallback(self, url: str, exc: HTTPException) -> bool:
        detail = str(exc.detail)
        return "douyin.com" in url and "Fresh cookies" in detail

    def _parse_douyin_share_video(self, url: str) -> dict[str, Any]:
        share_data = self._extract_douyin_share_data(url)
        title = self._safe_text(share_data.get("desc")) or "Douyin Video"
        uploader = self._safe_text((share_data.get("author") or {}).get("nickname"))
        duration = self._safe_float((share_data.get("video") or {}).get("duration"))
        if duration and duration > 1000:
            duration = round(duration / 1000, 2)
        thumbnail = self._pick_first_url((share_data.get("video") or {}).get("cover"))
        video_url = self._resolve_douyin_video_url(share_data)
        filesize = self._probe_filesize(video_url) if video_url else None
        formats = []
        if video_url:
            formats.append(
                {
                    "format_id": "douyin-share",
                    "ext": "mp4",
                    "resolution": self._format_resolution((share_data.get("video") or {}).get("width"), (share_data.get("video") or {}).get("height")),
                    "filesize": filesize,
                    "filesize_mb": round(filesize / 1024 / 1024, 2) if filesize else None,
                    "video_codec": None,
                    "audio_codec": None,
                    "format_note": "Douyin share-page fallback",
                    "protocol": "https",
                    "has_video": True,
                    "has_audio": True,
                    "recommended": True,
                }
            )
        return {
            "title": title,
            "webpage_url": self._build_douyin_share_url(url),
            "thumbnail": thumbnail,
            "duration": duration,
            "extractor": "DouyinShareFallback",
            "uploader": uploader,
            "description": title,
            "download_strategy": "server",
            "formats": formats,
        }

    def _download_douyin_share_video(self, url: str) -> dict[str, Any]:
        share_data = self._extract_douyin_share_data(url)
        video_url = self._resolve_douyin_video_url(share_data)
        if not video_url:
            raise HTTPException(status_code=400, detail="下载失败：抖音分享页兜底解析未找到可用视频地址")

        title = self._safe_text(share_data.get("desc")) or "douyin-video"
        safe_stem = self._sanitize_filename(title)[:80] or "douyin-video"
        file_name = f"{safe_stem}-{uuid.uuid4().hex[:10]}.mp4"
        file_path = settings.download_dir / file_name

        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
            "Referer": "https://www.iesdouyin.com/",
        }
        try:
            with requests.get(video_url, headers=headers, stream=True, timeout=120) as response:
                response.raise_for_status()
                with file_path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            handle.write(chunk)
        except Exception as exc:
            file_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"下载失败：抖音分享页兜底下载异常：{exc}") from exc

        return {
            "file_name": file_path.name,
            "file_size": file_path.stat().st_size,
        }

    def _extract_douyin_share_data(self, url: str) -> dict[str, Any]:
        share_url = self._build_douyin_share_url(url)
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
            "Referer": "https://www.iesdouyin.com/",
        }
        try:
            response = requests.get(share_url, headers=headers, timeout=30, allow_redirects=True)
            response.raise_for_status()
            html = response.text
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"解析失败：抖音分享页访问失败：{exc}") from exc

        match = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})</script>", html, re.S)
        if not match:
            raise HTTPException(status_code=400, detail="解析失败：抖音分享页兜底解析未找到页面数据")

        try:
            payload = json.loads(match.group(1))
            item_list = payload["loaderData"]["video_(id)/page"]["videoInfoRes"]["item_list"]
            if not item_list:
                raise KeyError("item_list empty")
            return item_list[0]
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"解析失败：抖音分享页兜底解析页面数据失败：{exc}") from exc

    def _build_douyin_share_url(self, url: str) -> str:
        aweme_id = self._extract_douyin_aweme_id(url)
        return f"https://www.iesdouyin.com/share/video/{aweme_id}/"

    def _extract_douyin_aweme_id(self, url: str) -> str:
        match = re.search(r"/video/(\d+)", url)
        if match:
            return match.group(1)
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True, timeout=30)
            response.raise_for_status()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"解析失败：无法解析抖音分享链接：{exc}") from exc
        match = re.search(r"/video/(\d+)", response.url)
        if not match:
            raise HTTPException(status_code=400, detail="解析失败：未能从抖音分享链接中提取视频 ID")
        return match.group(1)

    def _resolve_douyin_video_url(self, share_data: dict[str, Any]) -> str | None:
        url_list = ((share_data.get("video") or {}).get("play_addr") or {}).get("url_list") or []
        direct_url = next((self._decode_escaped_url(item) for item in url_list if item), None)
        if not direct_url:
            return None
        direct_url = direct_url.replace("playwm", "play")
        try:
            response = requests.get(
                direct_url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.iesdouyin.com/"},
                allow_redirects=False,
                timeout=30,
            )
            redirect_url = response.headers.get("location")
            return redirect_url or direct_url
        except Exception:
            return direct_url

    def _decode_escaped_url(self, value: str) -> str:
        return value.encode("utf-8").decode("unicode_escape")

    def _pick_first_url(self, asset: Any) -> str | None:
        if not isinstance(asset, dict):
            return None
        url_list = asset.get("url_list") or []
        for item in url_list:
            text = self._safe_text(item)
            if text:
                return self._decode_escaped_url(text)
        return None

    def _format_resolution(self, width: Any, height: Any) -> str | None:
        safe_width = self._safe_int(width)
        safe_height = self._safe_int(height)
        if safe_width and safe_height:
            return f"{safe_width}x{safe_height}"
        return None

    def _probe_filesize(self, url: str | None) -> int | None:
        if not url:
            return None
        try:
            response = requests.head(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.iesdouyin.com/"},
                allow_redirects=True,
                timeout=30,
            )
            return self._safe_int(response.headers.get("Content-Length"))
        except Exception:
            return None

    def _sanitize_filename(self, value: str) -> str:
        sanitized = re.sub(r'[\\/:*?\"<>|]+', '-', value)
        sanitized = re.sub(r'\s+', ' ', sanitized).strip(' .')
        return sanitized or 'video'

    def _safe_text(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _safe_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(number) or math.isinf(number):
            return None
        return round(number, 2)

    def _safe_int(self, value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return None
        return number if number >= 0 else None

    def _select_thumbnail(self, primary: Any, thumbnails: Any) -> str | None:
        best = self._pick_thumbnail(thumbnails)
        return best or self._safe_text(primary)

    def _pick_thumbnail(self, thumbnails: Any) -> str | None:
        if not isinstance(thumbnails, list):
            return None
        ranked: list[tuple[int, str]] = []
        for item in thumbnails:
            if not isinstance(item, dict):
                continue
            url = self._safe_text(item.get("url"))
            if not url:
                continue
            score = 0
            if "nd_dft" in url:
                score += 20
            if "nd_prv" in url:
                score += 10
            width = self._safe_int(item.get("width")) or 0
            height = self._safe_int(item.get("height")) or 0
            score += width + height
            ranked.append((score, url))
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[0][1]

    def _resolve_uploader(self, info: dict[str, Any], url: str) -> str | None:
        candidates = [
            self._safe_text(info.get("uploader")),
            self._safe_text(info.get("channel")),
            self._safe_text(info.get("creator")),
        ]
        uploader_id = self._safe_text(info.get("uploader_id"))
        if "xiaohongshu.com" in url:
            author = self._extract_xiaohongshu_author(url)
            if author and author != uploader_id:
                return author
            return next((item for item in candidates if item and item != uploader_id), None)
        return next((item for item in candidates if item), None) or uploader_id

    def _extract_xiaohongshu_author(self, url: str) -> str | None:
        if "xiaohongshu.com" not in url:
            return None
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            html = response.content.decode("utf-8", errors="ignore")
        except Exception:
            return None
        matches = re.findall(r'"nickname"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', html)
        for raw in matches:
            try:
                nickname = json.loads(f'"{raw}"')
            except Exception:
                nickname = raw
            nickname = self._safe_text(nickname)
            if nickname and not re.fullmatch(r"[0-9a-f]{24}", nickname):
                return nickname
        return None

    def _format_download_error(self, exc: DownloadError) -> str:
        message = str(exc)
        if "Failed to extract play info" in message:
            return "解析失败：当前平台页面结构发生变化，建议先确认 yt-dlp 已更新到最新版；如果刚更新过，可能是平台临时变更导致，请稍后重试。"
        if "Requested format is not available" in message:
            return "解析失败：当前视频格式信息暂不可用，请稍后重试或尝试其他视频链接。"
        return f"解析失败：{message}"

    def _normalize_formats(self, formats: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in formats:
            format_id = self._safe_text(item.get("format_id"))
            if not format_id:
                continue
            has_video = item.get("vcodec") not in (None, "none")
            has_audio = item.get("acodec") not in (None, "none")
            filesize = self._safe_int(item.get("filesize") or item.get("filesize_approx"))
            width = self._safe_int(item.get("width"))
            height = self._safe_int(item.get("height"))
            resolution = self._safe_text(item.get("resolution"))
            if not resolution and width and height:
                resolution = f"{width}x{height}"
            normalized.append(
                {
                    "format_id": format_id,
                    "ext": self._safe_text(item.get("ext")),
                    "resolution": resolution,
                    "filesize": filesize,
                    "filesize_mb": round(filesize / 1024 / 1024, 2) if filesize else None,
                    "video_codec": self._safe_text(item.get("vcodec")),
                    "audio_codec": self._safe_text(item.get("acodec")),
                    "format_note": self._safe_text(item.get("format_note") or item.get("format")),
                    "protocol": self._safe_text(item.get("protocol")),
                    "has_video": has_video,
                    "has_audio": has_audio,
                    "recommended": has_video and has_audio,
                }
            )
        normalized.sort(
            key=lambda entry: (
                0 if entry["has_video"] and entry["has_audio"] else 1,
                0 if entry["has_video"] else 1,
                0 if entry["recommended"] else 1,
                -(entry.get("filesize") or 0),
            )
        )
        return normalized[: settings.max_formats]


yt_dlp_service = YtDlpService()
