import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test, type TestContext } from 'node:test'
import sharp from 'sharp'
import { WhitelistStore, matchWhitelistPlate } from '../server/utils/whitelist-store'
import { sanitizePhoto } from '../server/utils/whitelist-images'
import { normalizePlate, WhitelistError } from '../server/utils/whitelist-validation'

async function fixture(t: TestContext): Promise<WhitelistStore> {
  const directory = await mkdtemp(join(tmpdir(), 'privacy-guard-whitelist-'))
  t.after(async () => { await rm(directory, { recursive: true, force: true }) })
  return new WhitelistStore(directory)
}

async function image() {
  return { data: await sharp({ create: { width: 48, height: 32, channels: 3, background: '#aaaaaa' } }).png().toBuffer(), type: 'image/png' }
}

function errorCode(code: number) {
  return (error: unknown) => error instanceof WhitelistError && error.statusCode === code
}

test('new store starts empty and disabled', async (t) => {
  const store = await fixture(t)
  assert.deepEqual(await store.getState(), { enabled: false, entries: [] })
  assert.deepEqual(await readdir(store.directory), [])
})

test('plate normalization retains distinct zeros and letters, rejects arbitrary text', () => {
  assert.equal(normalizePlate(' 123 АВС-02 '), '123ABC02')
  assert.equal(normalizePlate('О001О0'), 'O001O0')
  assert.throws(() => normalizePlate('ABCD'), errorCode(400))
  assert.throws(() => normalizePlate('<script>1'), errorCode(400))
  assert.throws(() => normalizePlate('12345Ж'), errorCode(400))
})

test('plates persist through restart with real enable and edit state', async (t) => {
  const store = await fixture(t)
  const created = await store.addPlate({ label: 'Служебный автомобиль', notes: 'Проходная', plate: '123 АВС 02' })
  const entry = created.entries[0]!
  assert.equal(entry.plate, '123ABC02')
  assert.equal(entry.country, 'KZ')
  assert.equal(entry.kind, 'plate')
  assert.deepEqual(entry.photos, [])
  await store.setEnabled({ enabled: true })
  await store.update(entry.id, { label: 'Доставка', notes: '', plate: '456DEF02', enabled: false })
  const restored = await new WhitelistStore(store.directory).getState()
  assert.equal(restored.enabled, true)
  assert.equal(restored.entries[0]!.label, 'Доставка')
  assert.equal(restored.entries[0]!.enabled, false)
  assert.equal(restored.entries[0]!.plate, '456DEF02')
})

test('duplicates use normalized plate and country, even when a record is disabled', async (t) => {
  const store = await fixture(t)
  const initial = await store.addPlate({ label: 'Первый', plate: '123ABC02', country: 'KZ' })
  await store.update(initial.entries[0]!.id, { enabled: false })
  await assert.rejects(store.addPlate({ label: 'Повтор', plate: '123 АВС-02', country: 'KZ' }), errorCode(409))
  await store.addPlate({ label: 'Другая страна', plate: '123ABC02', country: 'OTHER' })
  const next = await store.addPlate({ label: 'Второй', plate: '456DEF02', country: 'KZ' })
  await assert.rejects(store.update(next.entries[0]!.id, { plate: '123ABC02' }), errorCode(409))
  assert.equal((await store.getState()).entries.length, 3)
})

test('concurrent mutations serialize without lost entries and failed changes release the queue', async (t) => {
  const store = await fixture(t)
  await Promise.all(Array.from({ length: 20 }, (_, index) => store.addPlate({ label: `Машина ${index}`, plate: `AUTO${1000 + index}` })))
  await assert.rejects(store.addPlate({ label: 'Повтор', plate: 'AUTO1000' }), errorCode(409))
  await store.addPlate({ label: 'Следующий', plate: 'AUTO2000' })
  const restored = await new WhitelistStore(store.directory).getState()
  assert.equal(restored.entries.length, 21)
  assert.equal(new Set(restored.entries.map(entry => entry.id)).size, 21)
})

test('photo records use generated URLs and sanitized JPEG and survive restart', async (t) => {
  const store = await fixture(t)
  const original = await image()
  const state = await store.addPerson({ label: 'Иван Петров', notes: 'Сотрудник' }, [original])
  const entry = state.entries[0]!
  const photo = entry.photos[0]!
  assert.equal(entry.plate, null)
  assert.equal(entry.country, null)
  assert.equal(photo.url, `/api/whitelist/${entry.id}/photos/${photo.id}`)
  const stored = await store.getPhoto(entry.id, photo.id)
  const metadata = await sharp(stored).metadata()
  assert.equal(metadata.format, 'jpeg')
  assert.equal(metadata.width, 48)
  assert.equal(metadata.height, 32)
  assert.equal(metadata.exif, undefined)
  assert.deepEqual(await new WhitelistStore(store.directory).getState(), state)
  // Callers cannot alter the in-memory store by changing a returned object.
  entry.label = 'Изменение снаружи'
  photo.url = 'file:///secret'
  assert.equal((await store.getState()).entries[0]!.label, 'Иван Петров')
})

