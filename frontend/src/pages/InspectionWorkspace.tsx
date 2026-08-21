import { CheckOutlined, CloudUploadOutlined, DeleteOutlined, DownloadOutlined, EditOutlined, FileAddOutlined, LinkOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, StopOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Card, Checkbox, Col, Collapse, Descriptions, Empty, Form, Image, Input, List, Modal, Popconfirm, Radio, Row, Select, Space, Spin, Tabs, Tag, Timeline, Typography, Upload, type FormInstance } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, type Asset, type Draft, type Evidence, type Fact, type Inspection, type Template } from '../api'

type Analysis = { id: string; fact_id?: string; question: string; characterization: string; reasoning: string; citations: { id: string; document_id: string; excerpt: string; locator?: string; document_title?: string; material_type?: string }[]; created_at: string }

const statusColor: Record<string, string> = { candidate: 'gold', confirmed: 'green', rejected: 'default', pending: 'blue', processing: 'blue', completed: 'green', failed: 'red', draft: 'gold' }
const kindLabel: Record<string, string> = { observation: '现场观察', basic_info: '基础信息', issue: '问题', statement: '当事人陈述', qa: '询问问答' }

export default function InspectionWorkspace() {
  const { inspectionId = '' } = useParams()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const videoRef = useRef<HTMLVideoElement>(null)
  const [activeTab, setActiveTab] = useState('materials')
  const [selectedVideoId, setSelectedVideoId] = useState<string>()
  const [pendingSeekMs, setPendingSeekMs] = useState<number>()
  const [factOpen, setFactOpen] = useState(false)
  const [editFact, setEditFact] = useState<Fact>()
  const [factForm] = Form.useForm()
  const [queryForm] = Form.useForm()
  const [draftOpen, setDraftOpen] = useState(false)
  const [templateId, setTemplateId] = useState<string>()
  const [infoOpen, setInfoOpen] = useState(false)
  const [infoForm] = Form.useForm()

  const inspection = useQuery({ queryKey: ['inspection', inspectionId], queryFn: () => api<Inspection>(`/inspections/${inspectionId}`) })
  const assets = useQuery({ queryKey: ['assets', inspectionId], queryFn: () => api<Asset[]>(`/inspections/${inspectionId}/assets`), refetchInterval: query => query.state.data?.some(item => ['pending', 'processing'].includes(item.processing_status)) ? 2500 : false })
  const evidence = useQuery({ queryKey: ['evidence', inspectionId], queryFn: () => api<Evidence[]>(`/inspections/${inspectionId}/evidence`) })
  const facts = useQuery({ queryKey: ['facts', inspectionId], queryFn: () => api<Fact[]>(`/inspections/${inspectionId}/facts`) })
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => api<Template[]>('/templates') })
  const drafts = useQuery({ queryKey: ['drafts', inspectionId], queryFn: () => api<Draft[]>(`/inspections/${inspectionId}/documents`) })
  const analyses = useQuery({ queryKey: ['analyses', inspectionId], queryFn: () => api<Analysis[]>(`/inspections/${inspectionId}/legal-analyses`) })
  const videos = (assets.data ?? []).filter(item => item.kind === 'video')
  const selectedVideo = videos.find(item => item.id === selectedVideoId)
    ?? [...videos].reverse().find(item => item.processing_status === 'completed')
    ?? videos.at(-1)

  useEffect(() => {
    if (activeTab !== 'materials' || pendingSeekMs == null || !selectedVideo) return
    const video = videoRef.current
    if (!video) return
    const seek = () => {
      video.currentTime = Math.min(pendingSeekMs / 1000, Number.isFinite(video.duration) ? video.duration : pendingSeekMs / 1000)
      video.pause()
      video.scrollIntoView({ behavior: 'smooth', block: 'center' })
      setPendingSeekMs(undefined)
    }
    if (video.readyState >= HTMLMediaElement.HAVE_METADATA) seek()
    else video.addEventListener('loadedmetadata', seek, { once: true })
    return () => video.removeEventListener('loadedmetadata', seek)
  }, [activeTab, pendingSeekMs, selectedVideo])

  const refreshWorkspace = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['assets', inspectionId] }),
      queryClient.invalidateQueries({ queryKey: ['evidence', inspectionId] }),
      queryClient.invalidateQueries({ queryKey: ['facts', inspectionId] }),
      queryClient.invalidateQueries({ queryKey: ['drafts', inspectionId] }),
    ])
  }
  const jumpToEvidence = (item: Evidence) => {
    if (item.start_ms != null) {
      setSelectedVideoId(item.asset_id)
      setPendingSeekMs(item.start_ms)
      setActiveTab('materials')
    }
  }

  if (inspection.isLoading) return <div className="center"><Spin /></div>
  if (!inspection.data) return <Empty description="检查不存在" />
  return <div className="page inspection-page">
    <div className="page-heading"><div><Typography.Title level={2}>{inspection.data.title}</Typography.Title><Typography.Text type="secondary">创建于 {dayjs(inspection.data.created_at).format('YYYY-MM-DD HH:mm')}</Typography.Text></div><Space><Tag color="red">{inspection.data.status}</Tag><Button icon={<EditOutlined />} onClick={() => { infoForm.setFieldsValue({ title: inspection.data?.title, ...inspection.data?.extra_fields }); setInfoOpen(true) }}>编辑基础信息</Button></Space></div>
    <Descriptions bordered size="small" column={{ xs: 1, md: 3 }} items={[
      { key: 'organization', label: '被检查单位', children: String(inspection.data.extra_fields.organization || '待补充') },
      { key: 'address', label: '地址', children: String(inspection.data.extra_fields.address || '待补充') },
      { key: 'inspectors', label: '检查人员', children: String(inspection.data.extra_fields.inspectors || '待补充') },
    ]} />
    <Modal title="编辑检查基础信息" open={infoOpen} onCancel={() => setInfoOpen(false)} onOk={() => infoForm.submit()} destroyOnHidden>
      <Form form={infoForm} layout="vertical" onFinish={async values => {
        const { title, ...extra_fields } = values
        try { await api(`/inspections/${inspectionId}`, { method: 'PATCH', body: JSON.stringify({ title, extra_fields }) }); setInfoOpen(false); await queryClient.invalidateQueries({ queryKey: ['inspection', inspectionId] }); message.success('基础信息已保存') }
        catch (error) { message.error(error instanceof Error ? error.message : '保存失败') }
      }}>
        <Form.Item name="title" label="检查名称" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="organization" label="被检查单位"><Input /></Form.Item>
        <Form.Item name="address" label="地址"><Input /></Form.Item>
        <Form.Item name="inspection_date" label="检查时间"><Input placeholder="例如：2026-08-21 10:00" /></Form.Item>
        <Form.Item name="inspectors" label="检查人员"><Input /></Form.Item>
      </Form>
    </Modal>
    <Tabs className="workspace-tabs" activeKey={activeTab} onChange={setActiveTab} items={[
      { key: 'materials', label: '1. 材料与证据', children: <MaterialsTab assets={assets.data ?? []} evidence={evidence.data ?? []} inspectionId={inspectionId} selectedVideo={selectedVideo} selectedVideoId={selectedVideo?.id} onSelectVideo={setSelectedVideoId} videoRef={videoRef} onRefresh={refreshWorkspace} onJump={jumpToEvidence} /> },
      { key: 'facts', label: `2. 检查事实 (${facts.data?.length ?? 0})`, children: <FactsTab facts={facts.data ?? []} evidence={evidence.data ?? []} onJump={jumpToEvidence} onAdd={() => { setEditFact(undefined); factForm.resetFields(); setFactOpen(true) }} onEdit={fact => { setEditFact(fact); factForm.setFieldsValue({ kind: fact.kind, summary: fact.summary, evidence_ids: fact.evidence_ids }); setFactOpen(true) }} onAction={async (fact, action) => { await api(`/facts/${fact.id}/${action}`, { method: 'POST' }); await refreshWorkspace(); message.success(action === 'confirm' ? '事实已确认' : '事实已排除') }} /> },
      { key: 'knowledge', label: '3. 法规分析', children: <KnowledgeTab facts={facts.data ?? []} analyses={analyses.data ?? []} form={queryForm} onQuery={async values => { try { await api('/knowledge/query', { method: 'POST', body: JSON.stringify({ ...values, inspection_id: inspectionId }) }); queryForm.resetFields(); await queryClient.invalidateQueries({ queryKey: ['analyses', inspectionId] }); message.success('法规分析完成') } catch (error) { message.error(error instanceof Error ? error.message : '查询失败') } }} /> },
      { key: 'documents', label: `4. 文书 (${drafts.data?.length ?? 0})`, children: <DocumentsTab drafts={drafts.data ?? []} templates={templates.data ?? []} evidence={evidence.data ?? []} onCreate={() => setDraftOpen(true)} onRefresh={() => queryClient.invalidateQueries({ queryKey: ['drafts', inspectionId] })} /> },
    ]} />
    <Modal title={editFact ? '修改检查事实' : '人工补充检查事实'} open={factOpen} onCancel={() => setFactOpen(false)} onOk={() => factForm.submit()} destroyOnHidden>
      <Form form={factForm} layout="vertical" initialValues={{ kind: 'observation', evidence_ids: [] }} onFinish={async values => {
        try {
          if (editFact) await api(`/facts/${editFact.id}`, { method: 'PATCH', body: JSON.stringify(values) })
          else await api(`/inspections/${inspectionId}/facts`, { method: 'POST', body: JSON.stringify({ ...values, content: {} }) })
          setFactOpen(false); factForm.resetFields(); await refreshWorkspace(); message.success(editFact ? '事实已修改，相关文书字段已标记' : '事实已补充并确认')
        } catch (error) { message.error(error instanceof Error ? error.message : '保存失败') }
      }}>
        <Form.Item name="kind" label="事实类型"><Select options={Object.entries(kindLabel).map(([value, label]) => ({ value, label }))} /></Form.Item>
        <Form.Item name="summary" label="事实描述" rules={[{ required: true }]}><Input.TextArea rows={5} /></Form.Item>
        <Form.Item name="evidence_ids" label="关联证据（人工事实可不选）"><Select mode="multiple" options={(evidence.data ?? []).map(item => ({ value: item.id, label: `${item.kind} ${item.start_ms != null ? `${(item.start_ms / 1000).toFixed(1)}s` : ''} ${item.excerpt ?? ''}` }))} /></Form.Item>
      </Form>
    </Modal>
    <Modal title="选择文书模板" open={draftOpen} onCancel={() => setDraftOpen(false)} onOk={async () => {
      if (!templateId) return message.warning('请选择模板')
      try { await api(`/inspections/${inspectionId}/documents`, { method: 'POST', body: JSON.stringify({ template_id: templateId }) }); setDraftOpen(false); setTemplateId(undefined); await queryClient.invalidateQueries({ queryKey: ['drafts', inspectionId] }); message.success('文书初稿已生成') }
      catch (error) { message.error(error instanceof Error ? error.message : '生成失败') }
    }}><Select className="full-width" value={templateId} onChange={setTemplateId} placeholder="请选择" options={(templates.data ?? []).map(item => ({ value: item.id, label: `${item.name}${item.builtin ? '（系统样例）' : ''}` }))} /></Modal>
  </div>
}

