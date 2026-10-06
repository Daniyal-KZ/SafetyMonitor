import tailwindcss from '@tailwindcss/vite'

export default defineNuxtConfig({
  compatibilityDate: '2025-01-01',
  css: ['~/assets/css/main.css'],
  vite: { plugins: [tailwindcss()] },
  typescript: { strict: true, typeCheck: true },
  nitro: { preset: 'node-server' },
  devtools: { enabled: false },
  app: {
    head: {
      title: 'Privacy Guard',
      meta: [
        { name: 'description', content: 'Privacy Guard — интерфейс маскирования конфиденциальных данных на видео и с камеры.' },
        { name: 'theme-color', content: '#edf0f3' },
      ],
      link: [{ rel: 'icon', type: 'image/svg+xml', href: '/favicon.svg' }],
    },
  },
})
