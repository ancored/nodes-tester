<script setup>
import { ref, watch } from 'vue'
const props=defineProps({modelValue:{default:undefined},label:String,note:String})
const emit=defineEmits(['update:modelValue','valid'])
const text=ref(''), error=ref('')
watch(()=>props.modelValue,v=>{const formatted=JSON.stringify(v ?? {},null,2);try{if(JSON.stringify(JSON.parse(text.value))===JSON.stringify(v ?? {}))return}catch{}text.value=formatted;error.value='';emit('valid',true)},{immediate:true,deep:true})
function edit() {
  try {const v=JSON.parse(text.value);if(!v || Array.isArray(v) || typeof v!=='object')throw new Error('Нужен JSON-объект');error.value='';emit('valid',true);emit('update:modelValue',v)}
  catch(e){error.value=e.message;emit('valid',false)}
}
</script>
<template><label>{{ label }}<textarea v-model="text" class="json-field" spellcheck="false" @input="edit" :aria-label="label" /><span class="mut">{{ note }}</span><span v-if="error" class="bad" role="alert">{{ error }}</span></label></template>
