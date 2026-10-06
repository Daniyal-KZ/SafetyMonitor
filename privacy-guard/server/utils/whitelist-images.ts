import sharp from 'sharp'
import { WhitelistError } from './whitelist-validation'

export const MAX_PHOTOS = 5
export const MAX_PHOTO_BYTES = 10 * 1024 * 1024

export interface PhotoInput {
  data: Buffer
  type: string
}

function imageFormat(data: Buffer): 'jpeg' | 'png' | 'webp' | null {
  if (data.length >= 3 && data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff) return 'jpeg'
  if (data.length >= 8 && data.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) return 'png'
  if (data.length >= 12 && data.toString('ascii', 0, 4) === 'RIFF' && data.toString('ascii', 8, 12) === 'WEBP') return 'webp'
  return null
}

export async function sanitizePhoto(photo: PhotoInput): Promise<Buffer> {
  if (!photo.data.length || photo.data.length > MAX_PHOTO_BYTES) {
    throw new WhitelistError(400, 'Размер каждой фотографии — до 10 МБ.')
  }
  const format = imageFormat(photo.data)
  const expected = { 'image/jpeg': 'jpeg', 'image/png': 'png', 'image/webp': 'webp' }[photo.type]
  if (!format || expected !== format) {
    throw new WhitelistError(400, 'Выберите фотографию JPEG, PNG или WebP.')
  }
  try {
    const image = sharp(photo.data, { limitInputPixels: 20_000_000, failOn: 'warning' })
    const metadata = await image.metadata()
    if (!metadata.width || !metadata.height || (metadata.pages ?? 1) > 1 || metadata.format !== format) {
      throw new Error('Unsupported image')
    }
    // sharp omits EXIF and other metadata unless explicitly asked to keep them.
    return await image.rotate().resize(1600, 1600, { fit: 'inside', withoutEnlargement: true })
      .flatten({ background: '#ffffff' }).jpeg({ quality: 90 }).toBuffer()
  }
  catch {
    throw new WhitelistError(400, 'Фотография повреждена или слишком большая. Максимум — 20 мегапикселей.')
  }
}

export async function sanitizePhotos(photos: PhotoInput[]): Promise<Buffer[]> {
  if (photos.length < 1 || photos.length > MAX_PHOTOS) {
    throw new WhitelistError(400, 'Добавьте от 1 до 5 фотографий.')
  }
  // Decode in sequence to avoid holding several full-resolution images in memory.
  const result: Buffer[] = []
  for (const photo of photos) result.push(await sanitizePhoto(photo))
  return result
}