function assetDuration(item: Asset) {
  const format = item.metadata_json.format
  if (!format || typeof format !== 'object' || !('duration' in format)) return undefined
  const duration = Number(format.duration)
  return Number.isFinite(duration) ? duration : undefined
}

function MaterialsTab({ assets, evidence, inspectionId, selectedVideo, selectedVideoId, onSelectVideo, videoRef, onRefresh, onJump }: { assets: Asset[]; evidence: Evidence[]; inspectionId: string; selectedVideo?: Asset; selectedVideoId?: string; onSelectVideo: (id: string) => void; videoRef: React.RefObject<HTMLVideoElement | null>; onRefresh: () => Promise<void>; onJump: (item: Evidence) => void }) {
  const { message } = App.useApp()
  const [uploading, setUploading] = useState(false)
  const videos = assets.filter(item => item.kind === 'video')
  return <Row gutter={[20, 20]}>
    <Col xs={24} xl={14}><Card title="原始材料" extra={<Upload showUploadList={false} beforeUpload={async file => { setUploading(true); const data = new FormData(); data.append('file', file); try { await api(`/inspections/${inspectionId}/assets`, { method: 'POST', body: data }); await onRefresh(); message.success('材料已上传并进入处理队列') } catch (error) { message.error(error instanceof Error ? error.message : '上传失败') } finally { setUploading(false) }; return false }}><Button loading={uploading} icon={<CloudUploadOutlined />}>上传材料</Button></Upload>}>
      {videos.length > 0 && <Select className="full-width video-selector" value={selectedVideoId} onChange={onSelectVideo} options={videos.map(item => { const duration = assetDuration(item); return { value: item.id, label: `${item.filename}${duration != null ? ` · ${duration.toFixed(1)} 秒` : ''}${item.processing_status !== 'completed' ? ` · ${item.processing_status}` : ''}` } })} />}
      {selectedVideo && <video key={selectedVideo.id} ref={videoRef} className="evidence-video" controls preload="metadata" src={`/api/assets/${selectedVideo.id}/content`} />}
      <List dataSource={assets} locale={{ emptyText: <Empty description="上传视频、音频、照片或文字材料" /> }} renderItem={item => <List.Item actions={[
        ...(item.kind === 'video' ? [<Button key="preview" type={selectedVideoId === item.id ? 'primary' : 'default'} onClick={() => onSelectVideo(item.id)}>预览</Button>] : []),
        ...(item.processing_status === 'failed' ? [<Button key="retry" icon={<ReloadOutlined />} onClick={async () => { await api(`/assets/${item.id}/retry`, { method: 'POST' }); onRefresh() }}>重试</Button>] : []),
        <Popconfirm key="delete" title="删除原始材料" description="同时删除未被引用的派生证据，删除后无法恢复。" okText="删除" cancelText="取消" onConfirm={async () => { try { await api(`/assets/${item.id}`, { method: 'DELETE' }); await onRefresh(); message.success('材料已删除') } catch (error) { message.error(error instanceof Error ? error.message : '删除失败') } }}><Button danger disabled={['pending', 'processing'].includes(item.processing_status)} icon={<DeleteOutlined />}>删除</Button></Popconfirm>,
      ]}><List.Item.Meta title={item.filename} description={<Space direction="vertical" size={2}><Space><Tag color={statusColor[item.processing_status]}>{item.processing_status}</Tag>{assetDuration(item) != null && <Typography.Text type="secondary">{assetDuration(item)?.toFixed(1)} 秒</Typography.Text>}</Space>{item.processing_error && <Typography.Text type="danger">{item.processing_error}</Typography.Text>}{typeof item.metadata_json.warning === 'string' && <Typography.Text type="warning">{item.metadata_json.warning}</Typography.Text>}</Space>} /></List.Item>} />
    </Card></Col>
    <Col xs={24} xl={10}><Card title={`已提取证据（${evidence.length}）`}><List dataSource={evidence} locale={{ emptyText: <Empty description="材料处理完成后显示证据" /> }} renderItem={item => { const visual = item.kind === 'keyframe' || item.kind === 'photo'; return <List.Item onClick={item.start_ms != null ? () => onJump(item) : undefined} className={item.start_ms != null ? 'clickable-list' : undefined} extra={visual ? <Image width={100} onClick={event => event.stopPropagation()} src={`/api/evidence/${item.id}/content`} /> : null}><List.Item.Meta title={<Space><Tag>{item.kind}</Tag>{item.start_ms != null && <span>{(item.start_ms / 1000).toFixed(1)} 秒</span>}</Space>} description={item.excerpt || (visual ? item.kind === 'keyframe' ? '点击图片预览；点击此行定位到视频' : '点击图片预览' : '')} /></List.Item> }} /></Card></Col>
  </Row>
}

