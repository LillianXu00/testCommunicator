<script setup lang="ts">
import { ref } from 'vue'
import { KeyRound, X } from 'lucide-vue-next'
import { getAdminToken } from '../api'

const token = ref(getAdminToken())
defineEmits<{ close: []; save: [token: string] }>()
</script>

<template>
  <div class="modal-backdrop" @click.self="$emit('close')">
    <form class="modal" @submit.prevent="$emit('save', token)">
      <header><div class="modal-icon"><KeyRound :size="20" /></div><div><span>REGISTRY ACCESS</span><h2>管理令牌</h2></div><button class="icon-button" type="button" @click="$emit('close')"><X :size="18" /></button></header>
      <label><span>Bearer Token</span><input v-model="token" type="password" autocomplete="off" autofocus /></label>
      <p>令牌仅保存在当前浏览器标签页，不会写入Registry。</p>
      <footer><button class="button secondary" type="button" @click="$emit('close')">取消</button><button class="button primary" type="submit">应用</button></footer>
    </form>
  </div>
</template>
