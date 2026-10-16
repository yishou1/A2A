import { describe, expect, it } from 'vitest'
import {
  LOCAL_IMAGE_MAX_BYTES,
  formatFileSize,
  injectLocalImage,
  schemaSupportsLocalImage,
  validateLocalImage,
} from './localImageUpload'

describe('local image upload helpers', () => {
  it('validates image type and size', () => {
    expect(validateLocalImage({ name: 'target.png', type: 'image/png', size: 1024 })).toBeUndefined()
    expect(validateLocalImage({ name: 'target.JPG', type: '', size: 1024 })).toBeUndefined()
    expect(validateLocalImage({ name: 'target.gif', type: 'image/gif', size: 1024 })).toContain('JPG')
    expect(validateLocalImage({ name: 'target.jpg', type: 'image/jpeg', size: LOCAL_IMAGE_MAX_BYTES + 1 })).toContain('10 MB')
  })

  it('replaces the first frame source while preserving frame metadata', () => {
    const result = injectLocalImage({
      frames: [{ sensor_id: 'EO-01', modality: 'eo_ir', payload: { image_uri: 'old.jpg', quality: 0.9 } }],
      mission_id: 'mission-1',
    }, 'encoded-image')

    expect(result).toEqual({
      frames: [{ sensor_id: 'EO-01', modality: 'eo_ir', payload: { image_base64: 'encoded-image', quality: 0.9 } }],
      mission_id: 'mission-1',
    })
  })

  it('creates a valid first frame when the input has no frame', () => {
    expect(injectLocalImage({}, 'encoded-image')).toEqual({
      frames: [{ sensor_id: 'LOCAL-UPLOAD-1', modality: 'eo_ir', payload: { image_base64: 'encoded-image' } }],
    })
  })

  it('detects image_base64 support in a nested schema', () => {
    expect(schemaSupportsLocalImage({ items: { properties: { payload: { properties: { image_base64: { type: 'string' } } } } } })).toBe(true)
    expect(schemaSupportsLocalImage({ properties: { text: { type: 'string' } } })).toBe(false)
  })

  it('formats file sizes for the upload summary', () => {
    expect(formatFileSize(1536)).toBe('1.5 KB')
    expect(formatFileSize(2 * 1024 * 1024)).toBe('2.0 MB')
  })
})
