<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import mermaid from 'mermaid'
import svgPanZoom from 'svg-pan-zoom'
import type { ChatAnswer, SubtitleTrack, SummaryDelta, SummaryPayload } from '../lib/summaryApi'
import { postJson, readSSE } from '../lib/summaryApi'

const props = defineProps<{
  videoUrl: string
  videoTitle: string
}>()

const tabs = ['摘要', '字幕', '思维导图', 'AI问答'] as const
const activeTab = ref<(typeof tabs)[number]>('摘要')
const loadingSummary = ref(false)
const loadingSubtitles = ref(false)
const chatting = ref(false)
const error = ref('')
const summaryStatus = ref('')
const chatStatus = ref('')
const summary = ref<SummaryPayload | null>(null)
const summaryStreamingText = ref('')
const subtitles = ref<SubtitleTrack | null>(null)
const question = ref('')
const answer = ref('')
const answerStreamingText = ref('')
const mindMapSvg = ref('')
const mindMapContainer = ref<HTMLElement | null>(null)

const summaryTypingBuffer = ref('')
const answerTypingBuffer = ref('')

const hasData = computed(() => !!summary.value || !!subtitles.value)
const sortedSubtitleSegments = computed(() => {
  if (!subtitles.value?.segments?.length) return []
  return [...subtitles.value.segments].sort((a, b) => {
    if (a.start !== b.start) return a.start - b.start
    if (a.end !== b.end) return a.end - b.end
    return a.text.localeCompare(b.text, 'zh-CN')
  })
})
const subtitleSourceLabel = computed(() => {
  const source = subtitles.value?.source
  if (source === 'subtitle') return '平台字幕'
  if (source === 'automatic_caption') return '自动字幕'
  if (source === 'danmaku') return '弹幕回退'
  if (source === 'metadata') return '视频描述兜底'
  return '未知来源'
})
let panZoomInstance: ReturnType<typeof svgPanZoom> | null = null
let mermaidInitialized = false
let summaryAbortController: AbortController | null = null
let chatAbortController: AbortController | null = null
let summaryTypingTimer: ReturnType<typeof setTimeout> | null = null
let answerTypingTimer: ReturnType<typeof setTimeout> | null = null
const typingIntervalMs = 16

async function ensureMermaid() {
  if (mermaidInitialized) return
  mermaid.initialize({
    startOnLoad: false,
    theme: 'base',
    securityLevel: 'loose',
    themeVariables: {
      primaryColor: '#dbeafe',
      primaryTextColor: '#1e3a8a',
      primaryBorderColor: '#93c5fd',
      lineColor: '#93c5fd',
      tertiaryColor: '#eff6ff',
      fontFamily: 'Inter, PingFang SC, Microsoft YaHei, system-ui, sans-serif',
    },
    mindmap: {
      padding: 18,
    },
  })
  mermaidInitialized = true
}

function destroyPanZoom() {
  if (panZoomInstance) {
    panZoomInstance.destroy()
    panZoomInstance = null
  }
}

function stopTypingTimers() {
  if (summaryTypingTimer) {
    clearTimeout(summaryTypingTimer)
    summaryTypingTimer = null
  }
  if (answerTypingTimer) {
    clearTimeout(answerTypingTimer)
    answerTypingTimer = null
  }
}

function flushTypingBuffers() {
  if (summaryTypingBuffer.value) {
    summaryStreamingText.value += summaryTypingBuffer.value
    summaryTypingBuffer.value = ''
  }
  if (answerTypingBuffer.value) {
    answerStreamingText.value += answerTypingBuffer.value
    answerTypingBuffer.value = ''
  }
}

function tickSummaryTyping() {
  if (!summaryTypingBuffer.value) {
    summaryTypingTimer = null
    return
  }
  summaryStreamingText.value += summaryTypingBuffer.value.slice(0, 1)
  summaryTypingBuffer.value = summaryTypingBuffer.value.slice(1)
  summaryTypingTimer = setTimeout(tickSummaryTyping, typingIntervalMs)
}

function tickAnswerTyping() {
  if (!answerTypingBuffer.value) {
    answerTypingTimer = null
    return
  }
  answerStreamingText.value += answerTypingBuffer.value.slice(0, 1)
  answerTypingBuffer.value = answerTypingBuffer.value.slice(1)
  answerTypingTimer = setTimeout(tickAnswerTyping, typingIntervalMs)
}

function enqueueSummaryTyping(text: string) {
  if (!text) return
  summaryTypingBuffer.value += text
  if (!summaryTypingTimer) {
    tickSummaryTyping()
  }
}

function enqueueAnswerTyping(text: string) {
  if (!text) return
  answerTypingBuffer.value += text
  if (!answerTypingTimer) {
    tickAnswerTyping()
  }
}

