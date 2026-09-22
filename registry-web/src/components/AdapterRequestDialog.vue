<script setup lang="ts">
import { reactive } from 'vue'
import { Puzzle, X } from 'lucide-vue-next'
const form = reactive({ agentName: '', contact: '', endpoint: '', documentationUrl: '', notes: '' })
defineEmits<{ close: []; submit: [payload: Record<string, string>] }>()
</script>
<template>
  <div class="modal-backdrop" @click.self="$emit('close')">
    <form class="modal wide-modal" @submit.prevent="$emit('submit', { ...form })">
      <header><div class="modal-icon"><Puzzle :size="20" /></div><div><span>ADAPTER REQUEST</span><h2>申请新增Adapter</h2></div><button class="icon-button" type="button" @click="$emit('close')"><X :size="18" /></button></header>
      <div class="form-grid"><label><span>Agent名称</span><input v-model="form.agentName" required /></label><label><span>联系人</span><input v-model="form.contact" required /></label><label class="wide"><span>Endpoint</span><input v-model="form.endpoint" type="url" required /></label><label class="wide"><span>接口文档</span><input v-model="form.documentationUrl" type="url" /></label><label class="wide"><span>适配说明</span><textarea v-model="form.notes" rows="5" required></textarea></label></div>
      <footer><button class="button secondary" type="button" @click="$emit('close')">取消</button><button class="button primary" type="submit">提交申请</button></footer>
    </form>
  </div>
</template>
