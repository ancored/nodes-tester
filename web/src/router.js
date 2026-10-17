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
// name — имя маршрута и ключ перевода заголовка (t(name)).
export const sections = [
  { path: '/', name: 'Overview', component: Overview },
  { path: '/nodes', name: 'Nodes', component: Control },
  { path: '/subscriptions', name: 'Subscriptions and build', component: Config },
  { path: '/pipeline', name: 'Pipeline', component: Pipeline },
  { path: '/presets', name: 'Rules', component: Presets },
  { path: '/singbox', name: 'sing-box files', component: Singbox },
  { path: '/runs', name: 'Checks', component: Runs },
  { path: '/history', name: 'Switches', component: History },
  { path: '/traffic', name: 'Traffic', component: Traffic },
  { path: '/config', name: 'Settings', component: Config },
]
export const nodeSections = [
  { path: '/nodes', name: 'All nodes' },
  { path: '/rating', name: 'Rating', component: Rating },
  { path: '/results', name: 'Results', component: Results },
  { path: '/lifecycle', name: 'Restrictions and quality', component: Lifecycle },
  { path: '/graveyard', name: 'Archive', component: Graveyard },
]
export const router = createRouter({ history: createWebHashHistory(), routes: [
  ...sections, ...nodeSections.slice(1), { path: '/nodes/:crc', name: 'Node card', component: NodeDetail },
  { path: '/control', redirect: '/nodes' }, { path: '/:pathMatch(.*)*', redirect: '/' },
] })
