<template>
  <div class="login">
    <el-card class="card">
      <h2>消防监督检查 AI 辅助系统</h2>
      <el-form @submit.prevent="submit">
        <el-form-item><el-input v-model="username" placeholder="用户名" /></el-form-item>
        <el-form-item><el-input v-model="password" type="password" placeholder="密码" @keydown.enter="submit" /></el-form-item>
        <el-button type="primary" style="width: 100%" :loading="loading" @click="submit">登录</el-button>
      </el-form>
      <p class="tip">验收账号：admin / admin123，inspector / inspector123</p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { useAppStore } from '../store'

const username = ref(''); const password = ref(''); const loading = ref(false)
const router = useRouter(); const store = useAppStore()

async function submit() {
  loading.value = true
  try {
    store.user = await api.post('/api/auth/login', { username: username.value, password: password.value })
    router.push('/')
  } catch { /* api 层已提示 */ } finally { loading.value = false }
}
</script>

<style scoped>
.login { height: 100vh; display: flex; align-items: center; justify-content: center; background: #f0f2f5; }
.card { width: 360px; }
h2 { text-align: center; font-size: 18px; }
.tip { color: #909399; font-size: 12px; text-align: center; }
</style>