async function renderMindMap() {
  destroyPanZoom()
  mindMapSvg.value = ''
  const source = summary.value?.mind_map_mermaid?.trim()
  if (!source) return

  await ensureMermaid()
  const renderId = `mindmap-${Date.now()}`
  const { svg } = await mermaid.render(renderId, source)
  mindMapSvg.value = svg

  await nextTick()
  const svgElement = mindMapContainer.value?.querySelector('svg')
  if (!svgElement) return

  svgElement.setAttribute('width', '100%')
  svgElement.setAttribute('height', '100%')
  svgElement.removeAttribute('style')

  panZoomInstance = svgPanZoom(svgElement, {
    zoomEnabled: true,
    panEnabled: true,
    controlIconsEnabled: true,
    mouseWheelZoomEnabled: true,
    dblClickZoomEnabled: true,
    fit: true,
    center: true,
    minZoom: 0.5,
    maxZoom: 8,
    zoomScaleSensitivity: 0.25,
  })
}

async function loadSubtitles() {
  loadingSubtitles.value = true
  error.value = ''
  try {
    subtitles.value = await postJson<SubtitleTrack>('/api/subtitles', { url: props.videoUrl, preferred_language: 'zh-CN' })
  } catch (e) {
    error.value = e instanceof Error ? e.message : '字幕加载失败'
  } finally {
    loadingSubtitles.value = false
  }
}

async function loadSummary() {
  summaryAbortController?.abort()
  summaryAbortController = new AbortController()
  loadingSummary.value = true
  error.value = ''
  summaryStatus.value = '正在准备摘要流式生成...'
  summary.value = null
  summaryStreamingText.value = ''
  summaryTypingBuffer.value = ''
  mindMapSvg.value = ''
  try {
    await readSSE('/api/summarize', { url: props.videoUrl, preferred_language: 'zh-CN' }, {
      onEvent(event, data) {
        if (event === 'status') {
          summaryStatus.value = String((data as { message?: string }).message || '')
        }
        if (event === 'summary_delta') {
          summaryStatus.value = 'AI 正在流式生成摘要...'
          enqueueSummaryTyping((data as SummaryDelta).delta || '')
        }
        if (event === 'summary') {
          flushTypingBuffers()
          summary.value = data as SummaryPayload
          summaryStatus.value = '摘要生成完成'
        }
        if (event === 'done') {
          summaryStatus.value = String((data as { message?: string }).message || '摘要生成完成')
        }
        if (event === 'error') {
          throw new Error(String((data as { message?: string }).message || '总结生成失败'))
        }
      },
    }, { signal: summaryAbortController.signal })
    await renderMindMap()
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      flushTypingBuffers()
      summaryStatus.value = '已停止摘要生成'
      return
    }
    error.value = e instanceof Error ? e.message : '总结生成失败'
    summaryStatus.value = ''
  } finally {
    loadingSummary.value = false
    summaryAbortController = null
  }
}

function stopSummary() {
  summaryAbortController?.abort()
}

async function askQuestion() {
  if (!question.value.trim()) {
    error.value = '请输入你想提问的问题'
    return
  }
  chatAbortController?.abort()
  chatAbortController = new AbortController()
  chatting.value = true
  error.value = ''
  chatStatus.value = '正在准备问答流式生成...'
  answer.value = ''
  answerStreamingText.value = ''
  answerTypingBuffer.value = ''
  try {
    await readSSE('/api/chat', { url: props.videoUrl, preferred_language: 'zh-CN', question: question.value.trim() }, {
      onEvent(event, data) {
        if (event === 'status') {
          chatStatus.value = String((data as { message?: string }).message || '')
        }
        if (event === 'answer_delta') {
          chatStatus.value = 'AI 正在流式回答...'
          enqueueAnswerTyping((data as SummaryDelta).delta || '')
        }
        if (event === 'answer') {
          flushTypingBuffers()
          answer.value = (data as ChatAnswer).answer
          chatStatus.value = '问答生成完成'
        }
        if (event === 'done') {
          chatStatus.value = String((data as { message?: string }).message || '问答完成')
        }
        if (event === 'error') {
          throw new Error(String((data as { message?: string }).message || '问答失败'))
        }
      },
    }, { signal: chatAbortController.signal })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      flushTypingBuffers()
      chatStatus.value = '已停止问答生成'
      return
    }
    error.value = e instanceof Error ? e.message : '问答失败'
    chatStatus.value = ''
  } finally {
    chatting.value = false
    chatAbortController = null
  }
}

function stopChat() {
  chatAbortController?.abort()
}

function zoomInMindMap() {
  panZoomInstance?.zoomIn()
}

function zoomOutMindMap() {
  panZoomInstance?.zoomOut()
}

function resetMindMapView() {
  if (!panZoomInstance) return
  panZoomInstance.resetZoom()
  panZoomInstance.fit()
  panZoomInstance.center()
}

watch(() => props.videoUrl, () => {
  summaryAbortController?.abort()
  chatAbortController?.abort()
  stopTypingTimers()
  summary.value = null
  subtitles.value = null
  answer.value = ''
  answerStreamingText.value = ''
  answerTypingBuffer.value = ''
  summaryStreamingText.value = ''
  summaryTypingBuffer.value = ''
  summaryStatus.value = ''
  chatStatus.value = ''
  question.value = ''
  error.value = ''
  mindMapSvg.value = ''
  destroyPanZoom()
})

