import { useEffect, useState, type ReactNode } from 'react'
import {
  BookOutlined,
  CheckCircleOutlined,
  CloudUploadOutlined,
  FileWordOutlined,
  FireOutlined,
  LogoutOutlined,
  MessageOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Drawer,
  Empty,
  Flex,
  Form,
  Image,
  Input,
  Layout,
  List,
  Menu,
  Modal,
  Progress,
  Select,
  Space,
  Spin,
  Steps,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
} from 'antd'
import type { UploadRequestOption } from 'rc-upload/lib/interface'
import { Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, ApiError, download, streamAssistant, uploadMaterial } from './api'
import { buildDefaultDocumentTitle } from './documentNaming'
import type {
  AssistantResult,
  DocumentField,
  DraftDocument,
  Evidence,
  Fact,
  Inspection,
  KnowledgeDocument,
  Material,
  Template,
  TemplateRegion,
  TemplateVersion,
  TranscriptSegment,
  User,
} from './types'

const { Header, Content, Sider } = Layout
const { Title, Text, Paragraph } = Typography

const documentLabels: Record<string, string> = {
  inspection_record: '消防监督检查记录',
  photo_document: '照片类文书',
  interview_transcript: '询问笔录',
}

const stepLabels: Record<string, string> = {
  metadata: '元数据', transcription: '转写', keyframes: '关键画面',
  ocr_vision: '图像分析', fact_extraction: '事实提取',
}

function stepResult(step: Material['steps'][number]): string | undefined {
  if (step.status !== 'succeeded') return undefined
  const count = (key: string) => Number(step.artifact[key] || 0)
  if (step.kind === 'transcription') return `已生成 ${count('segment_count')} 段转写`
  if (step.kind === 'keyframes') return `已保存 ${count('frame_count')} 张关键画面`
  if (step.kind === 'ocr_vision') return `已分析 ${count('analyzed_count') || count('image_count')} 张画面`
  if (step.kind === 'fact_extraction') return `已生成 ${count('candidate_count')} 条候选事实`
  return undefined
}

function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const { message } = AntApp.useApp()
  const [loading, setLoading] = useState(false)
  const submit = async (values: { username: string; password: string }) => {
    setLoading(true)
    try {
      await api('/auth/login', { method: 'POST', body: JSON.stringify(values) })
      onLoggedIn()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '登录失败')
    } finally { setLoading(false) }
  }
  return <div className="login-page">
    <Card className="login-card">
      <div className="brand-mark"><FireOutlined /></div>
      <Title level={2}>消防监督检查</Title>
      <Paragraph type="secondary">AI 辅助系统 · 所有结果均需人工确认</Paragraph>
      <Form layout="vertical" onFinish={submit} size="large">
        <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input autoFocus /></Form.Item>
        <Form.Item name="password" label="密码" rules={[{ required: true }]}><Input.Password /></Form.Item>
        <Button block type="primary" htmlType="submit" loading={loading}>登录</Button>
      </Form>
    </Card>
  </div>
}

function Shell({ user }: { user: User }) {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const [assistantOpen, setAssistantOpen] = useState(false)
  const inspectionMatch = location.pathname.match(/\/inspections\/([^/]+)/)
  const selected = location.pathname.startsWith('/knowledge') ? 'knowledge'
    : location.pathname.startsWith('/templates') ? 'templates' : 'inspections'
  const logout = async () => {
    await api('/auth/logout', { method: 'POST' })
    queryClient.clear()
    window.location.reload()
  }
  return <Layout className="app-shell">
    <Sider width={232} theme="light" className="sidebar">
      <div className="logo"><FireOutlined /><span>消防检查 AI</span></div>
      <Menu mode="inline" selectedKeys={[selected]} onClick={({ key }) => navigate(`/${key}`)} items={[
        { key: 'inspections', icon: <FireOutlined />, label: '检查管理' },
        { key: 'knowledge', icon: <BookOutlined />, label: '法规与知识' },
        { key: 'templates', icon: <FileWordOutlined />, label: 'Word 模板' },
      ]} />
      <div className="sidebar-user">
        <Text strong>{user.display_name}</Text><br />
        <Text type="secondary">{user.role === 'admin' ? '管理员' : '检查人员'}</Text>
        <Button type="text" icon={<LogoutOutlined />} onClick={logout}>退出</Button>
      </div>
    </Sider>
    <Layout>
      <Header className="topbar">
        <div><Text type="secondary">AI 生成内容仅供参考，正式判断由执法人员完成</Text></div>
        <Button icon={<MessageOutlined />} type={assistantOpen ? 'primary' : 'default'} onClick={() => setAssistantOpen(true)}>AI 助手</Button>
      </Header>
      <Content className="content"><Routes>
        <Route path="/inspections" element={<InspectionList />} />
        <Route path="/inspections/:inspectionId" element={<InspectionDetail />} />
        <Route path="/knowledge" element={<KnowledgePage admin={user.role === 'admin'} />} />
        <Route path="/templates" element={<TemplatesPage admin={user.role === 'admin'} />} />
        <Route path="*" element={<Navigate to="/inspections" replace />} />
      </Routes></Content>
    </Layout>
    <AssistantDrawer open={assistantOpen} onClose={() => setAssistantOpen(false)} inspectionId={inspectionMatch?.[1]} />
  </Layout>
}

