import type { components } from './generated/api'

type Schemas = components['schemas']

export type Role = Schemas['UserRole']
export type DocumentType = Schemas['DocumentType']
export type User = Schemas['UserOut']
export type Inspection = Schemas['InspectionOut']
export type ProcessingStep = Schemas['ProcessingStepOut']

export type Material = Omit<Schemas['MaterialOut'], 'steps'> & {
  steps: ProcessingStep[]
}

export type TranscriptSegment = Omit<Schemas['TranscriptOut'], 'speaker_identity'> & {
  speaker_identity?: string
}

export type Evidence = Omit<
  Schemas['EvidenceOut'],
  'material_id' | 'transcript_segment_id' | 'start_ms' | 'end_ms' | 'object_key' | 'quote'
> & {
  material_id?: string
  transcript_segment_id?: string
  start_ms?: number
  end_ms?: number
  object_key?: string
  quote?: string
}

export type Fact = Omit<Schemas['FactOut'], 'evidence'> & {
  evidence: Evidence[]
}

export type KnowledgeDocument = Omit<Schemas['KnowledgeOut'], 'steps'> & {
  steps: ProcessingStep[]
}

export type TemplateRegion = Omit<Schemas['TemplateRegionOut'], 'source_path'> & {
  source_path?: string
}

export type Template = Omit<Schemas['TemplateOut'], 'active_version_id'> & {
  active_version_id?: string
}

export type TemplateVersion = Omit<Schemas['TemplateVersionOut'], 'regions'> & {
  regions: TemplateRegion[]
}

export type DocumentField = Schemas['DocumentFieldOut'] & {
  label?: string
  field_type?: string
}

export type DraftDocument = Omit<Schemas['DocumentOut'], 'fields'> & {
  fields: DocumentField[]
}

export type AssistantResult = Schemas['AssistantOut']
