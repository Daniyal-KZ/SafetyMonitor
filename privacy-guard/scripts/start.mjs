// Bind the local tool to loopback by default in development and production.
process.env.NITRO_HOST ||= '127.0.0.1'
process.env.NITRO_PORT ||= '4173'
await import('../.output/server/index.mjs')
