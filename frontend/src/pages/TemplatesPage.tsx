import { CloudUploadOutlined, DownloadOutlined, SettingOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Card, Form, Input, Modal, Select, Space, Table, Tag, Typography, Upload } from 'antd'
import { useState } from 'react'
import { api, type Template } from '../api'

const labels: Record<string, string> = { inspection_record: '消防监督检查记录', photo_record: '照片类文书', inquiry_record: '询问笔录' }

export default function TemplatesPage() {
  const [open, setOpen] = useState(false)
  const [file, setFile] = useState<File>()
  const [form] = Form.useForm()
  const [fieldForm] = Form.useForm()
  const [editing, setEditing] = useState<Template>()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => api<Template[]>('/templates') })
  return <div className="page">
    <div className="page-heading"><div><Typography.Title level={2}>文书模板</Typography.Title><Typography.Text type="secondary">模板负责格式，AI 只填写 Word 中明确声明的字段占位符</Typography.Text></div><Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setOpen(true)}>上传模板</Button></div>
    <Card><Table rowKey="id" dataSource={templates.data} loading={templates.isLoading} pagination={false} columns={[
      { title: '模板名称', dataIndex: 'name' },
      { title: '文书类型', dataIndex: 'document_type', render: value => labels[value] ?? value },
      { title: '来源', dataIndex: 'builtin', render: value => <Tag color={value ? 'red' : 'blue'}>{value ? '系统样例' : '我的模板'}</Tag> },
      { title: '字段', dataIndex: 'fields_json', render: fields => fields.map((field: Template['fields_json'][number]) => <Tag key={field.code}>{field.label}</Tag>) },
      { title: '操作', render: (_, row) => <Space><Button icon={<DownloadOutlined />} href={`/api/templates/${row.id}/content`}>下载样例</Button>{!row.builtin && <Button icon={<SettingOutlined />} onClick={() => { setEditing(row); fieldForm.setFieldsValue({ fields: row.fields_json }); }}>配置字段</Button>}</Space> },
    ]} /></Card>
    <Card className="tip-card" title="模板准备规则"><Alert type="info" showIcon message={<span><Typography.Text code>{'{{ field_code }}'}</Typography.Text> 表示占位符格式，不是必须使用的固定名称。</span>} /><Typography.Paragraph className="template-help">请在 DOCX 正文或表格单元格中直接输入占位符，例如 <Typography.Text code>{'{{ organization }}'}</Typography.Text>、<Typography.Text code>{'{{ facts }}'}</Typography.Text> 或 <Typography.Text code>{'{{ photos }}'}</Typography.Text>。必须使用英文半角花括号，字段名称只能包含字母、数字和下划线。可先下载上方系统样例进行修改。</Typography.Paragraph></Card>
    <Modal title="上传 Word 模板" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} destroyOnHidden>
      <Alert className="template-upload-alert" type="info" showIcon message="请先在 Word 中加入有效占位符" description={<span>例如 <Typography.Text code>{'{{ organization }}'}</Typography.Text>；<Typography.Text code>{'{{ field_code }}'}</Typography.Text> 只是格式说明。</span>} />
      <Form form={form} layout="vertical" initialValues={{ document_type: 'inspection_record' }} onFinish={async values => {
        if (!file) return message.warning('请选择 DOCX 文件')
        const data = new FormData(); data.append('name', values.name); data.append('document_type', values.document_type); data.append('file', file)
        try { await api('/templates', { method: 'POST', body: data }); await queryClient.invalidateQueries({ queryKey: ['templates'] }); setOpen(false); setFile(undefined); form.resetFields(); message.success('模板已校验并上传') }
        catch (error) { message.error(error instanceof Error ? error.message : '上传失败') }
      }}>
        <Form.Item name="name" label="模板名称" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="document_type" label="文书类型"><Select options={Object.entries(labels).map(([value, label]) => ({ value, label }))} /></Form.Item>
        <Form.Item label="DOCX 文件"><Upload beforeUpload={selected => { setFile(selected); return false }} maxCount={1} accept=".docx"><Button icon={<CloudUploadOutlined />}>选择文件</Button></Upload></Form.Item>
      </Form>
    </Modal>
    <Modal width={720} title={`配置字段 · ${editing?.name ?? ''}`} open={Boolean(editing)} onCancel={() => setEditing(undefined)} onOk={() => fieldForm.submit()} destroyOnHidden>
      <AlertText />
      <Form form={fieldForm} layout="vertical" onFinish={async values => {
        if (!editing) return
        try { await api(`/templates/${editing.id}/fields`, { method: 'PATCH', body: JSON.stringify(values) }); await queryClient.invalidateQueries({ queryKey: ['templates'] }); setEditing(undefined); message.success('字段配置已保存') }
        catch (error) { message.error(error instanceof Error ? error.message : '保存失败') }
      }}>
        <Form.List name="fields">{fields => <>{fields.map(({ key, name }) => <Card size="small" key={key} className="field-config-card"><Form.Item name={[name, 'code']} label="占位符代码"><Input disabled /></Form.Item><Form.Item name={[name, 'label']} label="显示名称" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name={[name, 'type']} label="字段类型"><Select options={[{ value: 'text', label: '单行文本' }, { value: 'multiline', label: '多行文本' }, { value: 'date', label: '日期' }, { value: 'image', label: '单张图片' }, { value: 'photo_list', label: '照片列表' }]} /></Form.Item><Form.Item name={[name, 'description']} label="生成说明"><Input /></Form.Item></Card>)}</>}</Form.List>
      </Form>
    </Modal>
  </div>
}

function AlertText() {
  return <Typography.Paragraph type="secondary">可调整字段名称、类型与生成说明；占位符代码来自 Word 模板，不能在这里修改。</Typography.Paragraph>
}
