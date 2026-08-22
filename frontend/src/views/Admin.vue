<template>
  <div>
    <h3>系统管理</h3>
    <el-row :gutter="16">
      <el-col :span="10">
        <el-card shadow="never">
          <template #header>
            用户
            <el-button size="small" style="float: right" @click="dialog = true">新增用户</el-button>
          </template>
          <el-table :data="users" size="small">
            <el-table-column prop="username" label="用户名" />
            <el-table-column label="角色" width="100">
              <template #default="{ row }">{{ row.role === 'admin' ? '管理员' : '检查员' }}</template>
            </el-table-column>
            <el-table-column label="" width="80">
              <template #default="{ row }">
                <el-button text size="small" type="danger" @click="delUser(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="14">
        <el-card shadow="never">
          <template #header>操作留痕</template>
          <el-form inline>
            <el-form-item label="对象类型">
              <el-select v-model="filterType" clearable style="width: 140px" @change="loadAudit">
                <el-option v-for="t in ['fact', 'document', 'inspection', 'material', 'template', 'knowledge_doc']"
                           :key="t" :label="t" :value="t" />
              </el-select>
            </el-form-item>
            <el-form-item label="对象 ID"><el-input-number v-model="filterId" :min="0" @change="loadAudit" /></el-form-item>
          </el-form>
          <el-table :data="auditRows" size="small" max-height="480">
            <el-table-column prop="at" label="时间" width="160">
              <template #default="{ row }">{{ row.at.replace('T', ' ').slice(0, 19) }}</template>
            </el-table-column>
            <el-table-column prop="actor" label="操作人" width="90" />
            <el-table-column prop="action" label="动作" width="90" />
            <el-table-column label="对象" width="110">
              <template #default="{ row }">{{ row.object_type }}#{{ row.object_id }}</template>
            </el-table-column>
            <el-table-column label="内容">
              <template #default="{ row }">
                <span v-if="row.detail.field">{{ row.detail.field }}：{{ String(row.detail.old).slice(0, 30) }} → {{ String(row.detail.new).slice(0, 30) }}</span>
                <span v-else>{{ row.detail.note }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="dialog" title="新增用户" width="380px">
      <el-form label-width="70px">
        <el-form-item label="用户名"><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="form.password" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role">
            <el-option label="检查员" value="inspector" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer><el-button type="primary" @click="createUser">创建</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'

const users = ref<any[]>([]); const auditRows = ref<any[]>([])
const dialog = ref(false); const form = ref<any>({ username: '', password: '', role: 'inspector' })
const filterType = ref(''); const filterId = ref(0)

async function load() { users.value = await api.get('/api/admin/users') }
async function loadAudit() {
  const p = new URLSearchParams()
  if (filterType.value) p.set('object_type', filterType.value)
  if (filterId.value) p.set('object_id', String(filterId.value))
  auditRows.value = await api.get(`/api/admin/audit?${p}`)
}
async function createUser() { await api.post('/api/admin/users', form.value); dialog.value = false; load() }
async function delUser(row: any) { await api.del(`/api/admin/users/${row.id}`); load() }
onMounted(() => { load(); loadAudit() })
</script>

<style scoped>
h3 { margin-top: 0; }
</style>
