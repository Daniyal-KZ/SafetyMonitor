import { defineEventHandler } from 'h3'
import { readWhitelistJson, whitelistRequest } from '../../utils/whitelist-http'
import { getWhitelistStore } from '../../utils/whitelist-store'

export default defineEventHandler(event => whitelistRequest(async () => getWhitelistStore().setEnabled(await readWhitelistJson(event))))
