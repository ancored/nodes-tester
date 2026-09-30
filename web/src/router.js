import { createRouter, createWebHashHistory } from 'vue-router'
import Overview from './views/Overview.vue'
import Rating from './views/Rating.vue'
import Results from './views/Results.vue'
import Traffic from './views/Traffic.vue'
import Lifecycle from './views/Lifecycle.vue'
import Graveyard from './views/Graveyard.vue'
import History from './views/History.vue'
import Control from './views/Control.vue'
import Runs from './views/Runs.vue'
import Config from './views/Config.vue'
import NodeDetail from './views/NodeDetail.vue'
import Pipeline from './views/Pipeline.vue'
import Singbox from './views/Singbox.vue'
import Presets from './views/Presets.vue'
export const sections = [
  { path: '/', name: 'Обзор', component: Overview },
  { path: '/nodes', name: 'Ноды', component: Control },
  { path: '/subscriptions', name: 'Подписки и сборка', component: Config },
  { path: '/pipeline', name: 'Конвейер', component: Pipeline },
  { path: '/presets', name: 'Правила', component: Presets },
  { path: '/singbox', name: 'Файлы sing-box', component: Singbox },
  { path: '/runs', name: 'Проверки', component: Runs },
  { path: '/history', name: 'Переключения', component: History },
  { path: '/traffic', name: 'Трафик', component: Traffic },
  { path: '/config', name: 'Настройки', component: Config },
]
export const nodeSections = [
  { path: '/nodes', name: 'Все ноды' },
  { path: '/rating', name: 'Рейтинг', component: Rating },
  { path: '/results', name: 'Результаты', component: Results },
  { path: '/lifecycle', name: 'Ограничения и качество', component: Lifecycle },
  { path: '/graveyard', name: 'Архив', component: Graveyard },
]
export const router = createRouter({ history: createWebHashHistory(), routes: [
  ...sections, ...nodeSections.slice(1), { path: '/nodes/:crc', name: 'Карточка ноды', component: NodeDetail },
  { path: '/control', redirect: '/nodes' }, { path: '/:pathMatch(.*)*', redirect: '/' },
] })
