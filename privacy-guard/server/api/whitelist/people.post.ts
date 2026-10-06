import { defineEventHandler } from 'h3'
import { readWhitelistPhotos, whitelistRequest } from '../../utils/whitelist-http'
import { getWhitelistStore } from '../../utils/whitelist-store'

export default defineEventHandler(event => whitelistRequest(async () => {
  const form = await readWhitelistPhotos(event, true)
  return getWhitelistStore().addPerson({ label: form.label, notes: form.notes }, form.photos)
}))