function FactsTab({ facts, evidence, onJump, onAdd, onEdit, onAction }: { facts: Fact[]; evidence: Evidence[]; onJump: (item: Evidence) => void; onAdd: () => void; onEdit: (fact: Fact) => void; onAction: (fact: Fact, action: 'confirm' | 'reject') => Promise<void> }) {
  const evidenceMap = useMemo(() => new Map(evidence.map(item => [item.id, item])), [evidence])
  return <Card title="共享检查事实" extra={<Button type="primary" icon={<PlusOutlined />} onClick={onAdd}>人工补充</Button>}><Alert type="info" showIcon message="AI 结果默认是候选事实，确认后才能进入文书；人工补充事实会直接标记为已确认。" /><List className="spaced-list" dataSource={facts} locale={{ emptyText: <Empty description="暂无检查事实" /> }} renderItem={fact => <List.Item actions={[
    <Button icon={<EditOutlined />} onClick={() => onEdit(fact)}>修改</Button>,
    ...(fact.status === 'candidate' ? [<Button type="primary" icon={<CheckOutlined />} onClick={() => onAction(fact, 'confirm')}>确认</Button>, <Button danger icon={<StopOutlined />} onClick={() => onAction(fact, 'reject')}>排除</Button>] : []),
  ]}><List.Item.Meta title={<Space wrap><Tag color={statusColor[fact.status]}>{fact.status === 'candidate' ? '待确认' : '已确认'}</Tag><Tag>{kindLabel[fact.kind] ?? fact.kind}</Tag><span>{fact.summary}</span></Space>} description={<div><Typography.Text type="secondary">{fact.source === 'ai' ? `AI 候选${fact.confidence != null ? ` · 置信度 ${Math.round(fact.confidence * 100)}%` : ''}` : '人工补充'}</Typography.Text><div className="fact-evidence-list">{fact.evidence_ids.map(id => { const item = evidenceMap.get(id); return item && <EvidenceReference key={id} item={item} onJump={onJump} /> })}</div></div>} /></List.Item>} /></Card>
}

