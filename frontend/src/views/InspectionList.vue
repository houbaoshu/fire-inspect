<template>
  <div>
    <div class="bar">
      <h3>检查列表</h3>
      <el-button type="primary" @click="dialog = true">新建检查</el-button>
    </div>
    <el-table :data="rows" @row-click="(r: any) => $router.push(`/inspections/${r.id}`)" highlight-current-row>
      <el-table-column prop="id" label="#" width="60" />
      <el-table-column prop="unit_name" label="被检查单位" />
      <el-table-column prop="address" label="地址" show-overflow-tooltip />
      <el-table-column prop="inspected_at" label="检查时间" width="180">
        <template #default="{ row }">{{ row.inspected_at?.replace('T', ' ').slice(0, 16) }}</template>
      </el-table-column>
      <el-table-column prop="checker_names" label="检查人员" width="140" />
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button text type="danger" @click.stop="del(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" title="新建检查" width="560px">
      <el-form label-width="110px">
        <el-form-item label="被检查单位"><el-input v-model="form.unit_name" /></el-form-item>
        <el-form-item label="地址"><el-input v-model="form.address" /></el-form-item>
        <el-form-item label="检查时间">
          <el-date-picker v-model="form.inspected_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" />
        </el-form-item>
        <el-form-item label="检查人员"><el-input v-model="form.checker_names" /></el-form-item>
        <el-form-item label="联系人及电话"><el-input v-model="form.contact" /></el-form-item>
        <el-form-item label="负责人"><el-input v-model="form.legal_person" /></el-form-item>
        <el-form-item label="检查类型">
          <el-select v-model="form.check_type" clearable>
            <el-option v-for="t in checkTypes" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.notes" type="textarea" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" @click="create">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api } from '../api'

const checkTypes = ["大型群众性活动举办前的检查", "建设工程施工现场检查", "对举报投诉的核查", "复查",
  "申请恢复施工、使用、生产、经营的检查", "申请解除临时查封的检查", "其它检查"]
const rows = ref<any[]>([])
const dialog = ref(false)
const form = ref<any>({ unit_name: '', address: '', inspected_at: null, checker_names: '', contact: '', legal_person: '', check_type: '', notes: '' })
const router = useRouter()

async function load() { rows.value = await api.get('/api/inspections') }
async function create() {
  const r = await api.post('/api/inspections', form.value)
  dialog.value = false
  router.push(`/inspections/${r.id}`)
}
async function del(row: any) {
  await ElMessageBox.confirm(`删除检查「${row.unit_name}」及其全部材料/文书？`, '确认', { type: 'warning' })
  await api.del(`/api/inspections/${row.id}`)
  load()
}
onMounted(load)
</script>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
h3 { margin: 0; }
</style>
