import { describe, expect, it } from 'vitest'
import { buildDefaultDocumentTitle } from './documentNaming'

describe('buildDefaultDocumentTitle', () => {
  it('uses the inspected unit when it is available', () => {
    expect(
      buildDefaultDocumentTitle(
        { title: '八月检查', unit_name: '某商业综合体' },
        '照片类文书',
      ),
    ).toBe('某商业综合体-照片类文书')
  })

  it('falls back to the inspection title', () => {
    expect(buildDefaultDocumentTitle({ title: '八月检查' }, '询问笔录')).toBe(
      '八月检查-询问笔录',
    )
  })
})
