from pathlib import Path
from time import time
from typing import Any, AsyncGenerator
from urllib.parse import quote

import copy
import requests
import yt_dlp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.config import settings
from app.schemas import (
    DirectLinkResponse,
    DownloadRequest,
    DownloadResponse,
    HealthResponse,
    ParseRequest,
    VideoInfo,
)
from app.sse_utils import sse_event
from app.subtitle_extractor import subtitle_extractor
from app.summary_schemas import ChatRequest, SubtitleExtractRequest, SubtitleTrack, SummaryRequest
from app.video_summarizer import video_summarizer
from app.ytdlp_service import yt_dlp_service


app = FastAPI(title=settings.app_name, version=settings.app_version)

VIDEO_CACHE_TTL_SECONDS = 15 * 60
video_runtime_cache: dict[str, dict[str, Any]] = {}


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _cache_key(url: str, preferred_language: str) -> str:
    return f"{url}::{preferred_language}"


def _purge_expired_cache() -> None:
    now = time()
    expired_keys = [key for key, value in video_runtime_cache.items() if now - value.get("timestamp", 0) > VIDEO_CACHE_TTL_SECONDS]
    for key in expired_keys:
        video_runtime_cache.pop(key, None)


def _save_video_context(url: str, preferred_language: str, video: dict[str, Any], subtitles: dict[str, Any]) -> None:
    _purge_expired_cache()
    video_runtime_cache[_cache_key(url, preferred_language)] = {
        "timestamp": time(),
        "video": copy.deepcopy(video),
        "subtitles": copy.deepcopy(subtitles),
    }


def _load_video_context(url: str, preferred_language: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    _purge_expired_cache()
    payload = video_runtime_cache.get(_cache_key(url, preferred_language))
    if not payload:
        return None
    return copy.deepcopy(payload["video"]), copy.deepcopy(payload["subtitles"])


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", yt_dlp_version=yt_dlp.version.__version__)


@app.post("/api/parse", response_model=VideoInfo)
async def parse_video(payload: ParseRequest) -> VideoInfo:
    data = await run_in_threadpool(yt_dlp_service.parse_video, str(payload.url))
    return VideoInfo(**data)


@app.post("/api/subtitles", response_model=SubtitleTrack)
async def extract_subtitles(payload: SubtitleExtractRequest) -> SubtitleTrack:
    cached = _load_video_context(str(payload.url), payload.preferred_language)
    if cached:
        _, subtitles = cached
        return SubtitleTrack(**subtitles)

    video = await run_in_threadpool(yt_dlp_service.parse_video, str(payload.url))
    subtitles = await run_in_threadpool(subtitle_extractor.extract_from_url, str(payload.url), payload.preferred_language)
    _save_video_context(str(payload.url), payload.preferred_language, video, subtitles)
    return SubtitleTrack(**subtitles)


@app.post("/api/summarize")
async def summarize_video(payload: SummaryRequest) -> StreamingResponse:
    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            cached = _load_video_context(str(payload.url), payload.preferred_language)
            if cached:
                yield sse_event("status", {"message": "已命中缓存，正在生成摘要..."})
                video, subtitles = cached
            else:
                yield sse_event("status", {"message": "正在解析视频信息..."})
                video = await run_in_threadpool(yt_dlp_service.parse_video, str(payload.url))

                yield sse_event("status", {"message": "正在提取字幕内容..."})
                subtitles = await run_in_threadpool(subtitle_extractor.extract_from_url, str(payload.url), payload.preferred_language)
                _save_video_context(str(payload.url), payload.preferred_language, video, subtitles)

            async for chunk in video_summarizer.stream_summary(video.get("title") or "未命名视频", subtitles.get("full_text") or ""):
                yield chunk
        except HTTPException as exc:
            yield sse_event("error", {"message": exc.detail})
        except Exception as exc:
            yield sse_event("error", {"message": f"总结生成失败：{exc}"})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@app.post("/api/chat")
async def chat_about_video(payload: ChatRequest) -> StreamingResponse:
    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            cached = _load_video_context(str(payload.url), payload.preferred_language)
            if cached:
                yield sse_event("status", {"message": "已命中缓存，正在生成回答..."})
                video, subtitles = cached
            else:
                yield sse_event("status", {"message": "正在解析视频信息..."})
                video = await run_in_threadpool(yt_dlp_service.parse_video, str(payload.url))

                yield sse_event("status", {"message": "正在提取字幕内容..."})
                subtitles = await run_in_threadpool(subtitle_extractor.extract_from_url, str(payload.url), payload.preferred_language)
                _save_video_context(str(payload.url), payload.preferred_language, video, subtitles)

            async for chunk in video_summarizer.stream_chat(video.get("title") or "未命名视频", subtitles.get("full_text") or "", payload.question):
                yield chunk
        except HTTPException as exc:
            yield sse_event("error", {"message": exc.detail})
        except Exception as exc:
            yield sse_event("error", {"message": f"问答失败：{exc}"})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@app.post("/api/direct-link", response_model=DirectLinkResponse)
def direct_link(payload: DownloadRequest) -> DirectLinkResponse:
    data = yt_dlp_service.get_direct_link(str(payload.url), payload.format_id)
    return DirectLinkResponse(**data)


@app.post("/api/download", response_model=DownloadResponse)
def download_video(payload: DownloadRequest, request: Request) -> DownloadResponse:
    result = yt_dlp_service.download_video(str(payload.url), payload.format_id)
    encoded_name = quote(result["file_name"])
    file_url = str(request.base_url).rstrip("/") + f"/api/files/{encoded_name}"
    return DownloadResponse(
        file_name=result["file_name"],
        file_url=file_url,
        file_size=result.get("file_size"),
        message="文件已下载到服务器，点击链接即可保存到本地。",
    )


@app.get("/api/files/{file_name}")
def serve_file(file_name: str) -> FileResponse:
    target = settings.download_dir / file_name
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path=Path(target), filename=target.name, media_type="application/octet-stream")


@app.get("/api/thumbnail")
def proxy_thumbnail(url: str = Query(...)) -> Response:
    try:
        upstream = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        upstream.raise_for_status()
        content_type = upstream.headers.get("Content-Type", "image/jpeg")
        return Response(content=upstream.content, media_type=content_type)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"封面加载失败：{exc}") from exc


@app.get("/api/download-proxy")
def proxy_download(url: str = Query(...), filename: str = Query("video.mp4")) -> StreamingResponse:
    try:
        upstream = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"}, stream=True)
        upstream.raise_for_status()
        content_type = upstream.headers.get("Content-Type", "application/octet-stream")
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return StreamingResponse(upstream.iter_content(chunk_size=8192), media_type=content_type, headers=headers)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"下载代理失败：{exc}") from exc
