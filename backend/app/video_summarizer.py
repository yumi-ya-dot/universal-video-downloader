from __future__ import annotations

import json
from typing import Any, AsyncGenerator

import httpx
from fastapi import HTTPException

from app.config import settings
from app.sse_utils import sse_event
from app.summary_schemas import SummaryResult


class VideoSummarizer:
    def ensure_configured(self) -> None:
        if not settings.deepseek_api_key:
            raise HTTPException(status_code=500, detail='DeepSeek API Key 未配置，请设置 DEEPSEEK_API_KEY 环境变量')

    def build_summary_prompt(self, title: str, transcript: str) -> str:
        return (
            '你是一名擅长中文知识提炼的视频学习助手。请基于以下视频字幕内容，输出 JSON。'
            '要求：1）overview 为 120-220 字中文摘要；2）key_points 为 4-8 条核心要点；'
            '3）chapter_outline 为 3-8 条分章节大纲；4）mind_map 为树状结构，包含 title 与 children。'
            '返回格式必须是 JSON，不要额外解释。\n\n'
            f'视频标题：{title}\n\n字幕内容：\n{transcript[:24000]}'
        )

    def build_chat_prompt(self, title: str, transcript: str, question: str) -> str:
        return (
            '你是一名视频内容问答助手。请严格基于提供的视频字幕回答，中文作答，回答要准确、简洁、分点。'
            '如果字幕里没有明确答案，要坦诚说明“字幕中未明确提到”。\n\n'
            f'视频标题：{title}\n\n字幕内容：\n{transcript[:24000]}\n\n用户问题：{question}'
        )

    async def stream_summary(self, title: str, transcript: str) -> AsyncGenerator[str, None]:
        self.ensure_configured()
        yield sse_event('status', {'message': '正在调用 DeepSeek 生成总结...'})

        content_parts: list[str] = []
        async for chunk in self._chat_completion_stream(self.build_summary_prompt(title, transcript)):
            if not chunk:
                continue
            content_parts.append(chunk)
            yield sse_event('summary_delta', {'delta': chunk})

        content = ''.join(content_parts).strip()
        parsed = self._parse_summary_response(title, content)
        yield sse_event('summary', parsed.model_dump())
        yield sse_event('done', {'message': '总结生成完成'})

    async def stream_chat(self, title: str, transcript: str, question: str) -> AsyncGenerator[str, None]:
        self.ensure_configured()
        yield sse_event('status', {'message': '正在调用 DeepSeek 回答问题...'})

        answer_parts: list[str] = []
        async for chunk in self._chat_completion_stream(self.build_chat_prompt(title, transcript, question)):
            if not chunk:
                continue
            answer_parts.append(chunk)
            yield sse_event('answer_delta', {'delta': chunk})

        answer = ''.join(answer_parts).strip()
        yield sse_event('answer', {'answer': answer})
        yield sse_event('done', {'message': '问答完成'})

    async def _chat_completion_stream(self, prompt: str) -> AsyncGenerator[str, None]:
        url = settings.deepseek_base_url.rstrip('/') + '/chat/completions'
        headers = {
            'Authorization': f'Bearer {settings.deepseek_api_key}',
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream',
        }
        payload = {
            'model': settings.deepseek_model,
            'messages': [
                {'role': 'system', 'content': '你是严谨、结构化、善于中文总结的视频学习助手。'},
                {'role': 'user', 'content': prompt},
            ],
            'temperature': 0.3,
            'stream': True,
        }
        timeout = httpx.Timeout(connect=20.0, read=settings.deepseek_timeout, write=20.0, pool=20.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream('POST', url, headers=headers, json=payload) as response:
                    response.raise_for_status()
                    async for raw_line in response.aiter_lines():
                        if not raw_line:
                            continue
                        line = raw_line.strip()
                        if not line.startswith('data:'):
                            continue
                        data = line[5:].strip()
                        if data == '[DONE]':
                            break
                        try:
                            payload = json.loads(data)
                        except Exception:
                            continue
                        delta = (((payload.get('choices') or [{}])[0].get('delta') or {}).get('content') or '')
                        if delta:
                            yield delta
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:400] if exc.response is not None else str(exc)
            raise HTTPException(status_code=500, detail=f'DeepSeek 流式调用失败：{detail}') from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f'DeepSeek 流式调用失败：{exc}') from exc

    def _parse_summary_response(self, title: str, content: str) -> SummaryResult:
        cleaned = content.strip()
        if cleaned.startswith('```'):
            cleaned = cleaned.strip('`')
            cleaned = cleaned.replace('json', '', 1).strip()
        try:
            data = json.loads(cleaned)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f'总结结果解析失败：{exc}；模型返回内容：{content[:400]}') from exc

        mind_map = data.get('mind_map') or {'title': title, 'children': []}
        mermaid = self._to_mermaid(mind_map)
        return SummaryResult(
            title=title,
            overview=str(data.get('overview') or ''),
            key_points=[str(item).strip() for item in (data.get('key_points') or []) if str(item).strip()],
            chapter_outline=[str(item).strip() for item in (data.get('chapter_outline') or []) if str(item).strip()],
            mind_map_mermaid=mermaid,
            mind_map_tree=mind_map,
        )

    def _to_mermaid(self, root: dict[str, Any]) -> str:
        lines = ['mindmap']

        def walk(node: dict[str, Any], depth: int) -> None:
            title = self._sanitize_label(str(node.get('title') or '未命名主题'))
            indent = '    ' * depth
            lines.append(f'{indent}{title}')
            for child in node.get('children') or []:
                if isinstance(child, dict):
                    walk(child, depth + 1)

        walk(root, 0)
        return '\n'.join(lines)

    def _sanitize_label(self, value: str) -> str:
        return value.replace('\n', ' ').replace(':', '：').strip()


video_summarizer = VideoSummarizer()
