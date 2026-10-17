<script setup>
// Иконка «?»: открывает раздел справки в боковой панели. topic — «страница#якорь» (docs/help/<страница>.md).
import { openHelp } from '../help.js'
import { t } from '../i18n.js'
defineProps({ topic: { type: String, required: true } })
</script>
<template><button type="button" class="help-btn" :title="t('Help')" :aria-label="t('Help')" @click.stop="openHelp(topic)">?</button></template>