function PageHeading({ title, description, extra }: { title: string; description: string; extra?: ReactNode }) {
  return <Flex justify="space-between" align="flex-start" className="page-heading">
    <div><Title level={2}>{title}</Title><Text type="secondary">{description}</Text></div>{extra}
  </Flex>
}

function InspectionList() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { message } = AntApp.useApp()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()
  const { data = [], isLoading } = useQuery({ queryKey: ['inspections'], queryFn: () => api<Inspection[]>('/inspections') })
  const create = useMutation({
    mutationFn: (values: Record<string, unknown>) => api<Inspection>('/inspections', { method: 'POST', body: JSON.stringify(values) }),
    onSuccess: (inspection) => { queryClient.invalidateQueries({ queryKey: ['inspections'] }); setOpen(false); form.resetFields(); navigate(`/inspections/${inspection.id}`) },
    onError: (error) => message.error(error.message),
  })
  return <>
    <PageHeading title="检查管理" description="以一次检查为中心整理材料、事实、证据与文书" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>创建检查</Button>} />
    <Spin spinning={isLoading}><div className="card-grid">
      {data.map((item) => <Card key={item.id} hoverable onClick={() => navigate(`/inspections/${item.id}`)}>
        <Flex justify="space-between"><Tag color={item.status === 'open' ? 'green' : 'default'}>{item.status === 'open' ? '进行中' : '已归档'}</Tag><Text type="secondary">{new Date(item.updated_at).toLocaleDateString()}</Text></Flex>
        <Title level={4}>{item.title}</Title>
        <Paragraph type="secondary">{item.unit_name || '尚未填写被检查单位'}</Paragraph>
        <Text>{item.address || '尚未填写地址'}</Text>
      </Card>)}
    </div>{!isLoading && !data.length && <Empty description="还没有检查记录" />}</Spin>
    <Modal title="创建一次检查" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} okText="创建" confirmLoading={create.isPending}>
      <Form form={form} layout="vertical" onFinish={(values) => create.mutate(values)}>
        <Form.Item name="title" label="检查名称" rules={[{ required: true }]}><Input placeholder="例如：某商场消防监督检查" /></Form.Item>
        <Form.Item name="unit_name" label="被检查单位"><Input /></Form.Item>
        <Form.Item name="address" label="地址"><Input /></Form.Item>
      </Form>
    </Modal>
  </>
}

