<template>
  <el-aside width="360px" class="panel">
    <div class="head">
      <b>AI 助手</b>
      <span class="tip">基于当前检查上下文</span>
    </div>
    <div ref="listEl" class="msgs">
      <div v-for="(m, i) in store.chat" :key="i" :class="['msg', m.role]">
        <div class="bubble">
          <div class="text">{{ m.text }}</div>
          <div v-if="m.suggestion" class="suggestion">💡 {{ m.suggestion }}</div>
          <div v-if="m.actions?.length" class="actions">
            <el-button v-for="(a, j) in m.actions" :key="j" size="small" @click="run(a)">
              {{ actionLabel(a) }}
            </el-button>
          </div>
        </div>
      </div>
      <div v-if="store.chatLoading" class="msg ai"><div class="bubble">思考中…</div></div>
      <div v-if="!store.chat.length" class="empty">
        可以问我：<br />· 这条事实的依据是什么？<br />· 对应哪条法规？<br />· 视频里哪里能证明这一点？
      </div>
    </div>
    <div class="input">
      <el-input v-model="text" type="textarea" :rows="2" placeholder="输入问题…"
                @keydown.enter.exact.prevent="send" />
      <el-button type="primary" :loading="store.chatLoading" @click="send">发送</el-button>
    </div>
  </el-aside>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore, type AssistantAction } from '../store'

const store = useAppStore()
const route = useRoute()
const text = ref('')
const listEl = ref<HTMLElement>()

watch(() => store.chat.length, async () => {
  await nextTick()
  listEl.value?.scrollTo({ top: listEl.value.scrollHeight })
})

function inspectionId(): number | null {
  const m = route.path.match(/^\/inspections\/(\d+)/)
  return m ? Number(m[1]) : null
}

function send() {
  const msg = text.value.trim()
  if (!msg) return
  text.value = ''
  store.ask(msg, inspectionId(), route.path)
}

function actionLabel(a: AssistantAction): string {
  return { open_video: `▶ 视频 ${Math.floor((a.ts_ms || 0) / 1000)}s`, open_keyframe: '🖼 查看照片',
           open_fact: '📋 定位事实', open_document: '📄 打开文书', open_knowledge: '📖 法规原文' }[a.type] || a.type
}

function run(a: AssistantAction) {
  if (store.actionHandler) store.actionHandler(a)
}
</script>

<style scoped>
.panel { border-left: 1px solid #e4e7ed; display: flex; flex-direction: column; background: #fff; height: calc(100vh - 60px); }
.head { padding: 12px; border-bottom: 1px solid #e4e7ed; display: flex; justify-content: space-between; align-items: center; }
.tip { font-size: 12px; color: #909399; }
.msgs { flex: 1; overflow-y: auto; padding: 12px; }
.msg { margin-bottom: 10px; display: flex; }
.msg.user { justify-content: flex-end; }
.bubble { max-width: 90%; padding: 8px 12px; border-radius: 8px; background: #f0f2f5; white-space: pre-wrap; font-size: 13px; }
.msg.user .bubble { background: #d9ecff; }
.suggestion { margin-top: 6px; font-size: 12px; color: #e6a23c; }
.actions { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; }
.empty { color: #909399; font-size: 13px; text-align: center; margin-top: 40px; line-height: 2; }
.input { padding: 12px; border-top: 1px solid #e4e7ed; display: flex; gap: 8px; align-items: flex-end; }
</style>
