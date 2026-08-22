<template>
  <div v-if="insp">
    <div class="head">
      <h3>{{ insp.unit_name || '（未命名检查）' }}</h3>
      <span class="dim">{{ insp.address }} · {{ insp.inspected_at?.replace('T', ' ').slice(0, 16) }} · {{ insp.checker_names }}</span>
      <el-button size="small" @click="editDialog = true">编辑信息</el-button>
    </div>
    <el-tabs v-model="tab">
      <el-tab-pane label="材料与处理" name="materials">
        <MaterialsPanel ref="matPanel" :inspection-id="iid" @refresh="refreshAll" />
      </el-tab-pane>
      <el-tab-pane label="检查事实" name="facts">
        <FactsPanel ref="factsPanel" :inspection-id="iid" @open-evidence="openEvidence" />
      </el-tab-pane>
      <el-tab-pane label="文书" name="documents">
        <DocumentsPanel ref="docsPanel" :inspection-id="iid" />
      </el-tab-pane>
      <el-tab-pane label="法规查询" name="knowledge">
        <KnowledgeQuery ref="kq" />
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="editDialog" title="编辑检查信息" width="560px">
      <el-form label-width="110px">
        <el-form-item label="被检查单位"><el-input v-model="insp.unit_name" /></el-form-item>
        <el-form-item label="地址"><el-input v-model="insp.address" /></el-form-item>
        <el-form-item label="检查人员"><el-input v-model="insp.checker_names" /></el-form-item>
        <el-form-item label="联系人及电话"><el-input v-model="insp.contact" /></el-form-item>
        <el-form-item label="负责人"><el-input v-model="insp.legal_person" /></el-form-item>
        <el-form-item label="检查类型">
          <el-select v-model="insp.check_type" clearable>
            <el-option v-for="t in checkTypes" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="insp.notes" type="textarea" /></el-form-item>
      </el-form>
      <template #footer><el-button type="primary" @click="saveInsp">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="evDialog" title="证据" width="560px">
      <video v-if="ev?.evidence_type === 'video_segment' && ev.material_id" ref="evVideo"
             :src="`/api/materials/${ev.material_id}/file`" controls style="width: 100%" />
      <el-image v-else-if="ev?.evidence_type === 'keyframe'" :src="`/api/keyframes/${ev.ref.keyframe_id}/image`"
                fit="contain" style="width: 100%" />
      <div v-else-if="ev?.evidence_type === 'knowledge_chunk'" class="chunk">
        <b>{{ ev.note }}</b>
        <p>{{ chunkText }}</p>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { useAppStore } from '../store'
import MaterialsPanel from '../components/MaterialsPanel.vue'
import FactsPanel from '../components/FactsPanel.vue'
import DocumentsPanel from '../components/DocumentsPanel.vue'
import KnowledgeQuery from '../components/KnowledgeQuery.vue'

const checkTypes = ["大型群众性活动举办前的检查", "建设工程施工现场检查", "对举报投诉的核查", "复查",
  "申请恢复施工、使用、生产、经营的检查", "申请解除临时查封的检查", "其它检查"]
const route = useRoute()
const iid = Number(route.params.id)
const insp = ref<any>(null)
const tab = ref('materials')
const editDialog = ref(false)
const evDialog = ref(false); const ev = ref<any>(null); const chunkText = ref('')
const matPanel = ref(); const factsPanel = ref(); const docsPanel = ref(); const kq = ref(); const evVideo = ref()
const store = useAppStore()

async function load() { insp.value = await api.get(`/api/inspections/${iid}`) }
async function saveInsp() { await api.patch(`/api/inspections/${iid}`, insp.value); editDialog.value = false }
function refreshAll() { factsPanel.value?.load(); docsPanel.value?.load() }

// 证据回溯（PRD §11）
async function openEvidence(e: any) {
  ev.value = e; evDialog.value = true
  if (e.evidence_type === 'video_segment') {
    setTimeout(() => { if (evVideo.value) { evVideo.value.currentTime = (e.ref.start_ms || 0) / 1000; evVideo.value.play() } }, 300)
  } else if (e.evidence_type === 'knowledge_chunk') {
    chunkText.value = '加载中…'
    // chunk 内容从知识库接口取（按文档内检索简化：直接询问后端不可行时用 note）
    chunkText.value = e.note || '（在法规知识库页面查看原文）'
  }
}

// 助手导航动作（PRD §13）
function handleAction(a: any) {
  if (a.type === 'open_video') {
    tab.value = 'materials'
    setTimeout(() => matPanel.value?.seekTo(a.material_id, a.ts_ms), 300)
  } else if (a.type === 'open_keyframe') {
    openEvidence({ evidence_type: 'keyframe', ref: { keyframe_id: a.keyframe_id } })
  } else if (a.type === 'open_fact') {
    tab.value = 'facts'
    setTimeout(() => document.getElementById(`fact-${a.fact_id}`)?.scrollIntoView({ behavior: 'smooth' }), 300)
  } else if (a.type === 'open_document') {
    tab.value = 'documents'
  } else if (a.type === 'open_knowledge') {
    tab.value = 'knowledge'
  }
}
onMounted(() => { load(); store.actionHandler = handleAction })
onUnmounted(() => { store.actionHandler = null })
</script>

<style scoped>
.head { display: flex; align-items: baseline; gap: 16px; }
.head h3 { margin: 0; }
.dim { color: #909399; font-size: 13px; }
.chunk { white-space: pre-wrap; line-height: 1.8; }
</style>
