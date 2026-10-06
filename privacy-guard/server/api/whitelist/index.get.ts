import { defineEventHandler } from 'h3'
import { whitelistRequest } from '../../utils/whitelist-http'
import { getWhitelistStore } from '../../utils/whitelist-store'

export default defineEventHandler(() => whitelistRequest(() => getWhitelistStore().getState()))
