import { defineEventHandler, getRouterParam } from 'h3'
import { whitelistRequest } from '../../utils/whitelist-http'
import { getWhitelistStore } from '../../utils/whitelist-store'

export default defineEventHandler(event => whitelistRequest(() => getWhitelistStore().remove(getRouterParam(event, 'id'))))
