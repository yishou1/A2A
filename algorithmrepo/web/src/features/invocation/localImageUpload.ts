export const LOCAL_IMAGE_MAX_BYTES = 10 * 1024 * 1024

export const LOCAL_IMAGE_ACCEPT = '.jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp'

const supportedImageTypes = new Set(['image/jpeg', 'image/png', 'image/webp'])

type LocalFile = Pick<File, 'name' | 'size' | 'type'>

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

export function validateLocalImage(file: LocalFile): string | undefined {
  const hasSupportedExtension = /\.(jpe?g|png|webp)$/i.test(file.name)
  if (!supportedImageTypes.has(file.type.toLowerCase()) && !(file.type === '' && hasSupportedExtension)) {
    return '仅支持 JPG、PNG 或 WebP 图片。'
  }
  if (file.size <= 0) return '图片文件为空。'
  if (file.size > LOCAL_IMAGE_MAX_BYTES) return '图片不能超过 10 MB。'
  return undefined
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function schemaSupportsLocalImage(schema: unknown): boolean {
  const seen = new Set<object>()
  const visit = (value: unknown): boolean => {
    if (!isRecord(value) || seen.has(value)) return false
    seen.add(value)
    const properties = value.properties
    if (isRecord(properties) && Object.prototype.hasOwnProperty.call(properties, 'image_base64')) return true
    return Object.values(value).some(visit)
  }
  return visit(schema)
}

export function injectLocalImage(inputs: Record<string, unknown>, base64: string): Record<string, unknown> {
  const existingFrames = Array.isArray(inputs.frames) ? inputs.frames : []
  const firstFrame = isRecord(existingFrames[0]) ? existingFrames[0] : {}
  const oldPayload = isRecord(firstFrame.payload) ? firstFrame.payload : {}
  const remainingPayload = { ...oldPayload }
  delete remainingPayload.image_uri
  delete remainingPayload.attachment_ref
  const nextFrame = {
    ...firstFrame,
    sensor_id: typeof firstFrame.sensor_id === 'string' && firstFrame.sensor_id ? firstFrame.sensor_id : 'LOCAL-UPLOAD-1',
    modality: typeof firstFrame.modality === 'string' && firstFrame.modality ? firstFrame.modality : 'eo_ir',
    payload: { ...remainingPayload, image_base64: base64 },
  }
  return { ...inputs, frames: [nextFrame] }
}

export function readImageAsBase64(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('读取本地图片失败，请重新选择。'))
    reader.onload = () => {
      if (typeof reader.result !== 'string') {
        reject(new Error('读取本地图片失败，请重新选择。'))
        return
      }
      const comma = reader.result.indexOf(',')
      resolve(comma >= 0 ? reader.result.slice(comma + 1) : reader.result)
    }
    reader.readAsDataURL(file)
  })
}