function InspectionDetail() {
  const { inspectionId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const { data, isLoading } = useQuery({ queryKey: ['inspection', inspectionId], queryFn: () => api<Inspection>(`/inspections/${inspectionId}`) })
  if (isLoading || !data) return <Spin fullscreen />
  const tab = params.get('tab') || 'overview'
  return <>
    <PageHeading title={data.title} description={data.unit_name || '被检查单位待补充'} extra={<Tag color="red">{data.status === 'open' ? '进行中' : '已归档'}</Tag>} />
    <Tabs activeKey={tab} onChange={(key) => setParams({ tab: key })} items={[
      { key: 'overview', label: '概览', children: <Overview inspection={data} /> },
      { key: 'materials', label: '检查材料', children: <Materials inspectionId={inspectionId} /> },
      { key: 'facts', label: '检查事实', children: <Facts inspectionId={inspectionId} /> },
      { key: 'documents', label: '检查文书', children: <Documents inspection={data} /> },
    ]} />
  </>
}

function Overview({ inspection }: { inspection: Inspection }) {
  return <Card><Descriptions column={2} items={[
    { key: 'unit', label: '被检查单位', children: inspection.unit_name || '待补充' },
    { key: 'address', label: '地址', children: inspection.address || '待补充' },
    { key: 'time', label: '检查时间', children: inspection.inspected_at ? new Date(inspection.inspected_at).toLocaleString() : '待补充' },
    { key: 'status', label: '状态', children: inspection.status === 'open' ? '进行中' : '已归档' },
  ]} /></Card>
}

function Materials({ inspectionId }: { inspectionId: string }) {
  const queryClient = useQueryClient()
  const { message } = AntApp.useApp()
  const [progress, setProgress] = useState<number>()
  const [transcriptMaterial, setTranscriptMaterial] = useState<Material>()
  const [searchParams] = useSearchParams()
  const { data = [] } = useQuery({
    queryKey: ['materials', inspectionId],
    queryFn: () => api<Material[]>(`/inspections/${inspectionId}/materials`),
    refetchInterval: (query) => query.state.data?.some((m) => m.steps.some((s) => ['pending', 'running'].includes(s.status))) ? 2000 : false,
  })
  const { data: imageEvidence = [] } = useQuery({
    queryKey: ['evidence', inspectionId],
    queryFn: () => api<Evidence[]>(`/inspections/${inspectionId}/evidence`),
  })
  const { data: transcript = [] } = useQuery({
    queryKey: ['transcript', transcriptMaterial?.id],
    queryFn: () => api<TranscriptSegment[]>(`/materials/${transcriptMaterial?.id}/transcript`),
    enabled: !!transcriptMaterial,
  })
  useEffect(() => {
    const materialId = searchParams.get('material')
    const startMs = Number(searchParams.get('start') || 0)
    if (!materialId) return
    const player = document.getElementById(`media-${materialId}`) as HTMLMediaElement | null
    if (player) {
      player.currentTime = startMs / 1000
      player.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [data, searchParams])
  const upload = async (options: UploadRequestOption) => {
    const file = options.file as File
    try {
      await uploadMaterial(inspectionId, file, setProgress)
      message.success('上传完成，已开始处理')
      await queryClient.refetchQueries({ queryKey: ['materials', inspectionId], type: 'active' })
      await queryClient.invalidateQueries({ queryKey: ['evidence', inspectionId] })
      options.onSuccess?.({})
    } catch (error) { options.onError?.(error as Error); message.error((error as Error).message) }
    finally { setProgress(undefined) }
  }
  const retry = async (materialId: string, kind: string) => {
    await api(`/materials/${materialId}/steps/${kind}/retry`, { method: 'POST' })
    queryClient.invalidateQueries({ queryKey: ['materials', inspectionId] })
  }
  const updateTranscript = async (segment: TranscriptSegment, changes: Record<string, unknown>) => {
    try {
      await api(`/transcript/${segment.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ ...changes, row_version: segment.row_version }),
      })
      await queryClient.invalidateQueries({ queryKey: ['transcript', segment.material_id] })
    } catch (error) { message.error((error as Error).message) }
  }
  const removeImage = async (evidenceId: string) => {
    await api(`/evidence/${evidenceId}`, { method: 'DELETE' })
    await queryClient.invalidateQueries({ queryKey: ['evidence', inspectionId] })
    message.success('已删除错误候选画面')
  }
  return <Space direction="vertical" size="large" style={{ width: '100%' }}>
    <Upload.Dragger multiple customRequest={upload} showUploadList={false}>
      <p className="ant-upload-drag-icon"><CloudUploadOutlined /></p><p>点击或拖入视频、照片、录音、文字及其他证据材料</p><Text type="secondary">大文件自动分片；单个处理步骤失败不会影响原始材料</Text>
    </Upload.Dragger>
    {progress !== undefined && <Progress percent={progress} />}
    <List dataSource={data} locale={{ emptyText: '暂无材料' }} renderItem={(material) => <List.Item>
      <Card className="material-card">
        <Flex justify="space-between"><div><Text strong>{material.filename}</Text><br /><Text type="secondary">{material.kind} · {(material.size_bytes / 1024 / 1024).toFixed(2)} MiB</Text></div><Space>{['video', 'audio', 'text'].includes(material.kind) && <Button onClick={() => setTranscriptMaterial(material)}>查看转写</Button>}<Button href={`/api/v1/materials/${material.id}/download`} target="_blank">查看原件</Button></Space></Flex>
        {material.kind === 'video' && <video id={`media-${material.id}`} className="media-preview" controls src={`/api/v1/materials/${material.id}/download`} />}
        {material.kind === 'audio' && <audio id={`media-${material.id}`} className="audio-preview" controls src={`/api/v1/materials/${material.id}/download`} />}
        {material.kind === 'image' && <Image className="image-preview" src={`/api/v1/materials/${material.id}/download`} alt={material.filename} />}
        <Steps size="small" responsive items={material.steps.map((step) => ({
          title: stepLabels[step.kind] || step.kind,
          status: step.status === 'succeeded' ? 'finish' : step.status === 'failed' ? 'error' : step.status === 'running' ? 'process' : 'wait',
          description: step.status === 'failed' ? <Space direction="vertical"><Text type="danger">{step.error_message}</Text><Button size="small" icon={<ReloadOutlined />} onClick={() => retry(material.id, step.kind)}>重试</Button></Space> : stepResult(step),
        }))} />
      </Card>
    </List.Item>} />
    {!!imageEvidence.filter((item) => item.type === 'image').length && <Card title="候选照片与关键画面"><div className="evidence-gallery">{imageEvidence.filter((item) => item.type === 'image').map((item) => <Card key={item.id} size="small" cover={<Image height={150} style={{ objectFit: 'cover' }} src={`/api/v1/evidence/${item.id}/content`} alt={item.label} />} actions={[<Button danger type="text" key="delete" onClick={() => removeImage(item.id)}>删除错误候选</Button>]}><Card.Meta title={item.label} description={item.start_ms !== undefined ? `${(item.start_ms / 1000).toFixed(1)} 秒` : '上传照片'} /></Card>)}</div></Card>}
    <Drawer title={`${transcriptMaterial?.filename || ''} · 带时间转写`} open={!!transcriptMaterial} onClose={() => setTranscriptMaterial(undefined)} width={680}>
      <Alert showIcon message="无法可靠识别身份时只使用通用说话人标签。填写身份并确认后才会用于询问笔录。" />
      <List dataSource={transcript} locale={{ emptyText: '暂无可用转写' }} renderItem={(segment) => <List.Item><Card size="small" style={{ width: '100%' }}><Space direction="vertical" style={{ width: '100%' }}><Text type="secondary">{(segment.start_ms / 1000).toFixed(1)}s – {(segment.end_ms / 1000).toFixed(1)}s</Text><Input addonBefore={segment.speaker_label} key={`speaker-${segment.id}-${segment.row_version}`} defaultValue={segment.speaker_identity} placeholder="人工填写实际身份" onBlur={(event) => { if (event.target.value !== (segment.speaker_identity || '')) updateTranscript(segment, { speaker_identity: event.target.value || null, identity_confirmed: !!event.target.value }) }} /><Input.TextArea key={`text-${segment.id}-${segment.row_version}`} defaultValue={segment.text} autoSize={{ minRows: 2, maxRows: 6 }} onBlur={(event) => { if (event.target.value !== segment.text) updateTranscript(segment, { text: event.target.value }) }} />{segment.identity_confirmed ? <Tag color="green">身份已人工确认</Tag> : <Tag color="orange">身份待确认</Tag>}</Space></Card></List.Item>} />
    </Drawer>
  </Space>
}

function Facts({ inspectionId }: { inspectionId: string }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { message } = AntApp.useApp()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()
  const { data = [] } = useQuery({ queryKey: ['facts', inspectionId], queryFn: () => api<Fact[]>(`/inspections/${inspectionId}/facts`) })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['facts', inspectionId] })
  const action = async (path: string) => { try { await api(path, { method: 'POST' }); refresh() } catch (error) { message.error((error as Error).message) } }
  const create = async (values: { title: string; detail?: string }) => { await api(`/inspections/${inspectionId}/facts`, { method: 'POST', body: JSON.stringify({ ...values, evidence_ids: [] }) }); setOpen(false); form.resetFields(); refresh() }
  return <>
    <Flex justify="space-between" className="section-actions"><Alert showIcon message="AI 提取内容均为候选事实，确认前不会用于正式文书。" /><Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>补充事实</Button></Flex>
    <List grid={{ gutter: 16, column: 2 }} dataSource={data} locale={{ emptyText: '尚未提取或补充检查事实' }} renderItem={(fact) => <List.Item>
      <Card title={<Space><Tag color={fact.status === 'confirmed' ? 'green' : 'orange'}>{fact.status === 'confirmed' ? '已确认' : '待确认'}</Tag>{fact.title}</Space>}>
        {fact.detail && <Paragraph>{fact.detail}</Paragraph>}
        <List size="small" header="关联证据" dataSource={fact.evidence} locale={{ emptyText: '暂无关联证据' }} renderItem={(item) => <List.Item actions={item.type === 'image' ? [<a key="image" href={`/api/v1/evidence/${item.id}/content`} target="_blank">查看图片</a>] : item.material_id ? [<Button key="media" type="link" onClick={() => navigate(`/inspections/${inspectionId}?tab=materials&material=${item.material_id}&start=${item.start_ms || 0}`)}>定位原媒体</Button>] : undefined}><Text type="secondary">{item.label}{item.quote ? ` · ${item.quote}` : ''}</Text></List.Item>} />
        <Flex justify="end">{fact.status === 'confirmed' ? <Button onClick={() => action(`/facts/${fact.id}/unconfirm`)}>取消确认</Button> : <Button type="primary" icon={<CheckCircleOutlined />} onClick={() => action(`/facts/${fact.id}/confirm`)}>确认事实</Button>}</Flex>
      </Card>
    </List.Item>} />
    <Modal title="补充检查事实" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()}><Form form={form} layout="vertical" onFinish={create}><Form.Item name="title" label="事实" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="detail" label="补充说明"><Input.TextArea rows={4} /></Form.Item></Form></Modal>
  </>
}

function DocumentFieldEditor({
  field,
  onSave,
  onRegenerate,
}: {
  field: DocumentField
  onSave: (value: unknown) => void
  onRegenerate: () => void
}) {
  const label = field.label || field.field_key
  const values = Array.isArray(field.value) ? field.value : undefined
  return <div style={{ width: '100%' }}>
    <Flex justify="space-between">
      <Text strong>{label}</Text>
      <Space>
        <Tag>{field.source === 'human' ? '人工' : 'AI 初稿'}</Tag>
        {field.is_shared && <Tag color="cyan">检查共享字段</Tag>}
        {field.is_evidence_linked && <Tag color="purple">共享证据链</Tag>}
        {field.is_locked && <Tag color="blue">已保护</Tag>}
        {field.is_stale && <Tag color="orange">来源已更新，请核对</Tag>}
        {field.field_type !== 'image' && <Button size="small" onClick={onRegenerate}>局部重新生成</Button>}
      </Space>
    </Flex>
    {field.field_type === 'image' && values ? <div className="evidence-gallery">
      {values.map((item, index) => {
        const photo = item as { evidence_id?: string; caption?: string }
        return <Card key={photo.evidence_id || index} size="small" cover={photo.evidence_id ? <Image height={120} style={{ objectFit: 'cover' }} src={`/api/v1/evidence/${photo.evidence_id}/content`} /> : undefined}>
          <Card.Meta title={`第 ${index + 1} 张`} description={photo.caption || '照片或关键画面'} />
        </Card>
      })}
    </div> : values ? <Space direction="vertical" style={{ width: '100%' }}>
      {values.map((item, index) => {
        const current = typeof item === 'string' ? item : String(item ?? '')
        return <div key={`${field.id}-${field.row_version}-${index}`} style={{ width: '100%' }}>
          <Text type="secondary">第 {index + 1} 张 · {label}</Text>
          <Input.TextArea defaultValue={current} autoSize={{ minRows: 2, maxRows: 8 }} onBlur={(event) => {
            if (event.target.value === current) return
            const next = [...values]
            next[index] = event.target.value
            onSave(next)
          }} />
        </div>
      })}
    </Space> : <Input.TextArea key={`${field.id}-${field.row_version}`} defaultValue={typeof field.value === 'string' ? field.value : JSON.stringify(field.value, null, 2)} autoSize={{ minRows: 2, maxRows: 8 }} onBlur={(event) => { if (event.target.value !== field.value) onSave(event.target.value) }} />}
  </div>
}

function Documents({ inspection }: { inspection: Inspection }) {
  const inspectionId = inspection.id
  const queryClient = useQueryClient()
  const { message } = AntApp.useApp()
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<DraftDocument>()
  const [proposals, setProposals] = useState<Array<{ field_id: string; current_value: unknown; proposed_value: unknown; locked: boolean }>>([])
  const [automaticTitle, setAutomaticTitle] = useState<string>()
  const [form] = Form.useForm()
  const selectedTemplateId = Form.useWatch('template_id', form)
  const { data = [] } = useQuery({ queryKey: ['documents', inspectionId], queryFn: () => api<DraftDocument[]>(`/inspections/${inspectionId}/documents`) })
  const { data: templates = [] } = useQuery({ queryKey: ['templates'], queryFn: () => api<Template[]>('/templates') })
  const { data: evidence = [] } = useQuery({ queryKey: ['evidence', inspectionId], queryFn: () => api<Evidence[]>(`/inspections/${inspectionId}/evidence`) })
  const selectedTemplate = templates.find((item) => item.id === selectedTemplateId)
  const selectTemplate = (templateId: string) => {
    const template = templates.find((item) => item.id === templateId)
    if (!template) return
    const currentTitle = String(form.getFieldValue('title') || '')
    if (!currentTitle.trim() || currentTitle === automaticTitle) {
      const nextTitle = buildDefaultDocumentTitle(
        inspection,
        documentLabels[template.document_type],
      )
      form.setFieldValue('title', nextTitle)
      setAutomaticTitle(nextTitle)
    }
  }
  const closeCreate = () => {
    setCreateOpen(false)
    setAutomaticTitle(undefined)
    form.resetFields()
  }
  const create = async (values: { template_id: string; title: string; selected_evidence_ids?: string[] }) => {
    const template = templates.find((item) => item.id === values.template_id)
    if (!template?.active_version_id) return
    try {
      const result = await api<DraftDocument>(`/inspections/${inspectionId}/documents`, { method: 'POST', body: JSON.stringify({ template_version_id: template.active_version_id, title: values.title, selected_evidence_ids: values.selected_evidence_ids || [] }) })
      closeCreate(); setEditing(result); queryClient.invalidateQueries({ queryKey: ['documents', inspectionId] })
    } catch (error) { message.error((error as Error).message) }
  }
  const reloadEditing = async (id: string) => { const result = await api<DraftDocument>(`/documents/${id}`); setEditing(result); queryClient.invalidateQueries({ queryKey: ['documents', inspectionId] }) }
  const saveField = async (field: DocumentField, value: unknown) => {
    await api(`/document-fields/${field.id}`, { method: 'PATCH', body: JSON.stringify({ value, row_version: field.row_version }) })
    if (editing) await reloadEditing(editing.id)
    if (field.is_shared) message.success('共享字段已同步到本次检查的其他文书')
  }
  const confirm = async () => { if (!editing) return; await api(`/documents/${editing.id}/confirm`, { method: 'POST' }); await reloadEditing(editing.id); message.success('文书已确认') }
  const proposeRegeneration = async (fieldId: string) => {
    if (!editing) return
    const result = await api<Array<{ field_id: string; current_value: unknown; proposed_value: unknown; locked: boolean }>>(`/documents/${editing.id}/regenerate`, { method: 'POST', body: JSON.stringify({ field_ids: [fieldId] }) })
    setProposals(result)
  }
  const acceptRegeneration = async () => {
    if (!editing) return
    await api(`/documents/${editing.id}/regenerate/accept`, { method: 'POST', body: JSON.stringify({ proposals }) })
    setProposals([])
    await reloadEditing(editing.id)
    message.success('已应用重新生成的内容')
  }
  return <>
    <Flex justify="space-between" align="center" className="section-actions"><Alert showIcon message="三类文书共享本次检查的事实、证据、转写和检查信息；仅保留各文书专属内容。" /><Button type="primary" icon={<PlusOutlined />} onClick={() => { setAutomaticTitle(undefined); setCreateOpen(true) }}>生成文书初稿</Button></Flex>
    <Table rowKey="id" dataSource={data} pagination={false} locale={{ emptyText: '尚未生成文书' }} columns={[
      { title: '文书', dataIndex: 'title' }, { title: '类型', dataIndex: 'document_type', render: (value) => documentLabels[value] },
      { title: '状态', dataIndex: 'status', render: (value) => <Tag color={value === 'confirmed' ? 'green' : 'orange'}>{value === 'confirmed' ? '已确认' : '初稿'}</Tag> },
      { title: '操作', render: (_, row: DraftDocument) => <Space><Button onClick={() => setEditing(row)}>编辑</Button><Button onClick={() => download(`/documents/${row.id}/export`, `${row.title}.docx`)}>导出 Word</Button></Space> },
    ]} />
    <Modal title="生成文书初稿" open={createOpen} onCancel={closeCreate} onOk={() => form.submit()}><Form form={form} layout="vertical" onFinish={create}><Form.Item name="template_id" label="已确认映射的模板" rules={[{ required: true }]}><Select onChange={selectTemplate} options={templates.map((item) => ({ value: item.id, label: `${item.name} · ${documentLabels[item.document_type]}` }))} /></Form.Item><Form.Item name="title" label="文书名称" rules={[{ required: true }]}><Input /></Form.Item>{selectedTemplate?.document_type === 'photo_document' && <Form.Item name="selected_evidence_ids" label="选择照片或关键画面" rules={[{ required: true, message: '至少选择一张照片' }]}><Checkbox.Group options={evidence.filter((item) => item.type === 'image').map((item) => ({ label: item.label, value: item.id }))} /></Form.Item>}</Form></Modal>
    <Drawer title={editing?.title} open={!!editing} onClose={() => setEditing(undefined)} width={680} extra={<Space><Button onClick={() => editing && download(`/documents/${editing.id}/export`, `${editing.title}.docx`)}>导出 Word</Button><Button type="primary" onClick={confirm}>确认文书</Button></Space>}>
      {editing && <><Alert showIcon message={editing.status === 'confirmed' ? '此版本已经人工确认；再次编辑会回到初稿状态。共享字段修改后会同步到本次检查的其他文书。' : '共享字段只需维护一次；照片逐帧说明、询问问答等文书专属内容仍分别编辑。'} /><List dataSource={editing.fields} renderItem={(field) => <List.Item><DocumentFieldEditor field={field} onSave={(value) => saveField(field, value)} onRegenerate={() => proposeRegeneration(field.id)} /></List.Item>} /></>}
    </Drawer>
    <Modal title="确认局部重新生成" open={!!proposals.length} onCancel={() => setProposals([])} onOk={acceptRegeneration} okText="接受替换"><Alert showIcon type={proposals[0]?.locked ? 'warning' : 'info'} message={proposals[0]?.locked ? '此字段包含人工修改，只有点击“接受替换”才会覆盖。' : '请核对新旧内容差异。'} /><Descriptions column={1} items={[{ key: 'current', label: '当前内容', children: String(proposals[0]?.current_value ?? '') }, { key: 'proposed', label: '建议内容', children: String(proposals[0]?.proposed_value ?? '') }]} /></Modal>
  </>
}

function KnowledgePage({ admin }: { admin: boolean }) {
  const queryClient = useQueryClient()
  const { message } = AntApp.useApp()
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<any>()
  const [uploading, setUploading] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [knowledgeFile, setKnowledgeFile] = useState<File>()
  const [knowledgeForm] = Form.useForm()
  const { data = [] } = useQuery({ queryKey: ['knowledge'], queryFn: () => api<KnowledgeDocument[]>('/knowledge') })
  const search = async () => { if (query.trim()) setResult(await api('/knowledge/search', { method: 'POST', body: JSON.stringify({ query, limit: 8 }) })) }
  const remove = async (document: KnowledgeDocument) => {
    Modal.confirm({
      title: `删除“${document.title}”？`,
      content: '删除后将立即从后续检索中移除，历史引用只保留快照。',
      okButtonProps: { danger: true },
      onOk: async () => {
        await api(`/knowledge/${document.id}`, { method: 'DELETE' })
        await queryClient.invalidateQueries({ queryKey: ['knowledge'] })
        message.success('知识文档已删除')
      },
    })
  }
  const upload = async (values: { title: string; category: string }) => {
    if (!knowledgeFile) return
    setUploading(true)
    const body = new FormData(); body.append('title', values.title); body.append('category', values.category); body.append('file', knowledgeFile)
    try { await api('/knowledge', { method: 'POST', body }); message.success('知识文档已上传'); queryClient.invalidateQueries({ queryKey: ['knowledge'] }); setUploadOpen(false); setKnowledgeFile(undefined); knowledgeForm.resetFields() }
    catch (error) { message.error((error as Error).message) } finally { setUploading(false) }
  }
  return <>
    <PageHeading title="法规与业务知识" description="仅检索有权限用户上传的材料；每条回答都保留原文来源" extra={admin ? <Button onClick={() => setUploadOpen(true)} icon={<CloudUploadOutlined />}>上传知识文档</Button> : undefined} />
    <Card className="search-card"><Input.Search size="large" value={query} onChange={(e) => setQuery(e.target.value)} onSearch={search} enterButton="查询" placeholder="输入现场事实、法规名称或业务问题" />{result && <div className="answer"><Paragraph>{result.answer}</Paragraph>{result.applicability && <Alert showIcon type="warning" message={result.applicability} />}<List header="引用原文" dataSource={result.citations} renderItem={(item: any) => <List.Item actions={[<a key="source" href={`/api/v1/knowledge/${item.document_id}/source`} target="_blank">打开原文</a>]}><List.Item.Meta title={`${item.title}${item.page_number ? ` · 第 ${item.page_number} 页` : ''}`} description={item.quote} /></List.Item>} /></div>}</Card>
    <Table rowKey="id" dataSource={data} pagination={false} columns={[{ title: '知识文档', dataIndex: 'title' }, { title: '类别', dataIndex: 'category' }, { title: '处理步骤', render: (_, row: KnowledgeDocument) => <Space>{row.steps?.map((step) => <Tag key={step.id} color={step.status === 'succeeded' ? 'green' : step.status === 'failed' ? 'red' : 'blue'}>{step.kind}: {step.status}</Tag>)}</Space> }, { title: '操作', render: (_, row: KnowledgeDocument) => <Space><Button href={`/api/v1/knowledge/${row.id}/source`} target="_blank">打开原文</Button>{admin && <Button danger onClick={() => remove(row)}>删除</Button>}</Space> }]} />
    <Modal title="上传知识文档" open={uploadOpen} onCancel={() => setUploadOpen(false)} onOk={() => knowledgeForm.submit()} confirmLoading={uploading}><Form form={knowledgeForm} layout="vertical" onFinish={upload}><Form.Item name="title" label="文档标题" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="category" label="材料类别" rules={[{ required: true }]}><Select options={[['law', '法律法规'], ['standard', '标准规范'], ['local_rule', '地方规定'], ['internal_policy', '内部制度'], ['inspection_guide', '检查规范'], ['other', '其他参考材料']].map(([value, label]) => ({ value, label }))} /></Form.Item><Form.Item label="文件" required><Upload accept=".pdf,.docx,.txt" maxCount={1} beforeUpload={(value) => { setKnowledgeFile(value); knowledgeForm.setFieldValue('title', value.name.replace(/\.[^.]+$/, '')); return false }}><Button>选择 PDF、DOCX 或 TXT</Button></Upload></Form.Item></Form></Modal>
  </>
}

function TemplatesPage({ admin }: { admin: boolean }) {
  const queryClient = useQueryClient()
  const { message } = AntApp.useApp()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [mapping, setMapping] = useState<{ template: Template; version: TemplateVersion }>()
  const [file, setFile] = useState<File>()
  const [form] = Form.useForm()
  const { data = [] } = useQuery({ queryKey: ['templates'], queryFn: () => api<Template[]>('/templates') })
  const openMapping = async (template: Template) => {
    if (!template.active_version_id) return
    const version = await api<TemplateVersion>(`/templates/${template.id}/versions/${template.active_version_id}`)
    setMapping({ template, version })
  }
  const upload = async (values: { name: string; document_type: string }) => {
    if (!file) return
    const body = new FormData(); body.append('name', values.name); body.append('document_type', values.document_type); body.append('file', file)
    try { await api('/templates', { method: 'POST', body }); setUploadOpen(false); setFile(undefined); form.resetFields(); queryClient.invalidateQueries({ queryKey: ['templates'] }); message.success('模板已上传并开始识别') } catch (error) { message.error((error as Error).message) }
  }
  const updateRegion = async (region: TemplateRegion, changes: Partial<TemplateRegion>) => {
    const updated = await api<TemplateRegion>(`/templates/regions/${region.id}`, { method: 'PATCH', body: JSON.stringify({ ...changes, row_version: region.row_version }) })
    if (mapping) setMapping({ ...mapping, version: { ...mapping.version, regions: mapping.version.regions.map((item) => item.id === updated.id ? updated : item) } })
  }
  const confirm = async () => {
    if (!mapping) return
    try { const version = await api<TemplateVersion>(`/templates/${mapping.template.id}/versions/${mapping.version.id}/confirm`, { method: 'POST' }); setMapping({ ...mapping, version }); message.success('模板映射已确认') } catch (error) { message.error((error as Error).message) }
  }
  return <>
    <PageHeading title="Word 模板" description="直接上传现有 Word 文书，确认系统识别出的填写区域" extra={admin ? <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setUploadOpen(true)}>上传模板</Button> : undefined} />
    <Table rowKey="id" dataSource={data} pagination={false} columns={[{ title: '模板', dataIndex: 'name' }, { title: '文书类型', dataIndex: 'document_type', render: (value) => documentLabels[value] }, { title: '上传时间', dataIndex: 'created_at', render: (value) => new Date(value).toLocaleString() }, { title: '操作', render: (_, row: Template) => <Button onClick={() => openMapping(row)}>查看字段映射</Button> }]} />
    <Modal title="上传普通 Word 模板" open={uploadOpen} onCancel={() => setUploadOpen(false)} onOk={() => form.submit()}><Form form={form} layout="vertical" onFinish={upload}><Form.Item name="name" label="模板名称" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="document_type" label="文书类型" rules={[{ required: true }]}><Select options={Object.entries(documentLabels).map(([value, label]) => ({ value, label }))} /></Form.Item><Form.Item label="Word 文件" required><Upload beforeUpload={(value) => { setFile(value); return false }} maxCount={1} accept=".doc,.docx"><Button>选择文件</Button></Upload></Form.Item></Form></Modal>
    <Drawer title={mapping ? `${mapping.template.name} · 字段映射` : ''} open={!!mapping} onClose={() => setMapping(undefined)} width={760} extra={mapping && <Space><Button href={`/api/v1/templates/${mapping.template.id}/versions/${mapping.version.id}/preview`} target="_blank">预览原版式</Button>{admin && <Button type="primary" onClick={confirm}>确认整份映射</Button>}</Space>}>
      {mapping && <><Alert showIcon type={mapping.version.status === 'failed' ? 'error' : 'info'} message={mapping.version.status === 'succeeded' ? `识别出 ${mapping.version.regions.length} 个填写区域，请逐项核对后确认。` : mapping.version.error_message || '模板正在识别，请稍后重新打开。'} /><List dataSource={mapping.version.regions} locale={{ emptyText: '暂无识别结果' }} renderItem={(region) => <List.Item><Card size="small" style={{ width: '100%' }}><Space direction="vertical" style={{ width: '100%' }}><Flex justify="space-between"><Input value={region.label} disabled={!admin} onChange={(e) => setMapping({ ...mapping, version: { ...mapping.version, regions: mapping.version.regions.map((item) => item.id === region.id ? { ...item, label: e.target.value } : item) } })} onBlur={(e) => updateRegion(region, { label: e.target.value })} /><Checkbox checked={region.confirmed} disabled={!admin} onChange={(e) => updateRegion(region, { confirmed: e.target.checked })}>确认</Checkbox></Flex><Input addonBefore="业务来源" value={region.source_path} disabled={!admin} onChange={(e) => setMapping({ ...mapping, version: { ...mapping.version, regions: mapping.version.regions.map((item) => item.id === region.id ? { ...item, source_path: e.target.value } : item) } })} onBlur={(e) => updateRegion(region, { source_path: e.target.value })} /><Text type="secondary">定位：{region.path} · 识别置信度 {Math.round(region.confidence * 100)}%</Text></Space></Card></List.Item>} /></>}
    </Drawer>
  </>
}

function AssistantDrawer({ open, onClose, inspectionId }: { open: boolean; onClose: () => void; inspectionId?: string }) {
  const navigate = useNavigate()
  const location = useLocation()
  const [message, setMessage] = useState('')
  const [conversationId, setConversationId] = useState<string>()
  const [messages, setMessages] = useState<Array<{ role: string; content: string; result?: AssistantResult }>>([])
  const [loading, setLoading] = useState(false)
  useEffect(() => { setConversationId(undefined); setMessages([]) }, [inspectionId])
  const ask = async () => {
    if (!message.trim()) return
    const question = message; setMessage(''); setMessages((items) => [...items, { role: 'user', content: question }, { role: 'assistant', content: '' }]); setLoading(true)
    try {
      const params = new URLSearchParams(location.search)
      const result = await streamAssistant({ message: question, conversation_id: conversationId, context: { page_type: location.pathname, inspection_id: inspectionId || null, document_id: params.get('document'), fact_id: params.get('fact'), material_id: params.get('material') } }, (token) => setMessages((items) => items.map((item, index) => index === items.length - 1 ? { ...item, content: item.content + token } : item))) as AssistantResult
      setConversationId(result.conversation_id); setMessages((items) => items.map((item, index) => index === items.length - 1 ? { ...item, content: result.content, result } : item))
    } catch (error) { setMessages((items) => items.map((item, index) => index === items.length - 1 ? { role: 'assistant', content: (error as Error).message } : item)) } finally { setLoading(false) }
  }
  const act = (action: AssistantResult['actions'][number]) => {
    if (action.type === 'open_image') window.open(`/api/v1/evidence/${action.params.evidence_id}/content`, '_blank')
    else if (action.type === 'open_knowledge') navigate(`/knowledge?document=${action.params.document_id}&anchor=${action.params.anchor}`)
    else if (action.type === 'open_fact' && inspectionId) navigate(`/inspections/${inspectionId}?tab=facts&fact=${action.params.fact_id}`)
    else if (action.type === 'open_document' && inspectionId) navigate(`/inspections/${inspectionId}?tab=documents&document=${action.params.document_id}`)
    else if (action.type === 'seek_media' && inspectionId) navigate(`/inspections/${inspectionId}?tab=materials&material=${action.params.material_id}&start=${action.params.start_ms || 0}`)
  }
  return <Drawer title="当前页面 AI 助手" open={open} onClose={onClose} width={440} className="assistant-drawer"><Alert showIcon message={inspectionId ? '助手只会检索当前账号有权访问的检查与知识材料。' : '进入某次检查后，助手可结合该检查上下文回答。'} /><div className="chat-list">{messages.map((item, index) => <div key={index} className={`chat-bubble ${item.role}`}><Paragraph>{item.content}</Paragraph>{item.result?.actions.map((action, actionIndex) => <Button block size="small" key={actionIndex} onClick={() => act(action)}>{action.label}</Button>)}</div>)}{loading && <div className="chat-bubble assistant"><Spin size="small" /> 正在核对依据…</div>}</div><Flex gap={8}><Input.TextArea value={message} onChange={(e) => setMessage(e.target.value)} onPressEnter={(e) => { if (!e.shiftKey) { e.preventDefault(); ask() } }} placeholder="例如：这条事实的依据是什么？" autoSize={{ minRows: 2, maxRows: 5 }} /><Button type="primary" onClick={ask} loading={loading}>发送</Button></Flex></Drawer>
}

export default function App() {
  const queryClient = useQueryClient()
  const { data: user, isLoading, error } = useQuery({ queryKey: ['me'], queryFn: () => api<User>('/auth/me'), retry: false })
  if (isLoading) return <Spin fullscreen />
  if (!user || error instanceof ApiError && error.status === 401) return <Login onLoggedIn={() => queryClient.invalidateQueries({ queryKey: ['me'] })} />
  return <Shell user={user} />
}
