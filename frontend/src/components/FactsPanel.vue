<template>
  <div>
    <div class="bar">
      <span class="dim">AI 提取的为候选结果，请逐条确认；点击证据可回溯视频/照片/法规（PRD §11）</span>
      <el-button size="small" @click="addDialog = true">补充事实</el-button>
    </div>
    <div v-for="f in facts" :key="f.id" :id="`fact-${f.id}`" class="fact" :class="f.status">
      <div class="f-head">
        <el-tag :type="f.status === 'confirmed' ? 'success' : 'warning'" size="small">
          {{ f.status === 'confirmed' ? '已确认' : '候选' }}
        </el-tag>
        <el-input v-model="f.content" size="small" class="content" @change="patch(f, { content: f.content })" />
        <el-button v-if="f.status !== 'confirmed'" size="small" type="success" @click="patch(f, { status: 'confirmed' })">确认</el-button>
        <el-button v-else size="small" @click="patch(f, { status: 'candidate' })">撤回</el-button>
        <el-button size="small" text type="danger" @click="del(f)">删除</el-button>
      </div>
      <div class="evidences">
        <span v-for="e in f.evidences" :key="e.id" class="ev">
          <el-tag size="small" type="info" closable @close="delEv(e)" @click="openEv(e)" class="ev-tag">
            {{ evLabel(e) }}
          </el-tag>
        </span>
        <el-dropdown @command="(t: string) => attach(f, t)">
          <el-button size="small" text type="primary">+ 关联证据</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="video_segment">视频时间段</el-dropdown-item>
              <el-dropdown-item command="keyframe">关键画面</el-dropdown-item>
              <el-dropdown-item command="knowledge_chunk">法规条款</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button size="small" text @click="showHistory(f)">历史</el-button>
      </div>
    </div>
    <el-empty v-if="!facts.length" description="暂无事实：上传材料后自动提取，或手工补充" />

    <el-dialog v-model="addDialog" title="补充事实" width="480px">
      <el-input v-model="newFact" type="textarea" :rows="3" />
      <template #footer><el-button type="primary" @click="add">添加并确认</el-button></template>
    </el-dialog>

    <el-drawer v-model="histDrawer" title="修改历史" size="420px">
      <div v-for="h in history" :key="h.id" class="hist">
        <div class="dim">{{ h.at.replace('T', ' ').slice(0, 19) }} · {{ h.action }}</div>
        <div v-if="h.detail.field">{{ h.detail.field }}：{{ h.detail.old }} → {{ h.detail.new }}</div>
        <div v-if="h.detail.note" class="dim">{{ h.detail.note }}</div>
      </div>
    </el-drawer>

    <!-- 证据选择对话框 -->
    <el-dialog v-model="evDialog" :title="`关联证据：${evTypeName}`" width="640px">
      <template v-if="evType === 'video_segment'">
        <div v-for="m in materials" :key="m.id">
          <div><b>{{ m.filename }}</b></div>
          <div v-for="s in winSegs[m.id] || []" :key="s.id" class="pick" @click="pickVideoSeg(f_for_ev, m.id, s)">
            [{{ fmtMs(s.start_ms) }}–{{ fmtMs(s.end_ms) }}] {{ s.text.slice(0, 40) }}…
          </div>
        </div>
      </template>
      <template v-else-if="evType === 'keyframe'">
        <div class="kgrid">
          <div v-for="k in keyframes" :key="k.id" class="kcell" @click="pickKeyframe(f_for_ev, k)">
            <el-image :src="k.url" fit="cover" style="width: 100%; height: 90px" />
            <div class="dim">{{ k.ts_ms != null ? fmtMs(k.ts_ms) : '上传' }}</div>
          </div>
        </div>
      </template>
      <template v-else-if="evType === 'knowledge_chunk'">
        <el-input v-model="kw" placeholder="检索法规…" @keydown.enter="searchKw">
          <template #append><el-button @click="searchKw">检索</el-button></template>
        </el-input>
        <div v-for="c in chunks" :key="c.chunk_id" class="pick" @click="pickChunk(f_for_ev, c)">
          《{{ c.doc_title }}》{{ c.clause_label }}：{{ c.text.slice(0, 60) }}…
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, fmtMs } from '../api'

const props = defineProps<{ inspectionId: number }>()
const emit = defineEmits(['open-evidence'])