function EvidenceReference({ item, onJump }: { item: Evidence; onJump: (item: Evidence) => void }) {
  const visual = item.kind === 'keyframe' || item.kind === 'photo'
  if (!visual) return <Button size="small" icon={<LinkOutlined />} onClick={() => onJump(item)}>{item.kind}{item.start_ms != null ? ` ${(item.start_ms / 1000).toFixed(1)}s` : ''}</Button>
  return <div className="evidence-reference">
    <Image width={112} src={`/api/evidence/${item.id}/content`} />
    <Space direction="vertical" size={2}>
      <Typography.Text>{item.kind === 'keyframe' ? `关键帧 ${(item.start_ms ?? 0) / 1000}s` : '现场照片'}</Typography.Text>
      {item.kind === 'keyframe' && <Button size="small" icon={<LinkOutlined />} onClick={() => onJump(item)}>定位到视频</Button>}
    </Space>
  </div>
}

function KnowledgeTab({ facts, analyses, form, onQuery }: { facts: Fact[]; analyses: Analysis[]; form: FormInstance; onQuery: (values: Record<string, string>) => Promise<void> }) {
  return <Row gutter={[20, 20]}><Col xs={24} lg={9}><Card title="查找相关依据"><Form form={form} layout="vertical" onFinish={onQuery}><Form.Item name="fact_id" label="关联已确认事实（可选）"><Select allowClear options={facts.filter(item => item.status === 'confirmed').map(item => ({ value: item.id, label: item.summary }))} onChange={value => { const fact = facts.find(item => item.id === value); if (fact) form.setFieldValue('question', `${fact.summary}，可能涉及什么消防安全规定？`) }} /></Form.Item><Form.Item name="question" label="现场问题描述" rules={[{ required: true }]}><Input.TextArea rows={6} placeholder="例如：消防控制室无人值班，可能违反什么规定？" /></Form.Item><Button type="primary" block htmlType="submit" icon={<SearchOutlined />}>检索并分析</Button></Form></Card></Col><Col xs={24} lg={15}><Card title="分析记录"><Timeline items={analyses.map(item => ({ color: item.citations.length ? 'red' : 'gray', children: <div><Typography.Title level={5}>{item.characterization}</Typography.Title><Typography.Paragraph>{item.reasoning}</Typography.Paragraph><Collapse size="small" items={item.citations.map(citation => ({ key: citation.id, label: `${citation.document_title ?? '来源文档'} ${citation.locator ?? ''}`, children: <><Typography.Paragraph>{citation.excerpt}</Typography.Paragraph><Button href={`/api/knowledge-documents/${citation.document_id}/content`} target="_blank">打开原始文档</Button></> }))} />{!item.citations.length && <Alert type="warning" showIcon message="未找到足够依据" />}</div> }))} /></Card></Col></Row>
}

