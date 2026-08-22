<template>
  <div>
    <div class="bar">
      <h3>文书模板</h3>
      <el-button type="primary" @click="dialog = true">上传 Word 模板</el-button>
    </div>
    <p class="dim">直接上传现有正式文书即可，无需预先添加占位符；系统自动识别填写位置，中低置信字段请人工确认后使用（PRD §8）。</p>

    <el-table :data="rows" @row-click="open" highlight-current-row>
      <el-table-column prop="name" label="模板名称" />
      <el-table-column prop="doc_type_name" label="文书类型" width="140" />
      <el-table-column prop="version" label="版本" width="70">
        <template #default="{ row }">v{{ row.version }}</template>
      </el-table-column>
      <el-table-column label="字段" width="120">
        <template #default="{ row }">
          {{ row.fields.length }}（待确认 {{ row.fields.filter((f: any) => !f.confirmed).length }}）
        </template>
      </el-table-column>
      <el-table-column prop="builtin" label="来源" width="80">
        <template #default="{ row }">{{ row.builtin ? '预置' : '上传' }}</template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" title="上传模板" width="480px">
      <el-form label-width="90px">
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="文书类型">
          <el-select v-model="form.doc_type">
            <el-option label="监督检查记录表" value="record" />
            <el-option label="照片文书" value="photo" />
            <el-option label="询问笔录" value="interrogation" />
          </el-select>
        </el-form-item>
        <el-form-item label="文件">
          <el-upload :auto-upload="false" :limit="1" :on-change="(f: any) => form.file = f.raw" accept=".docx">
            <el-button>选择 .docx</el-button>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer><el-button type="primary" :loading="uploading" @click="upload">上传并识别</el-button></template>
    </el-dialog>

    <el-drawer v-model="drawer" :title="`字段映射：${current?.name}`" size="560px">
      <template v-if="current">
        <el-alert v-for="(w, i) in current.warnings" :key="i" type="warning" :closable="false" :title="w"
                  style="margin-bottom: 8px" />
        <div class="ops">
          <el-button size="small" type="success" @click="confirmAll">全部确认</el-button>
          <el-button size="small" @click="reRecognize">重新识别（规则升级后）</el-button>
        </div>
        <el-table :data="current.fields" size="small">
          <el-table-column label="字段（中文业务名）" width="200">
            <template #default="{ row }">
              <el-input v-model="row.field_label" size="small" @change="patch(row, { field_label: row.field_label })" />
            </template>
          </el-table-column>
          <el-table-column label="填充方式" width="150">
            <template #default="{ row }">{{ opName(row.fill_op) }}</template>
          </el-table-column>
          <el-table-column label="置信" width="70">
            <template #default="{ row }">
              <el-tag size="small" :type="{ high: 'success', medium: 'warning', low: 'danger' }[row.confidence]">
                {{ { high: '高', medium: '中', low: '低' }[row.confidence] }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="属性" width="120">
            <template #default="{ row }">
              <el-tag v-if="row.shared" size="small" type="info">同源</el-tag>
              <el-tag v-if="row.per_frame" size="small" type="warning">逐帧</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="确认" width="70">
            <template #default="{ row }">
              <el-switch :model-value="row.confirmed" @change="(v: boolean) => patch(row, { confirmed: v })" />
            </template>
          </el-table-column>
          <el-table-column label="" width="60">
            <template #default="{ row }">
              <el-button text size="small" type="danger" @click="delField(row)">删</el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'

const rows = ref<any[]>([])
const dialog = ref(false); const uploading = ref(false)
const form = ref<any>({ name: '', doc_type: 'record', file: null })
const drawer = ref(false); const current = ref<any>(null)

const opName = (op: string) => ({ cell_write: '空单元格写入', checkbox: '复选框打勾',
  append_after_label: '标签后追加（下划线）', replace_spaces: '空格段替换（下划线）',
  skeleton: '骨架填空（日期/编号）', replace_placeholder_image: '占位替换为照片',
  replace_placeholder: '占位段落替换', generate_body: '生成正文段落' }[op] || op)

async function load() { rows.value = await api.get('/api/templates') }
async function upload() {
  if (!form.value.file || !form.value.name) return ElMessage.warning('请填写名称并选择文件')
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('name', form.value.name); fd.append('doc_type', form.value.doc_type); fd.append('file', form.value.file)
    const t = await api.post('/api/templates', undefined, fd)
    dialog.value = false
    await load()
    open(t)
  } finally { uploading.value = false }
}
function open(row: any) { current.value = row; drawer.value = true }
async function patch(row: any, body: any) {
  await api.patch(`/api/templates/${current.value.id}/fields/${row.id}`, body)
  current.value = await api.get(`/api/templates/${current.value.id}`)
  load()
}
async function delField(row: any) {
  await api.del(`/api/templates/${current.value.id}/fields/${row.id}`)
  current.value = await api.get(`/api/templates/${current.value.id}`)
}
async function confirmAll() {
  await api.post(`/api/templates/${current.value.id}/confirm`)
  current.value = await api.get(`/api/templates/${current.value.id}`)
  ElMessage.success('映射已确认，生成文书时将使用')
}
async function reRecognize() {
  await api.post(`/api/templates/${current.value.id}/re-recognize`)
  current.value = await api.get(`/api/templates/${current.value.id}`)
}
onMounted(load)
</script>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
h3 { margin: 0; }
.dim { color: #909399; font-size: 13px; }
.ops { margin-bottom: 10px; display: flex; gap: 8px; }
</style>