const facts = ref<any[]>([])
const addDialog = ref(false); const newFact = ref('')
const histDrawer = ref(false); const history = ref<any[]>([])
const evDialog = ref(false); const evType = ref(''); const f_for_ev = ref<any>(null)
const materials = ref<any[]>([]); const winSegs = ref<Record<number, any[]>>({})
const keyframes = ref<any[]>([]); const kw = ref(''); const chunks = ref<any[]>([])

const evTypeName = ref('')

async function load() { facts.value = await api.get(`/api/inspections/${props.inspectionId}/facts`) }
async function patch(f: any, body: any) { await api.patch(`/api/facts/${f.id}`, body); ElMessage.success('已保存'); load() }
async function del(f: any) {
  await ElMessageBox.confirm('删除该事实？', '确认', { type: 'warning' })
  await api.del(`/api/facts/${f.id}`); load()
}
async function add() {
  if (!newFact.value.trim()) return
  await api.post(`/api/inspections/${props.inspectionId}/facts`, { content: newFact.value })
  newFact.value = ''; addDialog.value = false; load()
}
async function delEv(e: any) { await api.del(`/api/evidences/${e.id}`); load() }

function evLabel(e: any): string {
  if (e.evidence_type === 'video_segment') return `▶ 视频 ${fmtMs(e.ref.start_ms)}–${fmtMs(e.ref.end_ms)}`
  if (e.evidence_type === 'keyframe') return '🖼 关键画面'
  if (e.evidence_type === 'knowledge_chunk') return '📖 法规条款'
  return e.evidence_type
}
function openEv(e: any) { emit('open-evidence', e) }

async function attach(f: any, type: string) {
  f_for_ev.value = f; evType.value = type
  evTypeName.value = { video_segment: '视频时间段', keyframe: '关键画面', knowledge_chunk: '法规条款' }[type] || type
  if (type === 'video_segment') {
    materials.value = await api.get(`/api/inspections/${props.inspectionId}/materials`)
    for (const m of materials.value) {
      const t = await api.get(`/api/materials/${m.id}/transcripts`)
      winSegs.value[m.id] = t.window?.segments || []
    }
  } else if (type === 'keyframe') {
    keyframes.value = await api.get(`/api/inspections/${props.inspectionId}/keyframes`)
  } else { chunks.value = []; kw.value = '' }
  evDialog.value = true
}
async function pickVideoSeg(f: any, mid: number, s: any) {
  await api.post(`/api/facts/${f.id}/evidences`, { evidence_type: 'video_segment', material_id: mid,
    ref: { start_ms: s.start_ms, end_ms: s.end_ms, segment_id: s.id } })
  evDialog.value = false; load()
}
async function pickKeyframe(f: any, k: any) {
  await api.post(`/api/facts/${f.id}/evidences`, { evidence_type: 'keyframe', material_id: k.material_id, ref: { keyframe_id: k.id } })
  evDialog.value = false; load()
}
async function searchKw() {
  const r = await api.post('/api/knowledge/query', { question: kw.value })
  chunks.value = r.sources || []
}
async function pickChunk(f: any, c: any) {
  await api.post(`/api/facts/${f.id}/evidences`, { evidence_type: 'knowledge_chunk', ref: { chunk_id: c.chunk_id },
    note: `《${c.doc_title}》${c.clause_label}` })
  evDialog.value = false; load()
}
async function showHistory(f: any) {
  history.value = await api.get(`/api/facts/${f.id}/history`)
  histDrawer.value = true
}
defineExpose({ load })
onMounted(load)
</script>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.dim { color: #909399; font-size: 12px; }
.fact { background: #fff; border: 1px solid #e4e7ed; border-radius: 6px; padding: 10px; margin-bottom: 10px; }
.fact.confirmed { border-left: 3px solid #67c23a; }
.f-head { display: flex; align-items: center; gap: 8px; }
.content { flex: 1; }
.evidences { margin-top: 6px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.ev-tag { cursor: pointer; }
.pick { padding: 6px; cursor: pointer; border-radius: 4px; }
.pick:hover { background: #ecf5ff; }
.kgrid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.kcell { cursor: pointer; border: 1px solid #e4e7ed; border-radius: 4px; text-align: center; }
.hist { padding: 8px 0; border-bottom: 1px dashed #e4e7ed; font-size: 13px; }
</style>
