import { defineStore } from 'pinia'
import { api } from './api'

export interface AssistantAction { type: string; [k: string]: any }
export interface ChatMsg { role: 'user' | 'ai'; text: string; actions?: AssistantAction[]; suggestion?: string }

export const useAppStore = defineStore('app', {
  state: () => ({
    user: null as null | { id: number; username: string; role: string },
    assistantOpen: false,
    chat: [] as ChatMsg[],
    chatLoading: false,
    // 助手导航动作用的页面事件总线（InspectionDetail 注册处理器）
    actionHandler: null as null | ((a: AssistantAction) => void),
  }),
  actions: {
    async fetchUser() { this.user = await api.get('/api/auth/me') },
    async logout() { await api.post('/api/auth/logout'); this.user = null; location.href = '/login' },
    async ask(message: string, inspectionId: number | null, page: string, selected: any = {}) {
      this.chat.push({ role: 'user', text: message })
      this.chatLoading = true
      try {
        const r = await api.post('/api/assistant/chat', { message, inspection_id: inspectionId, page, selected })
        this.chat.push({ role: 'ai', text: r.answer, actions: r.actions, suggestion: r.suggestion })
      } catch {
        this.chat.push({ role: 'ai', text: '（助手暂时不可用，业务功能不受影响）' })
      } finally { this.chatLoading = false }
    },
  },
})
