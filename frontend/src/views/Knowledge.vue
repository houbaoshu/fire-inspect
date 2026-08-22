<template>
  <div>
    <div class="bar">
      <h3>法规知识库</h3>
      <el-button v-if="store.user?.role === 'admin'" type="primary" @click="dialog = true">上传知识文档</el-button>
    </div>
    <el-row :gutter="16">
      <el-col :span="14">
        <el-table :data="docs">
          <el-table-column prop="title" label="文档" />
          <el-table-column label="类型" width="130">
            <template #default="{ row }">
              <el-tag size="small" :type="row.doc_type === '正式法律法规' ? 'danger' : 'info'">{{ row.doc_type }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="{ ready: 'success', failed: 'danger', processing: 'warning' }[row.status]">
                {{ { ready: `已入库(${row.chunks})`, failed: '失败', processing: '解析中' }[row.status] }}
              </el-tag>
              <el-tooltip v-if="row.error" :content="row.error"><span class="err">?</span></el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150">
            <template #default="{ row }">
              <el-button text size="small" @click="view(row)">条款</el-button>
              <el-button text size="small" @click="download(row)">原文</el-button>
              <el-button v-if="store.user?.role === 'admin' && !row.builtin" text size="small" type="danger"
                         @click="del(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-col>
      <el-col :span="10">
        <el-card shadow="never">
          <template #header>法规检索（引用可追溯，AI 不伪造条款）</template>
          <KnowledgeQuery />
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="dialog" title="上传知识文档" width="480px">
      <el-form label-width="90px">
        <el-form-item label="标题"><el-input v-model="form.title" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.doc_type">
            <el-option v-for="t in types" :key="t" :label="t" :value="t" />
          </el-select>
          <div class="tip">内部制度等请如实选择类型，不会标记为正式法律法规（PRD §6）</div>
        </el-form-item>
        <el-form-item label="文件">
          <el-upload :auto-upload="false" :limit="1" :on-change="(f: any) => form.file = f.raw" accept=".docx,.pdf,.txt,.md">
            <el-button>选择文件</el-button>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer><el-button type="primary" :loading="uploading" @click="upload">上传并解析</el-button></template>
    </el-dialog>

    <el-drawer v-model="chunkDrawer" :title="currentDoc?.title" size="480px">
      <div v-for="c in chunks" :key="c.id" class="chunk">
        <b>{{ c.clause_label || '（前言/其他）' }}</b>
        <p>{{ c.text }}</p>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import { api } from '../api'
import { useAppStore } from '../store'
import KnowledgeQuery from '../components/KnowledgeQuery.vue'

const store = useAppStore()
const docs = ref<any[]>([]); const types = ref<string[]>([])
const dialog = ref(false); const uploading = ref(false)
const form = ref<any>({ title: '', doc_type: '其他', file: null })
const chunkDrawer = ref(false); const chunks = ref<any[]>([]); const currentDoc = ref<any>(null)

async function load() {
  docs.value = await api.get('/api/knowledge/docs')
  types.value = await api.get('/api/knowledge/types')
}
async function upload() {
  if (!form.value.file) return
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('title', form.value.title); fd.append('doc_type', form.value.doc_type); fd.append('file', form.value.file)
    await api.post('/api/knowledge/docs', undefined, fd)
    dialog.value = false; load()
  } finally { uploading.value = false }
}
async function del(row: any) {
  await ElMessageBox.confirm(`删除「${row.title}」？`, '确认', { type: 'warning' })
  await api.del(`/api/knowledge/docs/${row.id}`); load()
}
async function view(row: any) {
  currentDoc.value = row
  chunks.value = await api.get(`/api/knowledge/docs/${row.id}/chunks`)
  chunkDrawer.value = true
}
function download(row: any) { window.open(`/api/knowledge/docs/${row.id}/file`) }
onMounted(load)
</script>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
h3 { margin: 0; }
.err { color: #f56c6c; cursor: help; margin-left: 4px; }
.tip { font-size: 12px; color: #909399; }
.chunk { border-bottom: 1px dashed #e4e7ed; padding: 8px 0; }
.chunk p { margin: 4px 0; font-size: 13px; white-space: pre-wrap; color: #606266; }
</style>
