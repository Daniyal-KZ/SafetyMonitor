import { randomUUID } from 'node:crypto'
import { mkdir, readFile, rename, rmdir, unlink, open } from 'node:fs/promises'
import { resolve, join } from 'node:path'
import type { PlateCountry, WhitelistEntry, WhitelistState } from '../../shared/types/whitelist'
import { MAX_PHOTOS, sanitizePhotos, type PhotoInput } from './whitelist-images'
import { WhitelistError, enabledFlag, labelText, normalizePlate, notesText, objectBody, onlyFields, plateCountry, validateId } from './whitelist-validation'

interface StoredEntry extends Omit<WhitelistEntry, 'photos'> {
  photos: string[]
}

interface StoredState {
  version: 1
  enabled: boolean
  entries: StoredEntry[]
}

function emptyState(): StoredState {
  return { version: 1, enabled: false, entries: [] }
}

function validDate(value: unknown): string {
  if (typeof value !== 'string' || !Number.isFinite(Date.parse(value)) || new Date(value).toISOString() !== value) {
    throw new Error('Invalid date')
  }
  return value
}

function parseSavedState(value: unknown): StoredState {
  const body = objectBody(value)
  if (body.version !== 1 || typeof body.enabled !== 'boolean' || !Array.isArray(body.entries) || body.entries.length > 2000) {
    throw new Error('Invalid saved state')
  }
  const ids = new Set<string>()
  const plateKeys = new Set<string>()
  const entries: StoredEntry[] = body.entries.map((value: unknown) => {
    const item = objectBody(value)
    const id = validateId(item.id)
    if (ids.has(id) || (item.kind !== 'person' && item.kind !== 'plate') || !Array.isArray(item.photos)) {
      throw new Error('Invalid saved entry')
    }
    ids.add(id)
    const label = labelText(item.label)
    const notes = notesText(item.notes)
    const photos: string[] = item.photos.map((photo: unknown) => validateId(photo))
    if (new Set(photos).size !== photos.length || label !== item.label || notes !== item.notes) {
      throw new Error('Invalid saved text or photos')
    }
    let plate: string | null = null
    let country: PlateCountry | null = null
    if (item.kind === 'person') {
      if (photos.length < 1 || photos.length > MAX_PHOTOS || item.plate !== null || item.country !== null) {
        throw new Error('Invalid saved person')
      }
    }
    else {
      plate = normalizePlate(item.plate)
      country = plateCountry(item.country)
      const key = `${country}:${plate}`
      if (photos.length || plate !== item.plate || country !== item.country || plateKeys.has(key)) {
        throw new Error('Invalid saved plate')
      }
      plateKeys.add(key)
    }
    return {
      id, kind: item.kind, label, notes, enabled: enabledFlag(item.enabled), photos, plate, country,
      createdAt: validDate(item.createdAt), updatedAt: validDate(item.updatedAt),
    }
  })
  return { version: 1, enabled: body.enabled, entries }
}

function publicState(state: StoredState): WhitelistState {
  return {
    enabled: state.enabled,
    entries: state.entries.map(entry => ({
      ...entry,
      photos: entry.photos.map(id => ({ id, url: `/api/whitelist/${entry.id}/photos/${id}` })),
    })),
  }
}

function isMissing(error: unknown): boolean {
  return !!error && typeof error === 'object' && 'code' in error && error.code === 'ENOENT'
}

/** Single-process local store. All reads and commits share one serialized queue. */
export class WhitelistStore {
  private state: StoredState | undefined
  private queue: Promise<unknown> = Promise.resolve()
  readonly directory: string

  constructor(directory: string) {
    this.directory = resolve(directory)
  }

