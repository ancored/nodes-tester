<script setup>
import { ref, watch } from 'vue'
// array — поле ждёт JSON-массив, иначе JSON-объект.
const props=defineProps({modelValue:{default:undefined},label:String,note:String,array:Boolean})
const emit=defineEmits(['update:modelValue','valid'])
const text=ref(''), error=ref('')
const empty=()=>props.array ? [] : {}
watch(()=>props.modelValue,v=>{const formatted=JSON.stringify(v ?? empty(),null,2);try{if(JSON.stringify(JSON.parse(text.value))===JSON.stringify(v ?? empty()))return}catch{}text.value=formatted;error.value='';emit('valid',true)},{immediate:true,deep:true})
function edit() {
  try {const v=JSON.parse(text.value);if(!v || typeof v!=='object' || Array.isArray(v)!==props.array)throw new Error(props.array ? 'Нужен JSON-массив' : 'Нужен JSON-объект');error.value='';emit('valid',true);emit('update:modelValue',v)}
  catch(e){error.value=e.message;emit('valid',false)}
}
</script>
<template><label>{{ label }}<textarea v-model="text" class="json-field" spellcheck="false" @input="edit" :aria-label="label" /><span v-if="note" class="field-note">{{ note }}</span><span v-if="error" class="bad" role="alert">{{ error }}</span></label></template>
