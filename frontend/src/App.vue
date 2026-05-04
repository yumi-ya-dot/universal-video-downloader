<script setup lang="ts">
import { computed, ref } from 'vue'
import VideoSummary from './components/VideoSummary.vue'
import { apiUrl } from './lib/apiBase'

type F = {
  format_id: string
  ext: string | null
  resolution: string | null
  filesize: number | null
  format_note: string | null
  protocol: string | null
  has_video: boolean
  has_audio: boolean
  recommended: boolean
}

type V = {
  title: string
  webpage_url: string
  thumbnail: string | null
  duration: number | null
  extractor: string | null
  uploader: string | null
  description: string | null
  download_strategy: 'direct' | 'server'
  formats: F[]
}

const url = ref('')
const loading = ref(false)
const downloading = ref(false)
const error = ref('')
const success = ref('')
const video = ref<V | null>(null)
const selected = ref('')

const navItems = [
  { label: '功能特性', href: '#features' },
  { label: '支持平台', href: '#platforms' },
]
const platformChips = ['YouTube', 'Bilibili', 'Twitter/X', '抖音', 'TikTok']
const featureCards = [
  {
    title: '一键解析下载',
    description: '支持直接粘贴视频链接或整段分享文案，自动识别并解析可用下载格式。',
  },
  {
    title: '服务端稳定兜底',
    description: '针对复杂平台自动切换到服务端下载链路，减少直链失效和跨域限制。',
  },
  {
    title: 'AI 字幕与总结',
    description: '提取字幕、生成摘要、思维导图与问答结果，帮助快速理解视频内容。',
  },
]
const platformCards = [
  {
    name: 'YouTube / TikTok',
    description: '适合国际主流视频平台内容保存，支持多种清晰度与常见下载格式。',
  },
  {
    name: 'Bilibili / Twitter(X)',
    description: '兼容常见分享链接与原始视频链接，适用于知识、资讯与短内容下载。',
  },
  {
    name: '抖音 / 小红书等扩展平台',
    description: '对复杂平台提供额外解析与兜底策略，提升整体可用性与成功率。',
  },
]

const current = computed(() => video.value?.formats.find((item) => item.format_id === selected.value) ?? null)
const coverUrl = computed(() => video.value?.thumbnail ? apiUrl(`/api/thumbnail?url=${encodeURIComponent(video.value.thumbnail)}`) : null)

const dur = (s: number | null) => !s ? '未知时长' : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
const size = (b: number | null) => !b ? '大小未知' : `${(b / 1024 / 1024).toFixed(2)} MB`

function triggerBrowserDownload(url: string, filename = 'video.mp4') {
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.rel = 'noopener'
  a.target = '_self'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

async function parse() {
  error.value = ''
  success.value = ''
  video.value = null
  selected.value = ''

  if (!url.value.trim()) {
    error.value = '请先输入视频链接或分享文案'
    return
  }

  loading.value = true
  try {
    const r = await fetch(apiUrl('/api/parse'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url.value.trim() }),
    })
    const d = await r.json()
    if (!r.ok) throw new Error(d.detail || '解析失败')
    video.value = d
    selected.value = d.formats?.find((item: F) => item.has_video)?.format_id ?? d.formats?.[0]?.format_id ?? ''
    success.value = '解析成功，请选择格式后下载。'
  } catch (e) {
    error.value = e instanceof Error ? e.message : '解析失败'
  } finally {
    loading.value = false
  }
}