function DocumentsTab({ drafts, templates, evidence, onCreate, onRefresh }: { drafts: Draft[]; templates: Template[]; evidence: Evidence[]; onCreate: () => void; onRefresh: () => Promise<unknown> }) {
  const { message } = App.useApp()
  const templateMap = useMemo(() => new Map(templates.map(item => [item.id, item])), [templates])
  const imageEvidence = evidence.filter(item => item.kind === 'keyframe' || item.kind === 'photo')
  const regenerate = async (draft: Draft, fieldCodes: string[]) => {
    await api(`/documents/${draft.id}/regenerate`, { method: 'POST', body: JSON.stringify({ field_codes: fieldCodes }) })
    await onRefresh()
  }
  const keepCurrent = async (draft: Draft, fieldCode: string, value: unknown) => {
    await api(`/documents/${draft.id}/fields/${fieldCode}`, { method: 'PATCH', body: JSON.stringify({ value }) })
    await onRefresh()
    message.success('已保留当前人工内容')
  }
  return <Card title="文书初稿" extra={<Button type="primary" icon={<FileAddOutlined />} onClick={onCreate}>生成初稿</Button>}><Alert type="info" showIcon message="页面只编辑内容字段；正式版式由 Word 模板决定。事实变化后，相关字段会标记为需要更新。" /><div className="draft-list">{drafts.length === 0 && <Empty description="选择模板生成文书初稿" />}{drafts.map(draft => { const template = templateMap.get(draft.template_id); return <Card key={draft.id} type="inner" title={<Space>{template?.name ?? draft.document_type}<Tag color={statusColor[draft.status]}>{draft.status === 'confirmed' ? '已确认' : '草稿'}</Tag></Space>} extra={<Space><Button onClick={async () => { const stale = Object.entries(draft.fields_json).filter(([, state]) => state.stale && !state.edited).map(([code]) => code); if (!stale.length) return message.info(Object.values(draft.fields_json).some(state => state.stale) ? '人工修改字段请逐项选择保留或重新生成' : '没有需要更新的字段'); await regenerate(draft, stale); message.success('未人工修改的过期字段已更新') }} icon={<ReloadOutlined />}>更新未修改字段</Button><Button type="primary" icon={<CheckOutlined />} onClick={async () => { try { await api(`/documents/${draft.id}/confirm`, { method: 'POST' }); await onRefresh(); message.success('文书已确认') } catch (error) { message.error(error instanceof Error ? error.message : '确认失败') } }}>确认</Button><Button disabled={draft.status !== 'confirmed'} icon={<DownloadOutlined />} onClick={async () => { const result = await api<{ download_url: string }>(`/documents/${draft.id}/export`, { method: 'POST' }); window.location.href = result.download_url }}>导出 Word</Button></Space>}>
        {(template?.fields_json ?? []).map(field => { const state = draft.fields_json[field.code]; if (!state) return null; return <div className={`document-field ${state.stale ? 'stale-field' : ''}`} key={field.code}><div className="field-label"><Typography.Text strong>{field.label}</Typography.Text><Space>{state.edited && <Tag>已人工修改</Tag>}{state.stale && <Tag color="red">需要更新</Tag>}{state.stale && state.edited && <Button size="small" onClick={() => keepCurrent(draft, field.code, state.value)}>保留当前内容</Button>}{state.stale && <Button size="small" icon={<ReloadOutlined />} onClick={async () => { await regenerate(draft, [field.code]); message.success(`${field.label}已重新生成`) }}>重新生成此字段</Button>}</Space></div>{field.type === 'image' || field.type === 'photo_list' ? <EvidencePicker evidence={imageEvidence} multiple={field.type === 'photo_list'} value={state.value} onChange={async value => { await api(`/documents/${draft.id}/fields/${field.code}`, { method: 'PATCH', body: JSON.stringify({ value }) }); await onRefresh() }} /> : <Input.TextArea autoSize={{ minRows: field.type === 'multiline' ? 3 : 1, maxRows: 10 }} defaultValue={String(state.value ?? '')} key={`${draft.updated_at}-${field.code}`} onBlur={async event => { if (event.target.value === String(state.value ?? '')) return; await api(`/documents/${draft.id}/fields/${field.code}`, { method: 'PATCH', body: JSON.stringify({ value: event.target.value }) }); await onRefresh(); message.success(`${field.label}已保存`) }} />}</div> })}
      </Card> })}</div></Card>
}

