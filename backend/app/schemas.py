from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


ERROR_INVALID_URL = '\u8bf7\u8f93\u5165\u6709\u6548\u7684\u89c6\u9891\u94fe\u63a5\uff0c\u6216\u5305\u542b\u94fe\u63a5\u7684\u5206\u4eab\u6587\u6848'
URL_TRAILING_CHARS = '\u3002\uff0c\u3001\uff1b\uff1a\uff01\uff1f,.!?)\u3011]\"\''


def _extract_first_url(value: str) -> str:
    text = value.strip()
    match = re.search(r'https?://[^\s]+', text)
    if not match:
        raise ValueError(ERROR_INVALID_URL)
    return match.group(0).rstrip(URL_TRAILING_CHARS)


class ParseRequest(BaseModel):
    url: str = Field(..., min_length=1)

    @field_validator('url')
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return _extract_first_url(value)


class DownloadRequest(BaseModel):
    url: str = Field(..., min_length=1)
    format_id: str = Field(..., min_length=1)

    @field_validator('url')
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return _extract_first_url(value)


class VideoFormat(BaseModel):
    format_id: str
    ext: str | None = None
    resolution: str | None = None
    filesize: int | None = None
    filesize_mb: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    format_note: str | None = None
    protocol: str | None = None
    has_video: bool
    has_audio: bool
    recommended: bool = False


class VideoInfo(BaseModel):
    title: str
    webpage_url: str
    thumbnail: str | None = None
    duration: float | None = None
    extractor: str | None = None
    uploader: str | None = None
    description: str | None = None
    download_strategy: Literal['direct', 'server']
    formats: list[VideoFormat]


class DirectLinkResponse(BaseModel):
    strategy: Literal['redirect', 'server_download']
    direct_url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    reason: str | None = None


class DownloadResponse(BaseModel):
    strategy: Literal['server_download'] = 'server_download'
    file_name: str
    file_url: str
    file_size: int | None = None
    message: str


class HealthResponse(BaseModel):
    status: str
    yt_dlp_version: str | None = None
