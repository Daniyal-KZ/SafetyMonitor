import assert from 'node:assert/strict'
import { createServer, request as nodeRequest } from 'node:http'
import { once } from 'node:events'
import { test, type TestContext } from 'node:test'
import { createApp, defineEventHandler, toNodeListener } from 'h3'
import { assertWhitelistOrigin, readLimitedBody, readWhitelistJson, readWhitelistPhotos, whitelistRequest } from '../server/utils/whitelist-http'

async function server(t: TestContext) {
  const app = createApp()
  app.use(defineEventHandler((event) => { assertWhitelistOrigin(event) }))
  app.use('/json', defineEventHandler(event => whitelistRequest(() => readWhitelistJson(event))))
  app.use('/photos', defineEventHandler(event => whitelistRequest(async () => {
    const form = await readWhitelistPhotos(event, true)
    return { label: form.label, notes: form.notes, files: form.photos.map(photo => ({ length: photo.data.length, type: photo.type })) }
  })))
  app.use('/limited', defineEventHandler(event => whitelistRequest(async () => ({ bytes: (await readLimitedBody(event, 16)).length }))))
  const http = createServer(toNodeListener(app))
  http.listen(0, '127.0.0.1')
  await once(http, 'listening')
  const address = http.address()
  assert.ok(address && typeof address !== 'string')
  const url = `http://127.0.0.1:${address.port}`
  t.after(async () => {
    http.closeAllConnections()
    await new Promise<void>((resolve, reject) => http.close(error => error ? reject(error) : resolve()))
  })
  return { url, port: address.port }
}

test('local JSON requests pass and foreign browser origins are rejected', async (t) => {
  const { url } = await server(t)
  const valid = await fetch(`${url}/json`, { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: url }, body: '{"enabled":true}' })
  assert.equal(valid.status, 200)
  assert.deepEqual(await valid.json(), { enabled: true })
  for (const origin of ['https://foreign.example', 'null', `${url}.evil.example`]) {
    const rejected = await fetch(`${url}/json`, { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: origin }, body: '{}' })
    assert.equal(rejected.status, 403)
  }
  const crossSite = await fetch(`${url}/json`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'sec-fetch-site': 'cross-site' }, body: '{}' })
  assert.equal(crossSite.status, 403)
})

test('foreign Host is rejected even without Origin, protecting DNS rebinding', async (t) => {
  const { port } = await server(t)
  const status = await new Promise<number>((resolve, reject) => {
    // fetch normalizes Host; use a raw HTTP client to exercise the server check.
    const request = nodeRequest({ hostname: '127.0.0.1', port, path: '/json', method: 'POST', headers: { Host: 'foreign.example', 'Content-Type': 'application/json' } }, response => {
      response.resume()
      response.once('end', () => resolve(response.statusCode!))
    })
    request.once('error', reject)
    request.end('{}')
  })
  assert.equal(status, 403)
})

test('JSON requests require proper content type and bounded valid JSON', async (t) => {
  const { url } = await server(t)
  const form = await fetch(`${url}/json`, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'enabled=true' })
  assert.equal(form.status, 415)
  const malformed = await fetch(`${url}/json`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{broken' })
  assert.equal(malformed.status, 400)
  const oversized = await fetch(`${url}/json`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ data: 'x'.repeat(17000) }) })
  assert.equal(oversized.status, 413)
})

test('multipart upload reads actual uploaded bytes and rejects duplicate fields', async (t) => {
  const { url } = await server(t)
  const form = new FormData()
  form.set('label', 'Сотрудник')
  form.set('notes', 'Примечание')
  form.append('photos', new Blob(['actual bytes'], { type: 'image/png' }), 'photo.png')
  const response = await fetch(`${url}/photos`, { method: 'POST', body: form })
  assert.equal(response.status, 200)
  assert.deepEqual(await response.json(), { label: 'Сотрудник', notes: 'Примечание', files: [{ length: 12, type: 'image/png' }] })
  form.append('label', 'Второе имя')
  const duplicate = await fetch(`${url}/photos`, { method: 'POST', body: form })
  assert.equal(duplicate.status, 400)
})

test('streaming body limits apply without a Content-Length header', async (t) => {
  const { port } = await server(t)
  const result = await new Promise<number>((resolve, reject) => {
    const request = nodeRequest({ hostname: '127.0.0.1', port, path: '/limited', method: 'POST', headers: { 'Transfer-Encoding': 'chunked' } }, response => {
      response.resume()
      response.once('end', () => resolve(response.statusCode!))
    })
    request.once('error', reject)
    request.write(Buffer.alloc(12))
    request.end(Buffer.alloc(12))
  })
  assert.equal(result, 413)
})
