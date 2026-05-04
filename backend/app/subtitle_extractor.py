from __future__ import annotations

import copy
import json
import re
import tempfile
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests
from fastapi import HTTPException
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from app.ytdlp_service import yt_dlp_service


class SubtitleExtractor:
    @lru_cache(maxsize=64)
    def _extract_from_url_cached(self, url: str, preferred_language: str = 'zh-CN') -> dict[str, Any]:
        options = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': self._build_language_candidates(preferred_language),
            'subtitlesformat': 'json3/vtt/best',
            'socket_timeout': 20,
            'extractor_retries': 1,
            'retries': 1,
            'fragment_retries': 1,
        }

        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except DownloadError as exc:
            http_exc = HTTPException(status_code=400, detail=f'字幕提取失败：{exc}')
            if self._should_use_douyin_fallback(url, http_exc):
                return self._extract_douyin_fallback_subtitles(url)
            raise http_exc from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f'字幕提取异常：{exc}') from exc

        track = self._pick_track(info, preferred_language)
        if not track:
            raise HTTPException(status_code=404, detail='当前视频暂无可提取字幕')

        with tempfile.TemporaryDirectory(prefix='subtitle-extract-') as tmpdir:
            temp_dir = Path(tmpdir)
            download_options = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': [track['language']],
                'subtitlesformat': 'json3/vtt/srt/best',
                'outtmpl': str(temp_dir / '%(id)s.%(ext)s'),
                'socket_timeout': 20,
                'extractor_retries': 1,
                'retries': 1,
                'fragment_retries': 1,
            }
            try:
                with YoutubeDL(download_options) as ydl:
                    ydl.extract_info(url, download=True)
            except DownloadError as exc:
                raise HTTPException(status_code=400, detail=f'字幕下载失败：{exc}') from exc

            segments = self._load_segments(temp_dir)

        if not segments:
            raise HTTPException(status_code=404, detail='字幕文件存在，但未解析到有效内容')

        full_text = '\n'.join(segment['text'] for segment in segments if segment['text'])
        return {
            'language': track['language'],
            'language_label': track.get('name') or track['language'],
            'source': track['source'],
            'segments': segments,
            'full_text': full_text,
        }

    def extract_from_url(self, url: str, preferred_language: str = 'zh-CN') -> dict[str, Any]:
        return copy.deepcopy(self._extract_from_url_cached(url, preferred_language))

    def _pick_track(self, info: dict[str, Any], preferred_language: str) -> dict[str, str] | None:
        preferred_candidates: list[dict[str, str]] = []
        fallback_candidates: list[dict[str, str]] = []
        for source_name, mapping in (
            ('subtitle', info.get('subtitles') or {}),
            ('automatic_caption', info.get('automatic_captions') or {}),
        ):
            for language, items in mapping.items():
                if not items:
                    continue
                candidate = {
                    'language': language,
                    'name': items[0].get('name') if isinstance(items[0], dict) else language,
                    'source': 'danmaku' if language == 'danmaku' else source_name,
                }
                if language == 'danmaku':
                    fallback_candidates.append(candidate)
                else:
                    preferred_candidates.append(candidate)

        candidates = preferred_candidates or fallback_candidates
        if not candidates:
            return None

        langs = self._build_language_candidates(preferred_language)
        for wanted in langs:
            exact = next((item for item in candidates if item['language'] == wanted), None)
            if exact:
                return exact
        for wanted in langs:
            fuzzy = next((item for item in candidates if item['language'].lower().startswith(wanted.lower().split('-')[0])), None)
            if fuzzy:
                return fuzzy
        return candidates[0]

    def _build_language_candidates(self, preferred_language: str) -> list[str]:
        normalized = preferred_language.strip() or 'zh-CN'
        base = normalized.split('-')[0]
        candidates = [normalized, base, 'zh-CN', 'zh-Hans', 'zh', 'en']
        result: list[str] = []
        for item in candidates:
            if item and item not in result:
                result.append(item)
        return result

    def _load_segments(self, temp_dir: Path) -> list[dict[str, Any]]:
        json3_files = sorted(temp_dir.glob('*.json3'))
        if json3_files:
            segments = self._parse_json3(json3_files[0])
            if segments:
                return segments

        vtt_files = sorted(temp_dir.glob('*.vtt'))
        if vtt_files:
            segments = self._parse_vtt(vtt_files[0])
            if segments:
                return segments

        srt_files = sorted(temp_dir.glob('*.srt'))
        if srt_files:
            segments = self._parse_srt(srt_files[0])
            if segments:
                return segments

        xml_files = sorted(temp_dir.glob('*.xml'))
        if xml_files:
            segments = self._parse_danmaku_xml(xml_files[0])
            if segments:
                return segments
        return []

    def _extract_douyin_fallback_subtitles(self, url: str) -> dict[str, Any]:
        aweme_id = self._extract_douyin_aweme_id(url)
        try:
            segments = self._fetch_douyin_danmaku_segments(aweme_id)
        except HTTPException as exc:
            if exc.status_code not in (404,):
                raise
            return self._build_metadata_fallback_track(url)

        if not segments:
            return self._build_metadata_fallback_track(url)

        ordered_segments = self._dedupe_segments(segments)
        return {
            'language': 'danmaku',
            'language_label': 'danmaku',
            'source': 'danmaku',
            'segments': ordered_segments,
            'full_text': '\n'.join(segment['text'] for segment in ordered_segments if segment['text']),
        }

    def _build_metadata_fallback_track(self, url: str) -> dict[str, Any]:
        video = yt_dlp_service.parse_video(url)
        parts = [
            f"视频标题：{video.get('title') or '未知标题'}",
            f"发布者：{video.get('uploader') or '未知发布者'}",
            f"平台：{video.get('extractor') or '未知平台'}",
        ]
        description = (video.get('description') or '').strip()
        if description:
            parts.append(f"视频描述：{description[:4000]}")
        text = '\n'.join(part for part in parts if part.strip())
        return {
            'language': 'metadata',
            'language_label': 'metadata',
            'source': 'metadata',
            'segments': [{'start': 0, 'end': 0, 'text': text}],
            'full_text': text,
        }

    def _fetch_douyin_danmaku_segments(self, aweme_id: str) -> list[dict[str, Any]]:
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
            'Referer': 'https://www.iesdouyin.com/',
        }
        api_url = f'https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={aweme_id}'
        try:
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f'抖音弹幕回退失败：获取视频信息失败：{exc}') from exc

        items = payload.get('item_list') or []
        if not items:
            raise HTTPException(status_code=404, detail='抖音弹幕回退失败：未找到视频信息')

        danmaku_url = (((items[0].get('video_control') or {}).get('danmaku_url')) or '').strip()
        if not danmaku_url:
            raise HTTPException(status_code=404, detail='当前抖音视频暂无弹幕可供回退总结')

        try:
            response = requests.get(danmaku_url, headers=headers, timeout=30)
            response.raise_for_status()
            xml_text = response.text
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f'抖音弹幕回退失败：下载弹幕失败：{exc}') from exc

        return self._parse_danmaku_xml_content(xml_text)

    def _parse_json3(self, path: Path) -> list[dict[str, Any]]:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f'字幕 JSON3 解析失败：{exc}') from exc

        segments: list[dict[str, Any]] = []
        for event in data.get('events', []):
            start = (event.get('tStartMs') or 0) / 1000
            duration = (event.get('dDurationMs') or 0) / 1000
            texts = []
            for seg in event.get('segs', []) or []:
                text = (seg.get('utf8') or '').replace('\n', ' ').strip()
                if text:
                    texts.append(text)
            merged = ''.join(texts).strip()
            if merged:
                segments.append({'start': round(start, 2), 'end': round(start + duration, 2), 'text': merged})
        return self._dedupe_segments(segments)

    def _parse_vtt(self, path: Path) -> list[dict[str, Any]]:
        lines = path.read_text(encoding='utf-8', errors='ignore').splitlines()
        segments: list[dict[str, Any]] = []
        idx = 0
        while idx < len(lines):
            line = lines[idx].strip()
            if '-->' in line:
                start_raw, end_raw = [part.strip() for part in line.split('-->')]
                idx += 1
                texts = []
                while idx < len(lines) and lines[idx].strip():
                    current = lines[idx].strip()
                    if not current.startswith('NOTE') and '<' not in current:
                        texts.append(current)
                    idx += 1
                merged = ' '.join(texts).strip()
                if merged:
                    segments.append({
                        'start': self._vtt_time_to_seconds(start_raw),
                        'end': self._vtt_time_to_seconds(end_raw.split(' ')[0]),
                        'text': merged,
                    })
            idx += 1
        return self._dedupe_segments(segments)

    def _parse_srt(self, path: Path) -> list[dict[str, Any]]:
        lines = path.read_text(encoding='utf-8', errors='ignore').splitlines()
        segments: list[dict[str, Any]] = []
        idx = 0
        while idx < len(lines):
            line = lines[idx].strip()
            if '-->' in line:
                start_raw, end_raw = [part.strip() for part in line.split('-->')]
                idx += 1
                texts = []
                while idx < len(lines) and lines[idx].strip():
                    texts.append(lines[idx].strip())
                    idx += 1
                merged = ' '.join(texts).strip()
                if merged:
                    segments.append({
                        'start': self._vtt_time_to_seconds(start_raw),
                        'end': self._vtt_time_to_seconds(end_raw.split(' ')[0]),
                        'text': merged,
                    })
            idx += 1
        return self._dedupe_segments(segments)

    def _parse_danmaku_xml(self, path: Path) -> list[dict[str, Any]]:
        try:
            return self._parse_danmaku_xml_content(path.read_text(encoding='utf-8', errors='ignore'))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f'弹幕 XML 解析失败：{exc}') from exc

    def _parse_danmaku_xml_content(self, xml_text: str) -> list[dict[str, Any]]:
        try:
            root = ET.fromstring(xml_text)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f'弹幕 XML 解析失败：{exc}') from exc

        segments: list[dict[str, Any]] = []
        for item in root.findall('.//d'):
            text = (item.text or '').strip()
            p = item.attrib.get('p', '')
            start_raw = p.split(',')[0] if p else '0'
            try:
                start = round(float(start_raw), 2)
            except Exception:
                start = 0
            if text:
                segments.append({'start': start, 'end': start, 'text': text})
        return self._dedupe_segments(segments)

    def _should_use_douyin_fallback(self, url: str, exc: HTTPException) -> bool:
        detail = str(exc.detail)
        return 'douyin.com' in url and 'Fresh cookies' in detail

    def _extract_douyin_aweme_id(self, url: str) -> str:
        match = re.search(r'/video/(\d+)', url)
        if match:
            return match.group(1)

        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://www.iesdouyin.com/',
        }
        try:
            response = requests.get(url, headers=headers, allow_redirects=True, timeout=30)
            response.raise_for_status()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f'解析失败：无法解析抖音分享链接：{exc}') from exc

        match = re.search(r'/video/(\d+)', response.url)
        if not match:
            raise HTTPException(status_code=400, detail='解析失败：未能从抖音链接中提取视频 ID')
        return match.group(1)

    def _vtt_time_to_seconds(self, value: str) -> float:
        value = value.replace(',', '.')
        parts = value.split(':')
        try:
            if len(parts) == 3:
                hours, minutes, seconds = parts
                return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 2)
            if len(parts) == 2:
                minutes, seconds = parts
                return round(int(minutes) * 60 + float(seconds), 2)
        except Exception:
            return 0
        return 0

    def _dedupe_segments(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ordered_segments = sorted(
            segments,
            key=lambda item: (
                round(float(item.get('start', 0) or 0), 2),
                round(float(item.get('end', 0) or 0), 2),
                str(item.get('text') or ''),
            ),
        )

        result: list[dict[str, Any]] = []
        previous = None
        for segment in ordered_segments:
            text = segment['text'].strip()
            if not text or text == previous:
                continue
            previous = text
            result.append(segment)
        return result


subtitle_extractor = SubtitleExtractor()
