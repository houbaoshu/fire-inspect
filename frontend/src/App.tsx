import { BookOutlined, FileTextOutlined, FireOutlined, LogoutOutlined, SafetyCertificateOutlined, TeamOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Layout, Menu, Result, Spin, Typography } from 'antd'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { api, type User } from './api'
import InspectionWorkspace from './pages/InspectionWorkspace'
import InspectionsPage from './pages/InspectionsPage'
import KnowledgePage from './pages/KnowledgePage'
import LoginPage from './pages/LoginPage'
import TemplatesPage from './pages/TemplatesPage'
import UsersPage from './pages/UsersPage'

const { Header, Sider, Content } = Layout

function Shell({ user }: { user: User }) {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const items = [
    { key: '/inspections', icon: <FireOutlined />, label: '检查任务' },
    { key: '/templates', icon: <FileTextOutlined />, label: '文书模板' },
    ...(user.role === 'admin' ? [
      { key: '/knowledge', icon: <BookOutlined />, label: '知识库' },
      { key: '/users', icon: <TeamOutlined />, label: '用户管理' },
    ] : []),
  ]
  const selected = items.find(item => location.pathname.startsWith(item.key))?.key ?? '/inspections'
  return (
    <Layout className="app-shell">
      <Sider width={228} theme="light" className="sidebar">
        <div className="brand"><SafetyCertificateOutlined /><span>消防检查 AI</span></div>
        <Menu mode="inline" selectedKeys={[selected]} items={items} onClick={({ key }) => navigate(key)} />
        <div className="user-panel">
          <Typography.Text strong>{user.username}</Typography.Text>
          <Typography.Text type="secondary">{user.role === 'admin' ? '管理员' : '检查人员'}</Typography.Text>
          <Button type="text" icon={<LogoutOutlined />} onClick={async () => {
            await api('/auth/logout', { method: 'POST' })
            queryClient.setQueryData(['me'], null)
            navigate('/login')
          }}>退出登录</Button>
        </div>
      </Sider>
      <Layout>
        <Header className="topbar"><span>AI 结果仅供执法参考，最终内容须由检查人员确认</span></Header>
        <Content className="content">
          <Routes>
            <Route path="/inspections" element={<InspectionsPage />} />
            <Route path="/inspections/:inspectionId" element={<InspectionWorkspace />} />
            <Route path="/templates" element={<TemplatesPage />} />
            <Route path="/knowledge" element={user.role === 'admin' ? <KnowledgePage /> : <Navigate to="/inspections" />} />
            <Route path="/users" element={user.role === 'admin' ? <UsersPage /> : <Navigate to="/inspections" />} />
            <Route path="*" element={<Navigate to="/inspections" replace />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}

export default function App() {
  const me = useQuery({ queryKey: ['me'], queryFn: () => api<User>('/auth/me'), retry: false })
  if (me.isLoading) return <div className="center"><Spin size="large" /></div>
  return (
    <Routes>
      <Route path="/login" element={me.data ? <Navigate to="/inspections" replace /> : <LoginPage />} />
      <Route path="*" element={me.data ? <Shell user={me.data} /> : <Navigate to="/login" replace />} />
    </Routes>
  )
}

