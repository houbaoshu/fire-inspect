<template>
  <router-view v-if="$route.meta.public" />
  <el-container v-else class="layout">
    <el-header class="header">
      <span class="title" @click="$router.push('/')">消防监督检查 AI 辅助系统</span>
      <el-menu mode="horizontal" :default-active="$route.path" router class="menu">
        <el-menu-item index="/inspections">检查管理</el-menu-item>
        <el-menu-item index="/knowledge">法规知识库</el-menu-item>
        <el-menu-item index="/templates">文书模板</el-menu-item>
        <el-menu-item v-if="store.user?.role === 'admin'" index="/admin">系统管理</el-menu-item>
      </el-menu>
      <div class="right">
        <el-button text @click="store.assistantOpen = !store.assistantOpen">
          AI 助手{{ store.assistantOpen ? '收起' : '' }}
        </el-button>
        <el-dropdown>
          <span class="user">{{ store.user?.username }}（{{ store.user?.role === 'admin' ? '管理员' : '检查员' }}）</span>
          <template #dropdown>
            <el-dropdown-menu><el-dropdown-item @click="store.logout()">退出登录</el-dropdown-item></el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </el-header>
    <el-container>
      <el-main class="main"><router-view /></el-main>
      <AssistantPanel v-if="store.assistantOpen" />
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useAppStore } from './store'
import AssistantPanel from './components/AssistantPanel.vue'

const store = useAppStore()
onMounted(() => store.fetchUser().catch(() => {}))
</script>

<style>
body { margin: 0; font-family: 'Helvetica Neue', Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif; }
.layout { height: 100vh; }
.header { display: flex; align-items: center; border-bottom: 1px solid #e4e7ed; padding: 0 16px; }
.title { font-weight: 600; cursor: pointer; margin-right: 32px; white-space: nowrap; }
.menu { flex: 1; border-bottom: none; }
.right { display: flex; align-items: center; gap: 12px; }
.user { cursor: pointer; color: #606266; }
.main { background: #f5f7fa; }
</style>
