<script setup>
// Страница справки: текст, переход по ссылкам внутри справки и прокрутка к разделу.
import { nextTick, ref, watch } from 'vue'
import { loadHelp } from '../help.js'
import { lang, t } from '../i18n.js'
const props = defineProps({ page: { type: String, required: true }, anchor: { type: String, default: '' } })
const emit = defineEmits(['navigate', 'loaded'])
const doc = ref(null), error = ref(''), box = ref(null)
async function show() {
  error.value = ''
  try {
    const page = props.page
    const loaded = await loadHelp(page, lang.value)
    if (page !== props.page) return
    doc.value = loaded; emit('loaded', loaded)
    await nextTick(); scroll()
  } catch (e) { doc.value = null; error.value = e.message }
}
function scroll() {
  const root = box.value
  if (!root) return
  const target = props.anchor && root.querySelector('[id="' + props.anchor.replace(/"/g, '') + '"]')
  const scroller = root.closest('.help-scroll') || document.scrollingElement
  if (!target) { scroller.scrollTop = 0; return }
  const heading = target.closest('h1,h2,h3,h4,h5,h6') || target
  heading.scrollIntoView({ block: 'start' })
  heading.classList.add('help-target')
  setTimeout(() => heading.classList.remove('help-target'), 2000)
}
// Ссылки на другие страницы справки открываются тут же (в панели — в панели).
function click(event) {
  const a = event.target.closest('a[data-help]')
  if (!a || event.ctrlKey || event.metaKey || event.shiftKey) return
  event.preventDefault()
  const [page, anchor] = a.dataset.help.split('#')
  emit('navigate', page, anchor)
}
watch(() => [props.page, lang.value], show, { immediate: true })
watch(() => props.anchor, async () => { await nextTick(); scroll() })
</script>
<template>
  <article ref="box" class="help-doc" @click="click">
    <p v-if="error" class="notice bad">{{ t('Help is unavailable: {error}', { error }) }}</p>
    <p v-else-if="!doc" class="mut">{{ t('Loading…') }}</p>
    <div v-else v-html="doc.html"></div>
  </article>
</template>
