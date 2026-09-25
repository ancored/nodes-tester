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

// Hash-history: работает при раздаче статики без серверного роутинга.
export const sections = [
  { path: '/', name: 'Обзор', component: Overview },
  { path: '/rating', name: 'Рейтинг', component: Rating },
  { path: '/results', name: 'Результаты', component: Results },
  { path: '/traffic', name: 'Трафик', component: Traffic },
  { path: '/lifecycle', name: 'Жизненный цикл', component: Lifecycle },
  { path: '/graveyard', name: 'Кладбище', component: Graveyard },
  { path: '/history', name: 'Переключения', component: History },
  { path: '/control', name: 'Управление', component: Control },
  { path: '/runs', name: 'Прогоны', component: Runs },
  { path: '/config', name: 'Конфиг', component: Config },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [...sections, { path: '/:pathMatch(.*)*', redirect: '/' }],
})
