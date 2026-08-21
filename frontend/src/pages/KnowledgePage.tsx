import { CloudUploadOutlined, ReloadOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Card, Form, Input, Modal, Select, Space, Switch, Table, Tag, Typography, Upload } from 'antd'
import dayjs from 'dayjs'
import { useState } from 'react'
import { api } from '../api'

type KnowledgeDocument = { id: string; title: string; filename: string; material_type: string; enabled: boolean; indexing_status: string; indexing_error?: string; created_at: string }

export default function KnowledgePage() {
  const [open, setOpen] = useState(false)
  const [file, setFile] = useState<File>()
  const [form] = Form.useForm()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const documents = useQuery({ queryKey: ['knowledge-documents'], queryFn: () => api<KnowledgeDocument[]>('/knowledge-documents'), refetchInterval: query => query.state.data?.some(item => item.indexing_status === 'pending' || item.indexing_status === 'processing') ? 2500 : false })
  return <div className="page">
    <div className="page-heading"><div><Typography.Title level={2}>法规与业务知识库</Typography.Title><Typography.Text type="secondary">所有回答仅引用已入库原文；扫描版 PDF 暂不支持</Typography.Text></div><Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setOpen(true)}>上传知识文档</Button></div>
    <Card><Table rowKey="id" dataSource={documents.data} loading={documents.isLoading} pagination={false} columns={[
      { title: '标题', dataIndex: 'title', render: (value, row) => <div><a href={`/api/knowledge-documents/${row.id}/content`} target="_blank">{value}</a><div className="muted">{row.filename}</div></div> },
      { title: '材料性质', dataIndex: 'material_type', render: value => <Tag color={value === 'formal' ? 'red' : 'gold'}>{value === 'formal' ? '正式法规/标准' : '业务参考材料'}</Tag> },
      { title: '索引状态', dataIndex: 'indexing_status', render: (value, row) => <div><Tag color={value === 'ready' ? 'green' : value === 'failed' ? 'red' : 'blue'}>{value}</Tag>{row.indexing_error && <div className="error-text">{row.indexing_error}</div>}</div> },
      { title: '启用', dataIndex: 'enabled', render: (value, row) => <Switch checked={value} onChange={async enabled => { await api(`/knowledge-documents/${row.id}?enabled=${enabled}`, { method: 'PATCH' }); queryClient.invalidateQueries({ queryKey: ['knowledge-documents'] }) }} /> },
      { title: '上传时间', dataIndex: 'created_at', render: value => dayjs(value).format('YYYY-MM-DD HH:mm') },
      { title: '操作', render: (_, row) => row.indexing_status === 'failed' && <Button icon={<ReloadOutlined />} onClick={async () => { await api(`/knowledge-documents/${row.id}/retry`, { method: 'POST' }); queryClient.invalidateQueries({ queryKey: ['knowledge-documents'] }) }}>重试</Button> },
    ]} /></Card>
    <Modal title="上传知识文档" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} destroyOnHidden>
      <Form form={form} layout="vertical" initialValues={{ material_type: 'reference' }} onFinish={async values => {
        if (!file) return message.warning('请选择文件')
        const data = new FormData(); data.append('title', values.title); data.append('material_type', values.material_type); data.append('file', file)
        try { await api('/knowledge-documents', { method: 'POST', body: data }); await queryClient.invalidateQueries({ queryKey: ['knowledge-documents'] }); setOpen(false); setFile(undefined); form.resetFields() }
        catch (error) { message.error(error instanceof Error ? error.message : '上传失败') }
      }}>
        <Form.Item name="title" label="文档标题" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="material_type" label="材料性质"><Select options={[{ value: 'reference', label: '业务参考材料' }, { value: 'formal', label: '正式法规/标准' }]} /></Form.Item>
        <Form.Item label="文件"><Upload beforeUpload={selected => { setFile(selected); return false }} maxCount={1} accept=".pdf,.docx,.txt"><Button icon={<CloudUploadOutlined />}>选择 PDF / DOCX / TXT</Button></Upload></Form.Item>
      </Form>
    </Modal>
  </div>
}

