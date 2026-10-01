import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router.js'
import './styles.css'
import { polyfillCountryFlagEmojis } from 'country-flag-emoji-polyfill'
// Шрифт флагов — из сборки (Windows и Chromium не рисуют флаги-эмодзи сами).
import flagFont from 'country-flag-emoji-polyfill/dist/TwemojiCountryFlags.woff2?url'

polyfillCountryFlagEmojis('Twemoji Country Flags', flagFont)

createApp(App).use(router).mount('#app')
