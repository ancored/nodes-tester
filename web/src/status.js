import { t } from './i18n.js'
// Статусы нод (пояснения к ним — в справке, docs/help/restrictions.md). Подпись — геттер:
// переводится при каждом обращении, смена языка видна сразу.
const status = (key, cls, label) => ({ key, cls, get label() { return label() } })
export const STATUSES = [
  status('removed', 'mut', () => t('missing')),
  status('banned', 'bad', () => t('excluded manually')),
  status('quarantine', 'bad', () => t('quarantine')),
  status('pause', 'heavy', () => t('checks paused')),
  status('active', 'good', () => t('selected by the switcher')),
  status('ok', 'num', () => t('positive rating')),
  status('zero', 'bad', () => t('rating 0')),
  status('unknown', 'mut', () => t('no measurements')),
]
export const STATUS=Object.fromEntries(STATUSES.map(s=>[s.key,s]))
export function nodeStatus(n){
  const g=n.gstate ?? n.state
  if(n.present != null && +n.present === 0)return STATUS.removed
  if(+n.banned === 1)return STATUS.banned
  if(g==='garbage')return STATUS.quarantine
  if(g==='backoff')return STATUS.pause
  if(+n.active === 1)return STATUS.active
  if(n.score == null)return STATUS.unknown
  return +n.score>0 ? STATUS.ok : STATUS.zero
}
const cause = (cls, label, hint) => ({ cls, get label() { return label() }, get hint() { return hint() } })
export const CAUSES={
  alive:cause('good', () => t('positive rating'), () => t('Had a positive rating at the last presence')),
  zero:cause('heavy', () => t('rating 0'), () => t('Had a zero rating at the last presence')),
  quarantine:cause('bad', () => t('was quarantined'), () => t('Quarantine was in force when it disappeared')),
  banned:cause('bad', () => t('was excluded'), () => t('Was excluded manually before it disappeared')),
  untested:cause('mut', () => t('no measurements'), () => t('No saved rating')),
}
