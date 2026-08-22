import { describe, expect, it } from 'vitest'
import { ApiError } from './api'

describe('ApiError', () => {
  it('preserves the HTTP status and user-facing message', () => {
    const error = new ApiError(409, '内容已被其他用户修改')
    expect(error.status).toBe(409)
    expect(error.message).toBe('内容已被其他用户修改')
  })
})

