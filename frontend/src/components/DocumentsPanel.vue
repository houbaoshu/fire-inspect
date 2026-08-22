<template>
  <div>
    <div class="bar">
      <span class="dim">三类文书共享本次检查的事实与基础字段；AI 生成初稿，人工修改后确认（PRD §10）</span>
      <el-button type="primary" size="small" @click="createDialog = true">新建文书</el-button>
    </div>

    <el-table :data="docs" @row-click="open" highlight-current-row>
      <el-table-column prop="name" label="文书名称" />
      <el-table-column prop="doc_type_name" label="类型" width="140" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 'confirmed' ? 'success' : 'info'" size="small">
            {{ row.status === 'confirmed' ? '已确认' : '初稿' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220">
        <template #default="{ row }">
          <el-button text size="small" @click.stop="open(row)">编辑</el-button>
          <el-button text size="small" @click.stop="exportDoc(row)">导出 Word</el-button>
          <el-button text size="small" type="danger" @click.stop="del(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="createDialog" title="新建文书" width="560px">
      <el-form label-width="90px">
        <el-form-item label="文书类型">
          <el-radio-group v-model="createForm.doc_type">
            <el-radio-button value="record">监督检查记录</el-radio-button>
            <el-radio-button value="photo">照片文书</el-radio-button>
            <el-radio-button value="interrogation">询问笔录</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="模板">
          <el-select v-model="createForm.template_id">
            <el-option v-for="t in templatesForType" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称"><el-input v-model="createForm.name" placeholder="留空 = 单位+文书类型" /></el-form-item>
        <el-form-item v-if="createForm.doc_type === 'photo'" label="选图">
          <KeyframeGrid :inspection-id="inspectionId" selectable v-model="createForm.frame_ids" />
        </el-form-item>
      </el-form>
      <template #footer><el-button type="primary" :loading="creating" @click="create">生成初稿</el-button></template>
    </el-dialog>

    <el-drawer v-model="editDrawer" :title="current?.name" size="640px">
      <template v-if="current">
        <div class="ops">
          <el-tag :type="current.status === 'confirmed' ? 'success' : 'info'">
            {{ current.status === 'confirmed' ? '已确认' : '初稿' }}
          </el-tag>
          <el-button size="small" @click="regenerate">重新生成初稿</el-button>
          <el-button size="small" @click="renderDoc">刷新渲染</el-button>
          <el-button v-if="current.status !== 'confirmed'" size="small" type="success" @click="confirm">确认定稿</el-button>
          <el-button v-else size="small" type="warning" @click="unconfirm">退回初稿</el-button>
          <el-button size="small" text @click="showHistory">历史</el-button>
        </div>
        <el-alert v-if="current.status === 'confirmed'" type="success" :closable="false"
                  title="已确认文书：相关事实/共享字段变化将自动退回初稿（PRD §16）" style="margin-bottom: 10px" />
        <div v-if="current.doc_type === 'photo'" class="frames-pick">
          <b>选图：</b>
          <KeyframeGrid :inspection-id="inspectionId" selectable v-model="frameIds" />
          <el-button size="small" style="margin-top: 6px" @click="saveFrames">应用选图</el-button>
        </div>
        <div v-for="group in groupedFields" :key="group.key" class="fgroup">
          <div v-if="group.key" class="fgroup-title">—— 照片 {{ group.key }} ——</div>
          <div v-for="f in group.fields" :key="f.id" class="field">
            <div class="flabel">
              {{ f.field_label }}
              <el-tag v-if="f.origin === 'user'" size="small" type="warning">人工</el-tag>
            </div>
            <el-input :model-value="f.value" type="textarea" :autosize="{ minRows: 1, maxRows: 12 }"
                      @update:model-value="(v: string) => f.value = v"
                      @change="saveField(f)" />
          </div>
        </div>
        <el-drawer v-model="histDrawer" title="操作历史" size="380px" append-to-body>
          <div v-for="h in history" :key="h.id" class="hist">
            <div class="dim">{{ h.at.replace('T', ' ').slice(0, 19) }} · {{ h.action }}</div>
            <div v-if="h.detail.field">{{ h.detail.field }}：{{ String(h.detail.old).slice(0, 40) }} → {{ String(h.detail.new).slice(0, 40) }}</div>
            <div v-if="h.detail.note" class="dim">{{ h.detail.note }}</div>
          </div>
        </el-drawer>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import KeyframeGrid from './KeyframeGrid.vue'

const props = defineProps<{ inspectionId: number }>()
const docs = ref<any[]>([])
const templates = ref<any[]>([])
const createDialog = ref(false); const creating = ref(false)
const createForm = ref<any>({ doc_type: 'record', template_id: null, name: '', frame_ids: [] })
const editDrawer = ref(false); const current = ref<any>(null); const frameIds = ref<number[]>([])
const histDrawer = ref(false); const history = ref<any[]>([])

const templatesForType = computed(() => templates.value.filter((t: any) => t.doc_type === createForm.value.doc_type))
const groupedFields = computed(() => {
  if (!current.value) return []
  const shared = current.value.fields.filter((f: any) => !f.frame_key)
  const groups: any[] = [{ key: '', fields: shared }]
  const keys = [...new Set(current.value.fields.filter((f: any) => f.frame_key).map((f: any) => f.frame_key))].sort()
  for (const k of keys) groups.push({ key: k, fields: current.value.fields.filter((f: any) => f.frame_key === k) })
  return groups
})

async function load() {
  docs.value = await api.get(`/api/inspections/${props.inspectionId}/documents`)
  templates.value = await api.get('/api/templates')
}
async function create() {
  if (!createForm.value.template_id) return ElMessage.warning('请选择模板')
  creating.value = true
  try {
    const d = await api.post(`/api/inspections/${props.inspectionId}/documents`, createForm.value)
    createDialog.value = false
    await load()
    open(d)
  } finally { creating.value = false }
}
function open(row: any) {
  current.value = row
  frameIds.value = [...new Set(row.fields.filter((f: any) => f.frame_key).map((f: any) => Number(f.frame_key)))]
  editDrawer.value = true
}
async function saveField(f: any) {
  const r = await api.patch(`/api/documents/${current.value.id}/fields`,
    { field_label: f.field_label, frame_key: f.frame_key || '', value: f.value })
  f.origin = r.origin
  ElMessage.success('已保存（标记为人工修改，重新生成时不覆盖）')
}
async function regenerate() {
  await api.post(`/api/documents/${current.value.id}/regenerate`)
  current.value = await api.get(`/api/documents/${current.value.id}`)
  ElMessage.success('已重新生成（人工修改字段保留）')
}
async function renderDoc() {
  await api.post(`/api/documents/${current.value.id}/render`)
  ElMessage.success('已重新渲染')
}
async function confirm() { await api.post(`/api/documents/${current.value.id}/confirm`); current.value = await api.get(`/api/documents/${current.value.id}`); load() }
async function unconfirm() { await api.post(`/api/documents/${current.value.id}/unconfirm`); current.value = await api.get(`/api/documents/${current.value.id}`); load() }
async function saveFrames() {
  await api.post(`/api/documents/${current.value.id}/frames`, { frame_ids: frameIds.value })
  current.value = await api.get(`/api/documents/${current.value.id}`)
}
async function exportDoc(row: any) {
  const r = await fetch(`/api/documents/${row.id}/export`, { credentials: 'same-origin' })
  if (!r.ok) return ElMessage.error('导出失败：请先生成')
  const blob = await r.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob); a.download = `${row.name}.docx`; a.click()
}
async function del(row: any) {
  await ElMessageBox.confirm(`删除文书「${row.name}」？`, '确认', { type: 'warning' })
  await api.del(`/api/documents/${row.id}`); load()
}
async function showHistory() {
  history.value = await api.get(`/api/documents/${current.value.id}/history`)
  histDrawer.value = true
}
defineExpose({ load })
onMounted(load)
</script>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.dim { color: #909399; font-size: 12px; }
.ops { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
.frames-pick { margin-bottom: 12px; border: 1px dashed #e4e7ed; padding: 8px; border-radius: 4px; }
.fgroup { margin-bottom: 8px; }
.fgroup-title { color: #909399; font-size: 12px; margin: 8px 0 4px; }
.field { margin-bottom: 8px; }
.flabel { font-size: 13px; margin-bottom: 2px; display: flex; gap: 6px; align-items: center; }
.hist { padding: 8px 0; border-bottom: 1px dashed #e4e7ed; font-size: 13px; }
</style>