  private serialized<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.queue.then(operation)
    this.queue = result.catch(() => undefined)
    return result
  }

  private async load(): Promise<StoredState> {
    if (this.state) return this.state
    let contents: string
    try {
      contents = await readFile(join(this.directory, 'whitelist.json'), 'utf8')
    }
    catch (error) {
      if (isMissing(error)) {
        this.state = emptyState()
        return this.state
      }
      throw new WhitelistError(503, 'Не удалось прочитать список. Проверьте доступ к папке данных.')
    }
    try {
      this.state = parseSavedState(JSON.parse(contents))
      return this.state
    }
    catch {
      throw new WhitelistError(503, 'Файл списка повреждён. Данные сохранены без изменений.')
    }
  }

  private async commit(next: StoredState): Promise<void> {
    await mkdir(this.directory, { recursive: true, mode: 0o700 })
    const temporary = join(this.directory, `whitelist-${randomUUID()}.tmp`)
    try {
      const file = await open(temporary, 'wx', 0o600)
      try {
        await file.writeFile(`${JSON.stringify(next, null, 2)}\n`, 'utf8')
        await file.sync()
      }
      finally {
        await file.close()
      }
      await rename(temporary, join(this.directory, 'whitelist.json'))
      this.state = next
    }
    catch (error) {
      await unlink(temporary).catch(() => undefined)
      throw error
    }
  }

  private find(state: StoredState, id: string): StoredEntry {
    const entry = state.entries.find(item => item.id === id)
    if (!entry) throw new WhitelistError(404, 'Запись не найдена.')
    return entry
  }

  private photoPath(entryId: string, photoId: string): string {
    return join(this.directory, 'photos', validateId(entryId), `${validateId(photoId)}.jpg`)
  }

  private async writePhotos(entryId: string, buffers: Buffer[]): Promise<string[]> {
    const ids: string[] = []
    await mkdir(join(this.directory, 'photos', entryId), { recursive: true, mode: 0o700 })
    try {
      for (const data of buffers) {
        const id = randomUUID()
        const file = await open(this.photoPath(entryId, id), 'wx', 0o600)
        ids.push(id)
        try {
          await file.writeFile(data)
          await file.sync()
        }
        finally {
          await file.close()
        }
      }
      return ids
    }
    catch (error) {
      await this.cleanPhotos(entryId, ids)
      throw error
    }
  }

  private async cleanPhotos(entryId: string, ids: string[]): Promise<void> {
    await Promise.allSettled(ids.map(id => unlink(this.photoPath(entryId, id))))
    // Non-recursive removal succeeds only when this generated directory is empty.
    await rmdir(join(this.directory, 'photos', entryId)).catch(() => undefined)
  }

  getState(): Promise<WhitelistState> {
    return this.serialized(async () => publicState(await this.load()))
  }

  async addPerson(input: { label: unknown, notes?: unknown }, photos: PhotoInput[]): Promise<WhitelistState> {
    const label = labelText(input.label)
    const notes = notesText(input.notes)
    return this.serialized(async () => {
      const state = await this.load()
      if (state.entries.length >= 2000) throw new WhitelistError(400, 'В списке уже 2000 записей.')
      const buffers = await sanitizePhotos(photos)
      const id = randomUUID()
      const ids = await this.writePhotos(id, buffers)
      try {
        const now = new Date().toISOString()
        const entry: StoredEntry = {
          id, kind: 'person', label, notes, enabled: true, createdAt: now, updatedAt: now,
          photos: ids, plate: null, country: null,
        }
        const next = { ...state, entries: [entry, ...state.entries] }
        await this.commit(next)
        return publicState(next)
      }
      catch (error) {
        await this.cleanPhotos(id, ids)
        throw error
      }
    })
  }

  addPlate(value: unknown): Promise<WhitelistState> {
    const body = objectBody(value)
    onlyFields(body, ['label', 'notes', 'plate', 'country'])
    const label = labelText(body.label)
    const notes = notesText(body.notes)
    const plate = normalizePlate(body.plate)
    const country = plateCountry(body.country)
    return this.serialized(async () => {
      const state = await this.load()
      if (state.entries.length >= 2000) throw new WhitelistError(400, 'В списке уже 2000 записей.')
      if (state.entries.some(entry => entry.kind === 'plate' && entry.country === country && entry.plate === plate)) {
        throw new WhitelistError(409, 'Этот номер уже добавлен для выбранной страны.')
      }
      const now = new Date().toISOString()
      const entry: StoredEntry = {
        id: randomUUID(), kind: 'plate', label, notes, enabled: true, createdAt: now, updatedAt: now,
        photos: [], plate, country,
      }
      const next = { ...state, entries: [entry, ...state.entries] }
      await this.commit(next)
      return publicState(next)
    })
  }

  update(idValue: unknown, value: unknown): Promise<WhitelistState> {
    const id = validateId(idValue)
    const body = objectBody(value)
    onlyFields(body, ['label', 'notes', 'enabled', 'plate', 'country'])
    if (!Object.keys(body).length) throw new WhitelistError(400, 'Нет изменений для сохранения.')
    return this.serialized(async () => {
      const state = await this.load()
      const previous = this.find(state, id)
      const entry = { ...previous }
      if ('label' in body) entry.label = labelText(body.label)
      if ('notes' in body) entry.notes = notesText(body.notes)
      if ('enabled' in body) entry.enabled = enabledFlag(body.enabled)
      if ('plate' in body || 'country' in body) {
        if (entry.kind !== 'plate') throw new WhitelistError(400, 'Номер можно изменить только у автомобиля.')
        if ('plate' in body) entry.plate = normalizePlate(body.plate)
        if ('country' in body) entry.country = plateCountry(body.country)
        if (state.entries.some(other => other.id !== id && other.kind === 'plate' && other.country === entry.country && other.plate === entry.plate)) {
          throw new WhitelistError(409, 'Этот номер уже добавлен для выбранной страны.')
        }
      }
      entry.updatedAt = new Date().toISOString()
      const next = { ...state, entries: state.entries.map(item => item.id === id ? entry : item) }
      await this.commit(next)
      return publicState(next)
    })
  }

  remove(idValue: unknown): Promise<WhitelistState & { ok: true }> {
    const id = validateId(idValue)
    return this.serialized(async () => {
      const state = await this.load()
      const entry = this.find(state, id)
      const next = { ...state, entries: state.entries.filter(item => item.id !== id) }
      await this.commit(next)
      await this.cleanPhotos(id, entry.photos)
      return { ...publicState(next), ok: true }
    })
  }

  async addPhotos(idValue: unknown, photos: PhotoInput[]): Promise<WhitelistState> {
    const id = validateId(idValue)
    return this.serialized(async () => {
      const state = await this.load()
      const entry = this.find(state, id)
      if (entry.kind !== 'person') throw new WhitelistError(400, 'Фотографии можно добавлять только людям.')
      if (entry.photos.length + photos.length > MAX_PHOTOS) throw new WhitelistError(400, 'У одной записи может быть до 5 фотографий.')
      const buffers = await sanitizePhotos(photos)
      const ids = await this.writePhotos(id, buffers)
      try {
        const updated = { ...entry, photos: [...entry.photos, ...ids], updatedAt: new Date().toISOString() }
        const next = { ...state, entries: state.entries.map(item => item.id === id ? updated : item) }
        await this.commit(next)
        return publicState(next)
      }
      catch (error) {
        await this.cleanPhotos(id, ids)
        throw error
      }
    })
  }

  removePhoto(idValue: unknown, photoValue: unknown): Promise<WhitelistState> {
    const id = validateId(idValue)
    const photoId = validateId(photoValue)
    return this.serialized(async () => {
      const state = await this.load()
      const entry = this.find(state, id)
      if (!entry.photos.includes(photoId)) throw new WhitelistError(404, 'Фотография не найдена.')
      if (entry.photos.length < 2) throw new WhitelistError(400, 'В записи должна остаться хотя бы одна фотография.')
      const updated = { ...entry, photos: entry.photos.filter(item => item !== photoId), updatedAt: new Date().toISOString() }
      const next = { ...state, entries: state.entries.map(item => item.id === id ? updated : item) }
      await this.commit(next)
      await this.cleanPhotos(id, [photoId])
      return publicState(next)
    })
  }

  getPhoto(idValue: unknown, photoValue: unknown): Promise<Buffer> {
    const id = validateId(idValue)
    const photoId = validateId(photoValue)
    return this.serialized(async () => {
      const entry = this.find(await this.load(), id)
      if (!entry.photos.includes(photoId)) throw new WhitelistError(404, 'Фотография не найдена.')
      try {
        return await readFile(this.photoPath(id, photoId))
      }
      catch (error) {
        if (isMissing(error)) throw new WhitelistError(404, 'Фотография не найдена.')
        throw error
      }
    })
  }

  setEnabled(value: unknown): Promise<WhitelistState> {
    const body = objectBody(value)
    onlyFields(body, ['enabled'])
    const enabled = enabledFlag(body.enabled)
    return this.serialized(async () => {
      const next = { ...await this.load(), enabled }
      await this.commit(next)
      return publicState(next)
    })
  }
}

const GLOBAL_STORE = Symbol.for('privacy-guard.whitelist-store')
const storeGlobal = globalThis as typeof globalThis & { [GLOBAL_STORE]?: WhitelistStore }

export function getWhitelistStore(): WhitelistStore {
  return storeGlobal[GLOBAL_STORE] ??= new WhitelistStore(process.env.PRIVACY_GUARD_DATA_DIR || resolve(process.cwd(), 'data'))
}

/** Exact country-aware match for a plate supplied by a real OCR adapter. */
export function matchWhitelistPlate(state: WhitelistState, recognizedPlate: string, country: PlateCountry = 'KZ'): WhitelistEntry | null {
  if (!state.enabled) return null
  let normalized: string
  try {
    normalized = normalizePlate(recognizedPlate)
  }
  catch {
    return null
  }
  return state.entries.find(entry => entry.enabled && entry.kind === 'plate' && entry.country === country && entry.plate === normalized) ?? null
}
