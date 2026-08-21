import { PlusOutlined, RightOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Card, Empty, Form, Input, List, Modal, Space, Typography } from 'antd'
import dayjs from 'dayjs'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type Inspection } from '../api'

export default function InspectionsPage() {
  const navigate = useNavigate()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()
  const inspections = useQuery({ queryKey: ['inspections'], queryFn: () => api<Inspection[]>('/inspections') })
  return (
    <div className="page">
      <div className="page-heading"><div><Typography.Title level={2}>检查任务</Typography.Title><Typography.Text type="secondary">以一次检查为中心整理事实、证据、法规与文书</Typography.Text></div><Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>创建检查</Button></div>
      <Card>
        <List loading={inspections.isLoading} dataSource={inspections.data ?? []} locale={{ emptyText: <Empty description="还没有检查任务" /> }} renderItem={item => (
          <List.Item actions={[<Button type="link" icon={<RightOutlined />} iconPosition="end" onClick={() => navigate(`/inspections/${item.id}`)}>进入工作区</Button>]}>
            <List.Item.Meta title={item.title} description={<Space><span>状态：{item.status}</span><span>更新：{dayjs(item.updated_at).format('YYYY-MM-DD HH:mm')}</span></Space>} />
          </List.Item>
        )} />
      </Card>
      <Modal title="创建一次检查" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} destroyOnHidden>
        <Form form={form} layout="vertical" onFinish={async values => {
          try {
            const item = await api<Inspection>('/inspections', { method: 'POST', body: JSON.stringify({ title: values.title, extra_fields: { organization: values.organization || '', address: values.address || '' } }) })
            await queryClient.invalidateQueries({ queryKey: ['inspections'] })
            setOpen(false); form.resetFields(); navigate(`/inspections/${item.id}`)
          } catch (error) { message.error(error instanceof Error ? error.message : '创建失败') }
        }}>
          <Form.Item name="title" label="检查名称" rules={[{ required: true }]}><Input placeholder="例如：某单位消防监督检查" /></Form.Item>
          <Form.Item name="organization" label="被检查单位（可后补）"><Input /></Form.Item>
          <Form.Item name="address" label="地址（可后补）"><Input /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

