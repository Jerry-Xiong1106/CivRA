<script setup>
defineProps({
  field: { type: Object, required: true },
  modelValue: { type: String, default: '' },
  id: { type: String, default: 'answer' }
});
const emit = defineEmits(['update:modelValue']);
</script>
<template>
  <select
    v-if="field.kind === 'select'"
    :id="id"
    :name="id"
    :aria-label="field.label"
    :value="modelValue"
    required
    @change="emit('update:modelValue', $event.target.value)"
  >
    <option value="">请选择</option>
    <option v-for="(label, value) in field.options" :key="value" :value="value">
      {{ label }}
    </option>
  </select>
  <textarea
    v-else
    :id="id"
    :name="id"
    :aria-label="field.label"
    :value="modelValue"
    maxlength="5000"
    :placeholder="field.question"
    required
    @input="emit('update:modelValue', $event.target.value)"
  ></textarea>
</template>
