import { defineEventHandler, getRouterParam } from 'h3'
import { readWhitelistPhotos, whitelistRequest } from '../../../utils/whitelist-http'
import { getWhitelistStore } from '../../../utils/whitelist-store'

export default defineEventHandler(event => whitelistRequest(async () => {
  const form = await readWhitelistPhotos(event, false)
  return getWhitelistStore().addPhotos(getRouterParam(event, 'id'), form.photos)
}))
