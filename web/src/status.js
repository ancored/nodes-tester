import { t } from './i18n.js'
// Friendly explanations; presence, quality, restrictions and use are independent flags in node cards.
// Тексты — геттеры: переводятся при каждом обращении, смена языка видна сразу.
const status = (key, cls, label, what, who) => ({ key, cls, get label() { return label() }, get what() { return what() }, get who() { return who() } })
export const STATUSES = [
  status('removed', 'mut', () => t('missing'), () => t('Not in the current tester list. The history is kept for storage.retention after the last presence.'), () => t('sing-box list')),
  status('banned', 'bad', () => t('excluded manually'), () => t('Indefinite exclusion from the next checks. Does not remove the node from the subscription and does not change the current selector immediately.'), () => t('administrator')),
  status('quarantine', 'bad', () => t('quarantine'), () => t('Temporary restriction for selection.quarantine.duration. A new check is possible afterwards.'), () => t('tester or administrator')),
  status('pause', 'heavy', () => t('checks paused'), () => t('After a failed basic check the node is not checked for 2^(n−1) connection test periods; once the skip reaches selection.quarantine.after_skips it goes to quarantine.'), () => t('tester')),
  status('active', 'good', () => t('selected by the switcher'), () => t('The last choice of the switcher for the region. It is not a check of the actual sing-box selector or of the connection.'), () => t('switcher')),
  status('ok', 'num', () => t('positive rating'), () => t('The node has a positive rating. Whether it can be selected manually is checked by the server as well.'), () => t('tester')),
  status('zero', 'bad', () => t('rating 0'), () => t('The measured rating is zero. See the results and events for the cause.'), () => t('tester')),
  status('unknown', 'mut', () => t('no measurements'), () => t('There is no saved rating. This does not mean a failed check.'), () => '—'),
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
