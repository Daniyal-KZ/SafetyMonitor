import { defineEventHandler, getRouterParam, setHeader } from 'h3'
import { whitelistRequest } from '../../../../utils/whitelist-http'
import { getWhitelistStore } from '../../../../utils/whitelist-store'

export default defineEventHandler(event => whitelistRequest(async () => {
  const image = await getWhitelistStore().getPhoto(getRouterParam(event, 'id'), getRouterParam(event, 'photoId'))
  setHeader(event, 'Content-Type', 'image/jpeg')
  setHeader(event, 'Content-Length', image.length)
  return image
}))
