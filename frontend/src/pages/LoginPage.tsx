import { SafetyCertificateOutlined } from '@ant-design/icons'
import { useQueryClient } from '@tanstack/react-query'
import { App, Button, Card, Form, Input, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'
import { api, type User } from '../api'

export default function LoginPage() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  return (
    <div className="login-page">
      <Card className="login-card">
        <div className="login-mark"><SafetyCertificateOutlined /></div>
        <Typography.Title level={2}>消防监督检查</Typography.Title>
        <Typography.Paragraph type="secondary">AI 辅助系统 · MVP V1</Typography.Paragraph>
        <Form layout="vertical" initialValues={{ username: 'admin' }} onFinish={async values => {
          try {
            const user = await api<User>('/auth/login', { method: 'POST', body: JSON.stringify(values) })
            queryClient.setQueryData(['me'], user)
            navigate('/inspections')
          } catch (error) { message.error(error instanceof Error ? error.message : '登录失败') }
        }}>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input autoFocus /></Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}><Input.Password /></Form.Item>
          <Button block type="primary" htmlType="submit">登录</Button>
        </Form>
      </Card>
    </div>
  )
}

