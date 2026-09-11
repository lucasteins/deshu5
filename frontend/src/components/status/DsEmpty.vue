<script setup lang="ts">
/**
 * 空态三型（设计稿 §4.4.3）：
 *   no-data       初次无数据：图标 + 标题 + 说明 + 主操作（+ 可选次级链接）
 *   no-result     筛选后无结果：展示已应用筛选 chips + 「清空筛选」
 *   no-permission 无权限：说明 + 「申请权限」
 * 三型必须给出「下一步动作」；禁止只写「暂无数据」。
 */
import { computed } from 'vue'
import { ElButton } from 'element-plus'

const props = withDefaults(
  defineProps<{
    type?: 'no-data' | 'no-result' | 'no-permission'
    title?: string
    desc?: string
    actionText?: string
    /** no-result：已应用的筛选标签（chips） */
    filters?: string[]
    /** 次级链接文案（可选） */
    linkText?: string
  }>(),
  { type: 'no-data' },
)

const emit = defineEmits<{ action: []; clear: []; link: [] }>()

const DEFAULTS = {
  'no-data': {
    title: '这里还没有内容',
    desc: '创建第一条记录后，它会出现在这里',
    actionText: '新建',
  },
  'no-result': {
    title: '没有符合条件的记录',
    desc: '尝试减少筛选条件，或清空筛选重新查看',
    actionText: '清空筛选',
  },
  'no-permission': {
    title: '没有访问权限',
    desc: '你需要相应权限才能查看此内容，可联系管理员申请',
    actionText: '申请权限',
  },
} as const

const view = computed(() => {
  const d = DEFAULTS[props.type]
  return {
    title: props.title ?? d.title,
    desc: props.desc ?? d.desc,
    actionText: props.actionText ?? d.actionText,
  }
})

function onPrimary() {
  if (props.type === 'no-result') emit('clear')
  else emit('action')
}
</script>

<template>
  <div class="ds-empty" :class="`ds-empty--${type}`">
    <svg class="ds-empty__icon" aria-hidden="true" viewBox="0 0 64 64" fill="none">
      <template v-if="type === 'no-data'">
        <path
          d="M12 24l8-12h24l8 12v20a4 4 0 0 1-4 4H16a4 4 0 0 1-4-4V24Z"
          stroke="currentColor"
          stroke-width="2"
          stroke-linejoin="round"
        />
        <path d="M12 24h12l4 6h8l4-6h12" stroke="currentColor" stroke-width="2" stroke-linejoin="round" />
      </template>
      <template v-else-if="type === 'no-result'">
        <circle cx="28" cy="28" r="14" stroke="currentColor" stroke-width="2" />
        <path d="M38.5 38.5L52 52" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
        <path d="M23 28h10" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
      </template>
      <template v-else>
        <rect x="18" y="28" width="28" height="22" rx="4" stroke="currentColor" stroke-width="2" />
        <path d="M24 28v-6a8 8 0 0 1 16 0v6" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
      </template>
    </svg>

    <h3 class="ds-empty__title">{{ view.title }}</h3>
    <p v-if="view.desc" class="ds-empty__desc">{{ view.desc }}</p>

    <div v-if="type === 'no-result' && filters?.length" class="ds-empty__chips">
      <span v-for="(chip, i) in filters" :key="i" class="ds-empty__chip">{{ chip }}</span>
    </div>

    <div class="ds-empty__actions">
      <ElButton type="primary" @click="onPrimary">{{ view.actionText }}</ElButton>
      <button v-if="linkText" class="ds-empty__link" type="button" @click="emit('link')">
        {{ linkText }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.ds-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: var(--sp-10) var(--sp-6);
}
.ds-empty__icon {
  width: 64px;
  height: 64px;
  color: var(--text-4);
}
.ds-empty__title {
  margin: var(--sp-4) 0 0;
  font-size: var(--fs-h2); /* 16 / 600 */
  font-weight: var(--fw-h2);
  line-height: var(--lh-h2);
  color: var(--text-1);
}
.ds-empty__desc {
  margin: var(--sp-2) 0 0;
  max-width: 420px;
  font-size: var(--fs-body-sm); /* 13 */
  line-height: var(--lh-body-sm);
  color: var(--text-2);
}
.ds-empty__chips {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--sp-2);
  margin-top: var(--sp-3);
}
.ds-empty__chip {
  padding: 2px 10px;
  background: var(--sunken);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-full);
  font-size: var(--fs-caption); /* 12 */
  line-height: var(--lh-caption);
  color: var(--text-2);
}
.ds-empty__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  margin-top: var(--sp-5);
}
.ds-empty__link {
  padding: 0;
  border: none;
  background: none;
  font-size: var(--fs-body-sm);
  color: var(--accent);
  cursor: pointer;
}
.ds-empty__link:hover {
  text-decoration: underline;
}
</style>
