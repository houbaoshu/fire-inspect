import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('./views/Login.vue'), meta: { public: true } },
    { path: '/', redirect: '/inspections' },
    { path: '/inspections', component: () => import('./views/InspectionList.vue') },
    { path: '/inspections/:id', component: () => import('./views/InspectionDetail.vue') },
    { path: '/knowledge', component: () => import('./views/Knowledge.vue') },
    { path: '/templates', component: () => import('./views/Templates.vue') },
    { path: '/admin', component: () => import('./views/Admin.vue') },
  ],
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true
  try {
    const r = await fetch('/api/auth/me', { credentials: 'same-origin' })
    if (!r.ok) return '/login'
  } catch { return '/login' }
  return true
})
