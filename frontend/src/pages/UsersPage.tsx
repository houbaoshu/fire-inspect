import { PlusOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Card, Form, Input, Modal, Select, Table, Tag, Typography } from 'antd'
import { useState } from 'react'
import { api, type User } from '../api'

export default function UsersPage() {
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const users = useQuery({ queryKey: ['users'], queryFn: () => api<User[]>('/users') })
  return <div className="page">
    <div className="page-heading"><div><Typography.Title level={2}>用户管理</Typography.Title><Typography.Text type="secondary">MVP 仅提供管理员与检查人员两类角色</Typography.Text></div><Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>新增用户</Button></div>
    <Card><Table rowKey="id" dataSource={users.data} loading={users.isLoading} pagination={false} columns={[
      { title: '用户名', dataIndex: 'username' },
      { title: '角色', dataIndex: 'role', render: value => <Tag color={value === 'admin' ? 'red' : 'blue'}>{value === 'admin' ? '管理员' : '检查人员'}</Tag> },
      { title: '状态', dataIndex: 'active', render: value => value ? '启用' : '停用' },
    ]} /></Card>
    <Modal title="新增用户" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} destroyOnHidden>
      <Form form={form} layout="vertical" initialValues={{ role: 'inspector' }} onFinish={async values => {
        try { await api('/users', { method: 'POST', body: JSON.stringify(values) }); await queryClient.invalidateQueries({ queryKey: ['users'] }); setOpen(false); form.resetFields() }
        catch (error) { message.error(error instanceof Error ? error.message : '创建失败') }
      }}>
        <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 8 }]}><Input.Password /></Form.Item>
        <Form.Item name="role" label="角色"><Select options={[{ value: 'inspector', label: '检查人员' }, { value: 'admin', label: '管理员' }]} /></Form.Item>
      </Form>
    </Modal>
  </div>
}
