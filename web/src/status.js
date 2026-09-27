// Friendly explanations; presence, quality, restrictions and use are independent flags in node cards.
export const STATUSES = [
  {key:'removed',label:'отсутствует',cls:'mut',what:'Нет в текущем списке тестера. История хранится в течение настроенного retention_days после последнего присутствия.',who:'список sing-box'},
  {key:'banned',label:'исключена вручную',cls:'bad',what:'Бессрочное исключение из следующих проверок. Не удаляет ноду из подписки и не меняет текущий селектор немедленно.',who:'администратор'},
  {key:'quarantine',label:'карантин',cls:'bad',what:'Временное ограничение до срока, заданного cooldown.garbage_hours. Затем возможна повторная проверка.',who:'тестер или администратор'},
  {key:'pause',label:'пауза проверок',cls:'heavy',what:'После неудачной базовой проверки пропускает несколько проходов. Их количество растёт до cooldown.max_skip.',who:'тестер'},
  {key:'active',label:'выбрана переключателем',cls:'good',what:'Последний выбор переключателя по региону. Не является проверкой фактического селектора sing-box или соединения.',who:'переключатель'},
  {key:'ok',label:'рейтинг положительный',cls:'num',what:'Есть положительный рейтинг. Возможность ручного выбора дополнительно проверяется сервером.',who:'тестер'},
  {key:'zero',label:'рейтинг 0',cls:'bad',what:'Измеренный рейтинг равен нулю. Причину смотрите в результатах и событиях.',who:'тестер'},
  {key:'unknown',label:'нет замеров',cls:'mut',what:'Сохранённого рейтинга нет. Это не означает провал проверки.',who:'—'},
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
export const CAUSES={
  alive:{label:'положительный рейтинг',cls:'good',hint:'При последнем присутствии был положительный рейтинг'},
  zero:{label:'рейтинг 0',cls:'heavy',hint:'При последнем присутствии был нулевой рейтинг'},
  quarantine:{label:'была в карантине',cls:'bad',hint:'При исчезновении действовал карантин'},
  banned:{label:'была исключена',cls:'bad',hint:'До исчезновения была исключена вручную'},
  untested:{label:'без замеров',cls:'mut',hint:'Нет сохранённого рейтинга'},
}