watch(() => activeTab.value, async (tab) => {
  if (tab === '思维导图' && summary.value?.mind_map_mermaid) {
    await renderMindMap()
  }
})

onBeforeUnmount(() => {
  summaryAbortController?.abort()
  chatAbortController?.abort()
  stopTypingTimers()
  destroyPanZoom()
})
</script>

<template>
  <section class="panel summary-panel">
    <div class="panel-head summary-head">
      <div>
        <h2>AI 视频总结</h2>
        <p>基于字幕内容生成摘要、时间轴字幕、思维导图与问答结果</p>
      </div>
      <div class="summary-actions">
        <button class="secondary-button" :disabled="loadingSubtitles" @click="loadSubtitles">
          {{ loadingSubtitles ? '字幕加载中...' : '提取字幕' }}
        </button>
        <button class="summary-button" :disabled="loadingSummary" @click="loadSummary">
          {{ loadingSummary ? '总结生成中...' : '生成 AI 总结' }}
        </button>
        <button v-if="loadingSummary" class="stop-button" @click="stopSummary">
          停止生成
        </button>
      </div>
    </div>

    <div class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab"
        class="tab-button"
        :class="activeTab === tab ? 'active' : ''"
        @click="activeTab = tab"
      >
        {{ tab }}
      </button>
    </div>

    <div v-if="error" class="message error-message summary-message">{{ error }}</div>
    <div v-else-if="!hasData && !loadingSummary && !loadingSubtitles" class="summary-empty">
      先点击“提取字幕”或“生成 AI 总结”，不会影响原有下载流程。
    </div>

    <div v-else class="summary-content">
      <template v-if="activeTab === '摘要'">
        <div v-if="summaryStatus" class="summary-status">{{ summaryStatus }}</div>
        <div v-if="summary || summaryStreamingText" class="summary-block">
          <template v-if="summary">
            <h3>{{ summary.title }}</h3>
            <p class="summary-overview">{{ summary.overview }}</p>
            <div class="summary-list-card">
              <h4>核心要点</h4>
              <ul>
                <li v-for="item in summary.key_points" :key="item">{{ item }}</li>
              </ul>
            </div>
            <div class="summary-list-card">
              <h4>章节大纲</h4>
              <ol>
                <li v-for="item in summary.chapter_outline" :key="item">{{ item }}</li>
              </ol>
            </div>
          </template>
          <template v-else>
            <div class="summary-list-card streaming-card">
              <h4>AI 正在生成摘要</h4>
              <p class="streaming-content">{{ summaryStreamingText }}<span v-if="loadingSummary" class="typing-caret"></span></p>
            </div>
          </template>
        </div>
      </template>

      <template v-else-if="activeTab === '字幕'">
        <div v-if="subtitles" class="subtitle-block">
          <div class="subtitle-meta">
            语言：{{ subtitles.language_label || subtitles.language }} · 来源：{{ subtitleSourceLabel }}
          </div>
          <div class="subtitle-list">
            <div v-for="segment in sortedSubtitleSegments" :key="`${segment.start}-${segment.end}-${segment.text}`" class="subtitle-item">
              <span class="subtitle-time">{{ segment.start.toFixed(2) }}s</span>
              <p>{{ segment.text }}</p>
            </div>
          </div>
        </div>
      </template>

      <template v-else-if="activeTab === '思维导图'">
        <div v-if="summary?.mind_map_mermaid" class="mindmap-card mermaid-mindmap-card">
          <div class="mindmap-toolbar">
            <div>
              <h4>思维导图</h4>
              <p>基于 SVG/XML 渲染，支持拖拽、滚轮缩放与视图重置</p>
            </div>
            <div class="mindmap-actions">
              <button class="mindmap-action-button" type="button" @click="zoomOutMindMap">缩小</button>
              <button class="mindmap-action-button" type="button" @click="zoomInMindMap">放大</button>
              <button class="mindmap-action-button" type="button" @click="resetMindMapView">重置</button>
            </div>
          </div>
          <div ref="mindMapContainer" class="mindmap-svg-shell">
            <div v-if="mindMapSvg" class="mindmap-svg" v-html="mindMapSvg"></div>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="chat-card">
          <textarea
            v-model="question"
            rows="3"
            class="chat-input"
            placeholder="例如：这个视频讲了哪些核心知识点？"
          />
          <button class="summary-button" :disabled="chatting" @click="askQuestion">
            {{ chatting ? 'AI 思考中...' : '开始提问' }}
          </button>
          <button v-if="chatting" class="stop-button" @click="stopChat">
            停止回答
          </button>
          <div v-if="chatStatus" class="summary-status chat-status">{{ chatStatus }}</div>
          <div v-if="answer || answerStreamingText" class="answer-card">
            <h4>AI 回答</h4>
            <p>{{ answer || answerStreamingText }}<span v-if="chatting" class="typing-caret"></span></p>
          </div>
        </div>
      </template>
    </div>
  </section>
</template>
