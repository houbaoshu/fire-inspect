<template>
  <div>
    <div class="grid">
      <div v-for="k in frames" :key="k.id" class="cell" :class="{ selected: selected.includes(k.id) }">
        <el-image :src="k.url" fit="cover" class="img" :preview-src-list="[k.url]" preview-teleported />
        <div class="meta">
          <el-checkbox v-if="selectable" :model-value="selected.includes(k.id)"
                       @change="toggle(k.id)" />
          <span class="ts">{{ k.ts_ms != null ? fmtMs(k.ts_ms) : '上传' }}</span>
          <el-button text size="small" type="danger" @click="del(k)">删</el-button>
        </div>
        <div class="caption" :title="k.caption">{{ k.caption || '（无描述）' }}</div>
      </div>
    </div>
    <el-upload :show-file-list="false" :http-request="uploadPhoto" accept="image/*">
      <el-button size="small" style="margin-top: 8px">上传照片补充</el-button>
    </el-upload>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api, fmtMs } from '../api'

const props = defineProps<{ materialId?: number; inspectionId: number; selectable?: boolean; modelValue?: number[] }>()
const emit = defineEmits(['update:modelValue', 'refresh'])
const frames = ref<any[]>([])
const selected = ref<number[]>(props.modelValue || [])

async function load() {
  frames.value = props.materialId
    ? await api.get(`/api/materials/${props.materialId}/keyframes`)
    : await api.get(`/api/inspections/${props.inspectionId}/keyframes`)
}
function toggle(id: number) {
  const i = selected.value.indexOf(id)
  i >= 0 ? selected.value.splice(i, 1) : selected.value.push(id)
  emit('update:modelValue', [...selected.value])
}
async function del(k: any) { await api.del(`/api/keyframes/${k.id}`); load(); emit('refresh') }
async function uploadPhoto(opt: any) {
  const fd = new FormData(); fd.append('file', opt.file)
  await api.post(`/api/inspections/${props.inspectionId}/keyframes`, undefined, fd)
  load()
}
watch(() => props.modelValue, v => { selected.value = v || [] })
onMounted(load)
defineExpose({ reload: load })
</script>

<style scoped>
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }
.cell { border: 1px solid #e4e7ed; border-radius: 4px; overflow: hidden; background: #fff; }
.cell.selected { outline: 2px solid #409eff; }
.img { width: 100%; height: 110px; display: block; }
.meta { display: flex; align-items: center; justify-content: space-between; padding: 2px 6px; }
.ts { font-size: 12px; color: #606266; }
.caption { font-size: 12px; color: #909399; padding: 0 6px 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