function EvidencePicker({ evidence, multiple, value, onChange }: { evidence: Evidence[]; multiple: boolean; value: unknown; onChange: (value: string | string[]) => Promise<unknown> }) {
  const selected = multiple
    ? new Set(Array.isArray(value) ? value.filter(item => typeof item === 'string') : [])
    : new Set(typeof value === 'string' && value ? [value] : [])
  if (evidence.length === 0) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前检查中没有关键帧或照片" />
  const toggle = (id: string, checked: boolean) => {
    if (!multiple) return void onChange(checked ? id : '')
    const next = Array.from(selected)
    if (checked && !selected.has(id)) next.push(id)
    const index = next.indexOf(id)
    if (!checked && index >= 0) next.splice(index, 1)
    void onChange(next)
  }
  return <div className="evidence-picker">
    {evidence.map(item => <div key={item.id} className={`evidence-option ${selected.has(item.id) ? 'selected' : ''}`}>
      <Image width="100%" height={120} className="evidence-option-image" src={`/api/evidence/${item.id}/content`} />
      <label className="evidence-option-label">
        {multiple
          ? <Checkbox checked={selected.has(item.id)} onChange={event => toggle(item.id, event.target.checked)} />
          : <Radio checked={selected.has(item.id)} onChange={event => toggle(item.id, event.target.checked)} />}
        <span>{item.kind === 'keyframe' ? `关键帧 ${(item.start_ms ?? 0) / 1000}s` : '现场照片'}</span>
      </label>
    </div>)}
  </div>
}