async function serverDownload(payload: { url: string; format_id: string }, msg: string) {
  const r = await fetch(apiUrl('/api/download'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const d = await r.json()
  if (!r.ok) throw new Error(d.detail || '服务端下载失败')
  success.value = msg || d.message
  triggerBrowserDownload(d.file_url, d.file_name || 'video.mp4')
}

async function download() {
  if (!video.value || !selected.value) {
    error.value = '请先解析并选择格式'
    return
  }

  downloading.value = true
  error.value = ''
  success.value = ''

  const payload = { url: video.value.webpage_url, format_id: selected.value }
  try {
    await serverDownload(payload, '文件已准备完成，正在开始下载。')
  } catch (e) {
    error.value = e instanceof Error ? e.message : '下载失败'
  } finally {
    downloading.value = false
  }
}
</script>

<template>
  <div class="page-shell">
    <main class="page-container">
      <header class="site-header">
        <div class="site-brand">
          <div class="brand-mark">◉</div>
          <div class="brand-copy">
            <strong>SaveAny</strong>
            <span>万能视频下载</span>
          </div>
        </div>

        <nav class="site-nav">
          <a v-for="item in navItems" :key="item.label" :href="item.href">{{ item.label }}</a>
        </nav>
      </header>

      <section class="hero-section">
        <div class="hero-badge">支持 1800+ 平台，永久免费使用</div>

        <h1 class="hero-title">
          万能视频下载器，
          <span>一键保存</span>
        </h1>

        <p class="hero-subtitle">
          粘贴视频链接，智能解析，支持多种清晰度下载。YouTube、Bilibili、抖音、TikTok 等常见平台都可直接使用。
        </p>

        <div class="search-card">
          <div class="search-bar">
            <div class="search-icon">↗</div>
            <textarea
              v-model="url"
              rows="1"
              placeholder="请输入视频链接，或直接粘贴整段分享文案"
              class="search-input"
            />
            <button :disabled="loading" @click="parse" class="search-button">
              {{ loading ? '解析中...' : '解析视频' }}
            </button>
          </div>

          <div class="quick-links">
            <span class="quick-label">试试：</span>
            <span v-for="p in platformChips" :key="p" class="quick-chip">{{ p }}</span>
          </div>
        </div>

        <div v-if="error" class="message error-message">{{ error }}</div>
        <div v-if="success" class="message success-message">{{ success }}</div>
      </section>

      <section v-if="video" class="result-layout">
        <section class="panel preview-panel">
          <div class="panel-head">
            <div>
              <h2>解析结果</h2>
              <p>{{ video.extractor || '未知平台' }} · {{ dur(video.duration) }}</p>
            </div>
          </div>

          <div class="preview-cover">
            <img v-if="coverUrl" :src="coverUrl" :alt="video.title" />
            <div v-else class="preview-empty">暂无封面</div>
          </div>

          <div class="video-meta">
            <h3 class="video-title">{{ video.title }}</h3>
            <p class="video-subtitle">发布者：{{ video.uploader || '未知' }}</p>
            <p class="video-description">
              {{ video.description?.slice(0, 150) || '视频已解析完成，可以直接选择格式并下载。' }}
            </p>
            <a :href="video.webpage_url" target="_blank" rel="noreferrer" class="text-link">打开原视频页面</a>
          </div>
        </section>

        <section class="panel download-panel">
          <div class="panel-head">
            <div>
              <h2>选择格式下载</h2>
              <p>共 {{ video.formats.length }} 个可用格式</p>
            </div>
          </div>

          <div class="format-list">
            <label
              v-for="f in video.formats"
              :key="f.format_id"
              class="format-item"
              :class="selected === f.format_id ? 'selected' : ''"
            >
              <input v-model="selected" :value="f.format_id" type="radio" />
              <div class="format-body">
                <div class="format-top">
                  <strong>{{ f.resolution || f.ext || '未知格式' }}</strong>
                  <div class="format-badges">
                    <span v-if="f.recommended" class="badge badge-blue">推荐</span>
                    <span v-if="f.has_video && !f.has_audio" class="badge">自动补音频</span>
                  </div>
                </div>
                <p>{{ f.ext || '未知扩展名' }} · {{ f.format_note || '标准流' }} · {{ size(f.filesize) }}</p>
              </div>
            </label>
          </div>

          <div v-if="current" class="selection-note">
            当前选择：{{ current.resolution || current.ext || current.format_id }}
          </div>

          <button :disabled="downloading || !selected" @click="download" class="download-button">
            {{ downloading ? '准备下载中...' : '下载选中格式' }}
          </button>
        </section>
      </section>

      <VideoSummary
        v-if="video"
        :video-url="video.webpage_url"
        :video-title="video.title"
      />

      <section id="features" class="info-section">
        <div class="info-section-head">
          <span class="info-kicker">功能特性</span>
          <h2>围绕下载与视频理解的一体化工具体验</h2>
          <p>不仅可以下载视频，还能对内容进行字幕提取、摘要生成与问答分析。</p>
        </div>
        <div class="info-grid">
          <article v-for="item in featureCards" :key="item.title" class="info-card">
            <h3>{{ item.title }}</h3>
            <p>{{ item.description }}</p>
          </article>
        </div>
      </section>

      <section id="platforms" class="info-section">
        <div class="info-section-head">
          <span class="info-kicker">支持平台</span>
          <h2>覆盖常见内容平台，兼容多种分享方式</h2>
          <p>支持标准链接、短链接与整段分享文案输入，减少手动清洗成本。</p>
        </div>
        <div class="info-grid platform-grid">
          <article v-for="item in platformCards" :key="item.name" class="info-card platform-card">
            <h3>{{ item.name }}</h3>
            <p>{{ item.description }}</p>
          </article>
        </div>
      </section>
    </main>
  </div>
</template>
