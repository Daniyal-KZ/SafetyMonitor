import { defineEventHandler, setHeader } from 'h3'
import { assertWhitelistOrigin } from '../utils/whitelist-http'

export default defineEventHandler((event) => {
  if (event.path !== '/api/whitelist' && !event.path.startsWith('/api/whitelist/') && !event.path.startsWith('/api/whitelist?')) return
  assertWhitelistOrigin(event)
  setHeader(event, 'Cache-Control', 'no-store')
  setHeader(event, 'X-Content-Type-Options', 'nosniff')
})
