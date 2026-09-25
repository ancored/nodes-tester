// Единый словарь статусов нод — ОДИН термин на одно понятие во всех разделах админки.
// Значения в БД не меняются (garbage.reason = backoff|garbage, nodes.present/banned),
// меняются только подписи в UI. Легенда — компонент StatusLegend.
//
// Статусы взаимоисключающие; если у ноды несколько признаков, побеждает первый по списку.

export const STATUSES = [
  {
    key: 'removed', label: 'удалена', cls: 'mut',
    was: 'ушла, удалена, present=0',
    what: 'Нода пропала из подписки провайдера. Не тестируется. Хранится в БД ещё retention_days (по умолчанию 30 дн.) для истории, потом стирается целиком.',
    who: 'подписка (автоматически)',
  },
  {
    key: 'banned', label: 'бан', cls: 'bad',
    was: 'бан, banned',
    what: 'Ручное исключение: нода не тестируется и не участвует в рейтинге/переключении. Бессрочно.',
    who: 'вы, в «Управлении»; снимается только вручную',
  },
  {
    key: 'quarantine', label: 'карантин', cls: 'bad',
    was: 'мусор, мусорная, garbage',
    what: 'Потолок паузы: пропуск дорос до max_skip (по умолчанию 32 прогона) — на 6-м провале подряд. Не тестируется garbage_hours (по умолчанию 72 ч), затем одна проба: прошла — в строй, нет — снова карантин.',
    who: 'тестер (автоматически) или вы вручную',
  },
  {
    key: 'pause', label: 'пауза', cls: 'heavy',
    was: 'backoff, cooldown',
    what: 'Нода провалила gate (базовую проверку) и пропускает следующие прогоны: 1, затем 2, 4, 8, 16 — каждый провал подряд удваивает пропуск. Дорос до max_skip → карантин. Прошла gate → в строй.',
    who: 'тестер (автоматически)',
  },
  {
    key: 'active', label: 'активная', cls: 'good',
    was: 'active, боевая',
    what: 'Именно через эту ноду сейчас идёт трафик региона (выбрана переключателем).',
    who: 'switcher',
  },
  {
    key: 'ok', label: 'в строю', cls: 'num',
    was: 'живая, ·',
    what: 'В подписке, тестируется, рейтинг > 0 — кандидат на активную.',
    who: '—',
  },
  {
    key: 'zero', label: 'рейтинг 0', cls: 'bad',
    was: 'мёртвая',
    what: 'В подписке и тестируется, но рейтинг 0: последний прогон не прошёл (gate/DL50-veto) или замеров ещё нет. Обычно тут же уходит в паузу.',
    who: '—',
  },
]

export const STATUS = Object.fromEntries(STATUSES.map((s) => [s.key, s]))

// Статус ноды по полям снимка: present, banned, gstate|state (backoff|garbage), active, score.
export function nodeStatus(n) {
  const g = n.gstate ?? n.state
  if (n.present != null && +n.present === 0) return STATUS.removed
  if (+n.banned === 1) return STATUS.banned
  if (g === 'garbage') return STATUS.quarantine
  if (g === 'backoff') return STATUS.pause
  if (+n.active === 1) return STATUS.active
  if ((+n.score || 0) > 0) return STATUS.ok
  return STATUS.zero
}

// Почему нода ушла на «Кладбище» (поле cause из _graveyard).
export const CAUSES = {
  alive: { label: 'ушла рабочей', cls: 'good', hint: 'провайдер убрал ноду, которая ещё проходила тесты' },
  zero: { label: 'ушла с рейтингом 0', cls: 'heavy', hint: 'на момент удаления была на паузе или с нулевым рейтингом' },
  quarantine: { label: 'ушла из карантина', cls: 'bad', hint: 'к моменту удаления уже сидела в карантине' },
  banned: { label: 'была в бане', cls: 'bad', hint: 'забанена вами до удаления' },
  untested: { label: 'не тестировалась', cls: 'mut', hint: 'нет ни одного замера рейтинга' },
}
