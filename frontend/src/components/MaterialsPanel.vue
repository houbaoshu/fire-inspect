<template>
  <div>
    <el-upload :show-file-list="false" :http-request="upload" accept="video/*,audio/*,image/*">
      <el-button type="primary" :loading="uploading">上传材料（视频/录音/照片）</el-button>
    </el-upload>

    <div v-for="m in materials" :key="m.id" class="material">
      <div class="m-head">
        <b>{{ m.filename }}</b>
        <el-tag size="small">{{ { video: '视频', audio: '录音', photo: '照片', other: '其他' }[m.type] }}</el-tag>
        <span v-if="m.duration_ms" class="dim">{{ fmtMs(m.duration_ms) }}</span>
        <el-button text size="small" @click="toggle(m)">{{ expanded === m.id ? '收起' : '展开' }}</el-button>
        <el-button text size="small" type="primary" @click="extractFacts(m)">提取事实</el-button>
        <el-button text size="small" type="danger" @click="delMaterial(m)">删除</el-button>
      </div>

      <!-- 任务状态机：逐步骤状态/失败原因/重试（PRD §4） -->
      <div class="jobs">
        <span v-for="j in jobs[m.id] || []" :key="j.id" class="job">
          <el-tag :type="{ succeeded: 'success', failed: 'danger', running: 'warning', pending: 'info' }[j.status]" size="small">
            {{ jobName(j.kind) }}:{{ { succeeded: '成功', failed: '失败', running: '处理中', pending: '待处理' }[j.status] }}
          </el-tag>
          <el-tooltip v-if="j.error" :content="j.error"><span class="err">原因</span></el-tooltip>
          <el-button v-if="j.status === 'failed'" text size="small" type="warning" @click="retry(j)">重试</el-button>
        </span>
      </div>

      <div v-if="expanded === m.id" class="detail">
        <video v-if="m.type === 'video'" ref="videoRef" :src="`/api/materials/${m.id}/file`"
               controls class="video" :id="`video-${m.id}`" />
        <audio v-else-if="m.type === 'audio'" :src="`/api/materials/${m.id}/file`" controls />

        <el-tabs v-if="m.type === 'video' || m.type === 'audio'">
          <el-tab-pane label="整段转写">
            <p class="transcript">{{ transcripts[m.id]?.full?.segments?.[0]?.text || '（处理中或失败）' }}</p>
          </el-tab-pane>
          <el-tab-pane label="分段时间锚（问/答标注）">
            <div v-for="s in transcripts[m.id]?.window?.segments || []" :key="s.id" class="seg">
              <el-link @click="seek(m.id, s.start_ms)">[{{ fmtMs(s.start_ms) }}–{{ fmtMs(s.end_ms) }}]</el-link>
              <el-input v-model="s.text" size="small" class="seg-text" @change="patchSeg(s, { text: s.text })" />
              <el-radio-group :model-value="s.qa_role" size="small"
                              @change="(v: any) => patchSeg(s, { qa_role: v, qa_confirmed: true })">
                <el-radio-button value="">未标</el-radio-button>
                <el-radio-button value="问">问</el-radio-button>
                <el-radio-button value="答">答</el-radio-button>
              </el-radio-group>
            </div>
          </el-tab-pane>
          <el-tab-pane v-if="m.type === 'video'" label="关键画面">
            <KeyframeGrid :material-id="m.id" :inspection-id="inspectionId" @refresh="$emit('refresh')" />
          </el-tab-pane>
        </el-tabs>
        <KeyframeGrid v-else-if="m.type === 'photo'" :material-id="m.id" :inspection-id="inspectionId" />
      </div>
    </div>
    <el-empty v-if="!materials.length" description="尚未上传材料" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, fmtMs } from '../api'
import KeyframeGrid from './KeyframeGrid.vue'

const props = defineProps<{ inspectionId: number }>()
const emit = defineEmits(['refresh'])

const materials = ref<any[]>([])
const jobs = ref<Record<number, any[]>>({})
const transcripts = ref<Record<number, any>>({})
const expanded = ref<number | null>(null)
const uploading = ref(false)
let timer: any = null

const jobName = (k: string) => ({ transcribe_full: '整段转写', transcribe_windows: '分段转写',
  keyframes: '关键画面', fact_extract: '事实提取', knowledge_ingest: '知识解析' }[k] || k)

async function load() {
  materials.value = await api.get(`/api/inspections/${props.inspectionId}/materials`)
  for (const m of materials.value) {
    jobs.value[m.id] = await api.get(`/api/materials/${m.id}/jobs`)
    if (expanded.value === m.id) transcripts.value[m.id] = await api.get(`/api/materials/${m.id}/transcripts`)
  }
  const active = Object.values(jobs.value).flat().some((j: any) => ['pending', 'running'].includes(j.status))
  if (active && !timer) timer = setTimeout(() => { timer = null; load() }, 3000)
}

async function toggle(m: any) {
  expanded.value = expanded.value === m.id ? null : m.id
  if (expanded.value === m.id) transcripts.value[m.id] = await api.get(`/api/materials/${m.id}/transcripts`)
}

async function upload(opt: any) {
  uploading.value = true
  try {
    const fd = new FormData(); fd.append('file', opt.file)
    await api.post(`/api/inspections/${props.inspectionId}/materials`, undefined, fd)
    ElMessage.success('上传成功，开始处理')
    load()
  } finally { uploading.value = false }
}

async function delMaterial(m: any) {
  await ElMessageBox.confirm(`删除材料「${m.filename}」？相关转写/关键帧将一并删除。`, '确认', { type: 'warning' })
  await api.del(`/api/materials/${m.id}`); load(); emit('refresh')
}

async function retry(j: any) { await api.post(`/api/jobs/${j.id}/retry`); ElMessage.success('已重新排队'); load() }
async function extractFacts(m: any) { await api.post(`/api/materials/${m.id}/extract-facts`); ElMessage.success('事实提取已排队'); load() }

function seek(mid: number, ms: number) {
  const v = document.getElementById(`video-${mid}`) as HTMLVideoElement
  if (v) { v.currentTime = ms / 1000; v.play() }
}

async function patchSeg(s: any, body: any) { await api.patch(`/api/segments/${s.id}`, body) }

// 供助手动作跳转视频时间（PRD §13）
function seekTo(materialId: number, ms: number) {
  const m = materials.value.find((x: any) => x.id === materialId)
  if (!m) return
  expanded.value = materialId
  setTimeout(() => seek(materialId, ms), 300)
}
defineExpose({ seekTo })
onMounted(load)
onUnmounted(() => timer && clearTimeout(timer))
</script>

<style scoped>
.material { background: #fff; border: 1px solid #e4e7ed; border-radius: 6px; padding: 12px; margin-top: 12px; }
.m-head { display: flex; align-items: center; gap: 10px; }
.dim { color: #909399; font-size: 12px; }
.jobs { margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap; }
.job { display: flex; align-items: center; gap: 4px; }
.err { color: #f56c6c; font-size: 12px; cursor: help; }
.detail { margin-top: 12px; }
.video { width: 480px; max-width: 100%; display: block; margin-bottom: 8px; }
.transcript { background: #f9fafc; padding: 12px; border-radius: 4px; line-height: 1.8; }
.seg { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.seg-text { flex: 1; }
</style>
