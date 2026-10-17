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
import Help from './views/Help.vue'
// name — имя маршрута и ключ перевода заголовка (t(name)); help — раздел справки страницы (docs/help/<help>.md).
export const sections = [
  { path: '/', name: 'Overview', component: Overview, help: 'overview' },
  { path: '/nodes', name: 'Nodes', component: Control, help: 'nodes#all-nodes' },
  { path: '/subscriptions', name: 'Subscriptions and build', component: Config, help: 'subscriptions' },
  { path: '/pipeline', name: 'Pipeline', component: Pipeline, help: 'pipeline' },
  { path: '/presets', name: 'Rules', component: Presets, help: 'rules' },
  { path: '/singbox', name: 'sing-box files', component: Singbox, help: 'singbox-files' },
  { path: '/runs', name: 'Checks', component: Runs, help: 'runs' },
  { path: '/history', name: 'Switches', component: History, help: 'switches' },
  { path: '/traffic', name: 'Traffic', component: Traffic, help: 'traffic' },
  { path: '/config', name: 'Settings', component: Config, help: 'settings' },
  { path: '/help', name: 'Help', component: Help, help: 'README' },
]
export const nodeSections = [
  { path: '/nodes', name: 'All nodes', help: 'nodes#all-nodes' },
  { path: '/rating', name: 'Rating', component: Rating, help: 'nodes#rating' },
  { path: '/results', name: 'Results', component: Results, help: 'nodes#results' },
  { path: '/lifecycle', name: 'Restrictions and quality', component: Lifecycle, help: 'nodes#restrictions' },
  { path: '/graveyard', name: 'Archive', component: Graveyard, help: 'nodes#archive' },
]
// Раздел справки для маршрута: страница из списков выше или meta.help.
const HELP = Object.fromEntries([...sections, ...nodeSections].map(x => [x.path, x.help]))
export const helpTopic = route => route.meta?.help || HELP[route.path] || ''
export const router = createRouter({ history: createWebHashHistory(), routes: [
  ...sections, ...nodeSections.slice(1), { path: '/nodes/:crc', name: 'Node card', component: NodeDetail, meta: { help: 'node-card' } },
  { path: '/control', redirect: '/nodes' }, { path: '/:pathMatch(.*)*', redirect: '/' },
] })
