<template>
  <div>
    <el-input v-model="q" type="textarea" :rows="2" placeholder="输入法规问题，如：消防控制室值班有什么要求？"
              @keydown.ctrl.enter="search">
      <template #append></template>
    </el-input>
    <el-button type="primary" size="small" style="margin: 8px 0" :loading="loading" @click="search">检索</el-button>
    <div v-if="result" class="result">
      <el-alert :type="result.found ? 'success' : 'warning'" :closable="false" :title="result.answer" style="white-space: pre-wrap" />
      <div v-for="(s, i) in result.sources" :key="i" class="source">
        <div class="s-head">
          <el-tag size="small">{{ s.doc_type }}</el-tag>
          <b>《{{ s.doc_title }}》{{ s.clause_label }}</b>
          <el-button text size="small" @click="toggle(i)">原文</el-button>
        </div>
        <div v-if="openIdx === i" class="s-text">{{ s.text }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api'

const props = defineProps<{ initialQuestion?: string }>()
const q = ref(props.initialQuestion || '')
const loading = ref(false); const result = ref<any>(null); const openIdx = ref(-1)

async function search() {
  if (!q.value.trim()) return
  loading.value = true
  try { result.value = await api.post('/api/knowledge/query', { question: q.value }) } finally { loading.value = false }
}
function toggle(i: number) { openIdx.value = openIdx.value === i ? -1 : i }
defineExpose({ ask: (question: string) => { q.value = question; search() } })
</script>

<style scoped>
.result { margin-top: 8px; }
.source { margin-top: 10px; border: 1px solid #e4e7ed; border-radius: 4px; padding: 8px; background: #fff; }
.s-head { display: flex; gap: 8px; align-items: center; }
.s-text { margin-top: 6px; font-size: 13px; white-space: pre-wrap; color: #606266; }
</style>
