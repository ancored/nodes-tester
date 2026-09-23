<script setup>
defineProps({ title: { type: String, default: '' } })
</script>

<template>
  <section>
    <div class="wrap" style="padding: 24px">
      <h2>{{ title }}</h2>
      <p class="empty">Раздел появится в следующей фазе.</p>
    </div>
  </section>
</template>