test('photo normalization honors orientation and reduces dimensions without EXIF', async () => {
  const source = await sharp({ create: { width: 2400, height: 1000, channels: 3, background: '#ffffff' } })
    .jpeg().withMetadata({ orientation: 6 }).toBuffer()
  const result = await sanitizePhoto({ data: source, type: 'image/jpeg' })
  const metadata = await sharp(result).metadata()
  assert.equal(metadata.width, 667)
  assert.equal(metadata.height, 1600)
  assert.equal(metadata.orientation, undefined)
  assert.equal(metadata.exif, undefined)
})

test('invalid photos leave neither visible metadata nor files behind', async (t) => {
  const store = await fixture(t)
  const good = await image()
  await assert.rejects(store.addPerson({ label: 'Не сохранять' }, [good, { data: Buffer.from('<svg/>'), type: 'image/png' }]), errorCode(400))
  assert.deepEqual(await store.getState(), { enabled: false, entries: [] })
  assert.deepEqual(await readdir(store.directory), [])
  await assert.rejects(sanitizePhoto({ ...good, type: 'image/jpeg' }), errorCode(400))
  await assert.rejects(sanitizePhoto({ data: Buffer.alloc(10 * 1024 * 1024 + 1), type: 'image/jpeg' }), errorCode(400))
})

test('failed metadata commit rolls back newly written photos and preserves cached state', async (t) => {
  const store = await fixture(t)
  const initial = await store.getState()
  // A directory occupying the metadata file path makes the atomic rename fail.
  await mkdir(join(store.directory, 'whitelist.json'))
  await assert.rejects(store.addPerson({ label: 'Не сохранять' }, [await image()]))
  assert.deepEqual(await store.getState(), initial)
  assert.deepEqual(await readdir(join(store.directory, 'photos')), [])
  assert.equal((await readdir(store.directory)).some(name => name.endsWith('.tmp')), false)
})

test('photo CRUD enforces total count and last-photo rule and removes files', async (t) => {
  const store = await fixture(t)
  const good = await image()
  const created = await store.addPerson({ label: 'Сотрудник' }, [good])
  const entry = created.entries[0]!
  const firstId = entry.photos[0]!.id
  await assert.rejects(store.removePhoto(entry.id, firstId), errorCode(400))
  const added = await store.addPhotos(entry.id, [good, good, good, good])
  assert.equal(added.entries[0]!.photos.length, 5)
  await assert.rejects(store.addPhotos(entry.id, [good]), errorCode(400))
  const removed = await store.removePhoto(entry.id, firstId)
  assert.equal(removed.entries[0]!.photos.length, 4)
  await assert.rejects(store.getPhoto(entry.id, firstId), errorCode(404))
  assert.equal((await readdir(join(store.directory, 'photos', entry.id))).length, 4)
  const deleted = await store.remove(entry.id)
  assert.equal(deleted.ok, true)
  assert.deepEqual(deleted.entries, [])
  assert.deepEqual(await readdir(join(store.directory, 'photos')), [])
})

test('traversal and incompatible fields cannot mutate stored records', async (t) => {
  const store = await fixture(t)
  const created = await store.addPlate({ label: 'Машина', plate: '123ABC02' })
  assert.throws(() => store.update(created.entries[0]!.id, { photos: ['../../secret'] }), errorCode(400))
  assert.throws(() => store.getPhoto('../secret', '../file'), errorCode(404))
  await assert.rejects(store.addPhotos(created.entries[0]!.id, [await image()]), errorCode(400))
  assert.deepEqual(await store.getState(), created)
})

test('corrupt saved metadata is preserved and never silently reset', async (t) => {
  const store = await fixture(t)
  const path = join(store.directory, 'whitelist.json')
  const contents = '{corrupt'
  await writeFile(path, contents)
  await assert.rejects(store.getState(), errorCode(503))
  await assert.rejects(store.addPlate({ label: 'Новая', plate: '123ABC02' }), errorCode(503))
  assert.equal(await readFile(path, 'utf8'), contents)
})

test('plate matching requires actual exact recognized plate, matching country and active state', async (t) => {
  const store = await fixture(t)
  const state = await store.addPlate({ label: 'Автомобиль', plate: '123ABC02' })
  assert.equal(matchWhitelistPlate(state, '123ABC02'), null)
  const enabled = await store.setEnabled({ enabled: true })
  assert.equal(matchWhitelistPlate(enabled, '123 АВС-02')?.id, enabled.entries[0]!.id)
  assert.equal(matchWhitelistPlate(enabled, '123ABC02', 'RU'), null)
  assert.equal(matchWhitelistPlate(enabled, '123ABC03'), null)
  assert.equal(matchWhitelistPlate(enabled, 'garbage'), null)
  const disabled = await store.update(enabled.entries[0]!.id, { enabled: false })
  assert.equal(matchWhitelistPlate(disabled, '123ABC02'), null)
})
