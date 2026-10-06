import { createError, getRequestHeader, type H3Event } from 'h3'
import type { PhotoInput } from './whitelist-images'
import { WhitelistError } from './whitelist-validation'

export const MAX_MULTIPART_BYTES = 55 * 1024 * 1024
const MAX_JSON_BYTES = 16 * 1024

function httpError(error: WhitelistError) {
  const names: Record<number, string> = {
    400: 'Bad Request', 403: 'Forbidden', 404: 'Not Found', 408: 'Request Timeout',
    409: 'Conflict', 413: 'Payload Too Large', 415: 'Unsupported Media Type', 503: 'Service Unavailable',
  }
  return createError({
    statusCode: error.statusCode,
    statusMessage: names[error.statusCode] || 'Request Failed',
    message: error.message,
    data: { message: error.message },
  })
}

export async function whitelistRequest<T>(operation: () => T | Promise<T>): Promise<T> {
  try {
    return await operation()
  }
  catch (error) {
    if (error instanceof WhitelistError) throw httpError(error)
    throw httpError(new WhitelistError(503, 'Не удалось сохранить изменения. Попробуйте ещё раз.'))
  }
}

/** Protect the localhost application against cross-site requests and DNS rebinding. */
export function assertWhitelistOrigin(event: H3Event): void {
  const host = getRequestHeader(event, 'host') || ''
  let requestUrl: URL
  try {
    requestUrl = new URL(`http://${host}`)
  }
  catch {
    throw httpError(new WhitelistError(403, 'Недопустимый адрес запроса.'))
  }
  if (!['localhost', '127.0.0.1', '[::1]'].includes(requestUrl.hostname)) {
    throw httpError(new WhitelistError(403, 'Список доступен только на этом компьютере.'))
  }
  if (!['POST', 'PATCH', 'DELETE', 'PUT'].includes(event.method)) return
  if (getRequestHeader(event, 'sec-fetch-site') === 'cross-site') {
    throw httpError(new WhitelistError(403, 'Откройте приложение на этом компьютере.'))
  }
  const origin = getRequestHeader(event, 'origin')
  if (origin) {
    const socket = event.node.req.socket as typeof event.node.req.socket & { encrypted?: boolean }
    const protocol = socket.encrypted ? 'https:' : 'http:'
    let allowed = false
    try {
      const parsed = new URL(origin)
      allowed = parsed.origin === `${protocol}//${requestUrl.host}` && origin === parsed.origin
    }
    catch { /* Reject malformed and opaque origins. */ }
    if (!allowed) throw httpError(new WhitelistError(403, 'Запрос с другого сайта отклонён.'))
  }
}

/** Limit bytes while receiving them, before multipart/JSON decoding allocates memory. */
export async function readLimitedBody(event: H3Event, limit: number): Promise<Buffer> {
  const lengthHeader = getRequestHeader(event, 'content-length')
  if (lengthHeader && (!/^\d+$/u.test(lengthHeader) || Number(lengthHeader) > limit)) {
    throw new WhitelistError(413, 'Файлы слишком большие. Уменьшите размер загрузки.')
  }
  return await new Promise<Buffer>((resolve, reject) => {
    const request = event.node.req
    const chunks: Buffer[] = []
    let bytes = 0
    let finished = false
    const clean = () => {
      clearTimeout(timer)
      request.off('data', onData)
      request.off('end', onEnd)
      request.off('error', onError)
      request.off('aborted', onAborted)
    }
    const fail = (error: WhitelistError) => {
      if (finished) return
      finished = true
      clean()
      chunks.length = 0
      request.pause()
      // Let h3 send the error before closing a request that is still being uploaded.
      event.node.res.setHeader('Connection', 'close')
      event.node.res.once('finish', () => request.destroy())
      reject(error)
    }
    const onData = (value: Buffer | string) => {
      const chunk = Buffer.isBuffer(value) ? value : Buffer.from(value)
      bytes += chunk.length
      if (bytes > limit) {
        fail(new WhitelistError(413, 'Файлы слишком большие. Уменьшите размер загрузки.'))
        return
      }
      chunks.push(chunk)
    }
    const onEnd = () => {
      if (finished) return
      finished = true
      clean()
      resolve(Buffer.concat(chunks, bytes))
    }
    const onError = () => fail(new WhitelistError(400, 'Загрузка прервана. Попробуйте ещё раз.'))
    const onAborted = () => fail(new WhitelistError(400, 'Загрузка прервана. Попробуйте ещё раз.'))
    const timer = setTimeout(() => fail(new WhitelistError(408, 'Загрузка заняла слишком много времени.')), 60_000)
    timer.unref()
    request.on('data', onData)
    request.once('end', onEnd)
    request.once('error', onError)
    request.once('aborted', onAborted)
    if (request.readableEnded) onEnd()
  })
}

export async function readWhitelistJson(event: H3Event): Promise<unknown> {
  const contentType = getRequestHeader(event, 'content-type') || ''
  if (!/^application\/json(?:\s*;|$)/iu.test(contentType)) {
    throw new WhitelistError(415, 'Формат запроса не поддерживается.')
  }
  const body = await readLimitedBody(event, MAX_JSON_BYTES)
  try {
    return JSON.parse(body.toString('utf8')) as unknown
  }
  catch {
    throw new WhitelistError(400, 'Проверьте данные формы.')
  }
}

export async function readWhitelistPhotos(event: H3Event, withFields: boolean): Promise<{ label?: string, notes?: string, photos: PhotoInput[] }> {
  const contentType = getRequestHeader(event, 'content-type') || ''
  if (!/^multipart\/form-data(?:\s*;|$)/iu.test(contentType)) {
    throw new WhitelistError(415, 'Отправьте фотографии через форму загрузки.')
  }
  const body = await readLimitedBody(event, MAX_MULTIPART_BYTES)
  let form: FormData
  try {
    form = await new Response(new Uint8Array(body), { headers: { 'Content-Type': contentType } }).formData()
  }
  catch {
    throw new WhitelistError(400, 'Не удалось прочитать фотографии. Выберите файлы ещё раз.')
  }
  const allowed = withFields ? ['label', 'notes', 'photos'] : ['photos']
  for (const key of form.keys()) {
    if (!allowed.includes(key)) throw new WhitelistError(400, 'В форме есть неподдерживаемые поля.')
  }
  const fields: { label?: string, notes?: string } = {}
  if (withFields) {
    for (const key of ['label', 'notes'] as const) {
      const values = form.getAll(key)
      if (values.length > 1 || (values.length === 1 && typeof values[0] !== 'string')) {
        throw new WhitelistError(400, 'Проверьте данные формы.')
      }
      if (values.length) fields[key] = values[0] as string
    }
  }
  const files = form.getAll('photos')
  if (files.length < 1 || files.length > 5 || files.some(file => typeof file === 'string')) {
    throw new WhitelistError(400, 'Добавьте от 1 до 5 фотографий.')
  }
  const photos: PhotoInput[] = []
  for (const file of files) {
    if (typeof file === 'string') continue
    photos.push({ data: Buffer.from(await file.arrayBuffer()), type: file.type })
  }
  return { ...fields, photos }
}
