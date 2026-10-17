import { ref } from 'vue'
import ru from './locales/ru.js'

// Язык интерфейса. Строки в коде — английские, перевод — каталог locales/<язык>.js с ключом
// «английский текст». Нет перевода — показывается английский. По умолчанию язык браузера,
// ручной выбор запоминается на устройстве.
export const LANGS = [{ code: 'en', name: 'English' }, { code: 'ru', name: 'Русский' }]  // i18n-skip: название языка на нём самом
const CATALOGS = { ru }
const KEY = 'nt_lang'
const known = code => LANGS.some(l => l.code === code)

export function browserLang(list = globalThis.navigator?.languages ?? [globalThis.navigator?.language]) {
  for (const item of list || []) {
    const code = String(item || '').slice(0, 2).toLowerCase()
    if (known(code)) return code
  }
  return 'en'
}
function initial() {
  try { const saved = localStorage.getItem(KEY); if (known(saved)) return saved } catch {}
  return browserLang()
}

export const lang = ref(initial())
function mark() { if (globalThis.document) document.documentElement.lang = lang.value }
mark()

export function setLang(code) {
  if (!known(code)) return
  lang.value = code
  try { localStorage.setItem(KEY, code) } catch {}
  mark()
}

const fill = (text, params) => params ? text.replace(/\{(\w+)\}/g, (m, k) => k in params ? String(params[k]) : m) : text

// t('Snapshot {time}', { time }) — перевод с подстановкой именованных параметров.
export function t(text, params) {
  const found = CATALOGS[lang.value]?.[text]
  return fill(typeof found === 'string' ? found : text, params)
}

// tn(n, '{n} node', '{n} nodes') — число с формой слова. В каталоге ключ — форма для одного,
// значение — массив форм языка (для русского: одна, две–четыре, пять).
const RULES = {
  en: n => n === 1 ? 0 : 1,
  ru: n => n % 10 === 1 && n % 100 !== 11 ? 0 : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? 1 : 2,
}
export function tn(n, one, other, params = {}) {
  const found = CATALOGS[lang.value]?.[one]
  const forms = Array.isArray(found) ? found : [one, other]
  const index = Math.min((RULES[lang.value] || RULES.en)(Math.abs(Math.trunc(+n || 0))), forms.length - 1)
  return fill(forms[index], { n, ...params })
}

// Локаль для дат, чисел и сравнения строк.
export const locale = () => lang.value === 'ru' ? 'ru-RU' : 'en-GB'

// Ссылка на документ в репозитории на языке интерфейса: README.md → README.ru.md для русского.
export const docUrl = path => 'https://github.com/ancored/nodes-tester/blob/master/'
  + (lang.value === 'ru' ? path.replace(/\.md$/, '.ru.md') : path)
