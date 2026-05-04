from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas import ParseRequest


class SubtitleSegment(BaseModel):
    start: float = 0
    end: float = 0
    text: str


class SubtitleTrack(BaseModel):
    language: str
    language_label: str | None = None
    source: Literal['subtitle', 'automatic_caption', 'danmaku', 'metadata'] = 'subtitle'
    segments: list[SubtitleSegment] = Field(default_factory=list)
    full_text: str = ''


class SubtitleExtractRequest(ParseRequest):
    preferred_language: str = 'zh-CN'


class SummaryRequest(ParseRequest):
    preferred_language: str = 'zh-CN'


class ChatRequest(ParseRequest):
    preferred_language: str = 'zh-CN'
    question: str = Field(..., min_length=1)


class SummaryMindMapNode(BaseModel):
    title: str
    children: list['SummaryMindMapNode'] = Field(default_factory=list)


class SummaryResult(BaseModel):
    title: str
    overview: str
    key_points: list[str] = Field(default_factory=list)
    chapter_outline: list[str] = Field(default_factory=list)
    mind_map_mermaid: str
    mind_map_tree: SummaryMindMapNode | None = None


SummaryMindMapNode.model_rebuild()
