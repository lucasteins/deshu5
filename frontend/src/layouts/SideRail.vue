<script setup lang="ts">
/**
 * 侧栏（SideRail）—— F0.3 布局壳
 *
 * 依据设计稿 §4.4.6「左侧导航」：
 * - 宽 240px，可折叠至 64px（折叠后 hover 弹出浮层标签，延迟 300ms）
 * - 3 分组标签（micro 字阶 + --text-4）
 * - 当前态三重编码：左侧 3px --accent 指示条 + --accent-soft 底色 + 文字 600 字重
 * 全部尺寸 / 颜色走 token（tokens.css），不硬编码。
 */
import { useRoute } from 'vue-router'
import { openSettings } from '@/components'
import { NAV_GROUPS } from './navigation'

defineProps<{ collapsed: boolean }>()
defineEmits<{ toggleCollapse: [] }>()

const route = useRoute()
</script>

<template>
  <aside class="rail" :class="{ 'is-collapsed': collapsed }">
    <!-- 品牌区（56px，与顶栏等高） -->
    <div class="rail-top">
      <div class="logo-mark" aria-hidden="true">d5</div>
      <div v-if="!collapsed" class="logo-text">
        <b>deshu5</b>
        <span>智能问数训练系统</span>
      </div>
    </div>

    <!-- 导航（3 分组） -->
    <nav class="rail-scroll" aria-label="主导航">
      <div v-for="group in NAV_GROUPS" :key="group.key" class="nav-group">
        <div v-if="!collapsed" class="nav-group-label">{{ group.label }}</div>
        <div v-else class="nav-group-divider" aria-hidden="true" />

        <el-tooltip
          v-for="item in group.items"
          :key="item.name"
          :content="item.label"
          placement="right"
          :show-after="300"
          :disabled="!collapsed"
        >
          <RouterLink
            :to="item.path"
            class="nav-item"
            :class="{ 'is-active': route.name === item.name }"
            :title="collapsed ? undefined : item.label"
          >
            <el-icon class="nav-ico"><component :is="item.icon" /></el-icon>
            <template v-if="!collapsed">
              <span class="nav-label">{{ item.label }}</span>
              <span v-if="item.count" class="nav-count tabular">{{ item.count }}</span>
            </template>
          </RouterLink>
        </el-tooltip>
      </div>
    </nav>

    <!-- 底部：用户 + 折叠开关 -->
    <div class="rail-foot">
      <!-- 登录功能未接入，先以 admin 占位（2026-09-11 皮卡丘目验后调整） -->
      <div class="avatar" aria-hidden="true">A</div>
      <div v-if="!collapsed" class="who">
        <b>admin</b>
        <span>数据治理组</span>
      </div>
      <el-tooltip content="设置" placement="right" :show-after="300" :disabled="!collapsed">
        <button
          class="settings-btn"
          type="button"
          aria-label="设置"
          @click="openSettings"
        >
          <el-icon><component :is="'Setting'" /></el-icon>
          <span v-if="!collapsed" class="settings-btn__label">设置</span>
        </button>
      </el-tooltip>
      <el-tooltip :content="collapsed ? '展开侧栏' : '折叠侧栏'" placement="right" :show-after="300">
        <button
          class="collapse-btn"
          type="button"
          :aria-label="collapsed ? '展开侧栏' : '折叠侧栏'"
          :aria-expanded="!collapsed"
          @click="$emit('toggleCollapse')"
        >
          <el-icon><component :is="collapsed ? 'Expand' : 'Fold'" /></el-icon>
        </button>
      </el-tooltip>
    </div>
  </aside>
</template>

<style scoped>
.rail {
  width: 240px;
  flex: none;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--surface-1);
  border-right: 1px solid var(--line);
  transition: width var(--dur-base) var(--ease-standard);
}
.rail.is-collapsed {
  width: 64px;
}

/* 品牌区 */
.rail-top {
  height: 56px;
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 var(--sp-4);
  border-bottom: 1px solid var(--line-subtle);
}
.rail.is-collapsed .rail-top {
  justify-content: center;
  padding: 0;
}
.logo-mark {
  width: 26px;
  height: 26px;
  flex: none;
  display: grid;
  place-items: center;
  border-radius: var(--r-sm);
  background: var(--accent);
  color: var(--text-on-accent);
  font-family: var(--font-mono);
  font-size: var(--fs-micro);
  font-weight: 700;
  letter-spacing: -0.02em;
}
.logo-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.15;
}
.logo-text b {
  font-size: var(--fs-body-lg);
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--text-strong);
}
.logo-text span {
  font-size: var(--fs-micro);
  color: var(--text-3);
  letter-spacing: 0.02em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 导航区 */
.rail-scroll {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: var(--sp-3) var(--sp-2) var(--sp-4);
}
.nav-group {
  margin-bottom: var(--sp-1);
}
.nav-group-label {
  padding: var(--sp-3) 10px 6px;
  font-size: var(--fs-micro);
  font-weight: 500;
  letter-spacing: 0.06em;
  color: var(--text-4);
}
.nav-group-divider {
  height: 1px;
  margin: var(--sp-3) 8px var(--sp-2);
  background: var(--line-subtle);
}

/* 导航项 */
.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 34px;
  padding: 0 10px;
  border-radius: var(--r-sm);
  font-size: var(--fs-body-sm);
  color: var(--text-2);
  text-decoration: none;
  transition:
    background-color var(--dur-fast) var(--ease-standard),
    color var(--dur-fast) var(--ease-standard);
}
.nav-item:hover {
  background: var(--surface-2);
  color: var(--text-1);
}
.nav-ico {
  flex: none;
  font-size: 16px;
  opacity: 0.9;
}
.nav-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.nav-count {
  font-family: var(--font-mono);
  font-size: var(--fs-micro);
  color: var(--text-3);
}

/* 当前态三重编码：指示条 + 底色 + 字重 */
.nav-item.is-active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}
.nav-item.is-active::before {
  content: '';
  position: absolute;
  left: -8px;
  top: 6px;
  bottom: 6px;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: var(--accent);
}

/* 折叠态：仅图标居中 */
.rail.is-collapsed .nav-item {
  justify-content: center;
  padding: 0;
}
.rail.is-collapsed .nav-item.is-active::before {
  left: -8px;
}

/* 底部 */
.rail-foot {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px var(--sp-3);
  border-top: 1px solid var(--line-subtle);
}
.rail.is-collapsed .rail-foot {
  flex-direction: column;
  padding: 10px 0;
  gap: var(--sp-2);
}
.avatar {
  width: 28px;
  height: 28px;
  flex: none;
  display: grid;
  place-items: center;
  border-radius: var(--r-full);
  background: var(--sunken);
  border: 1px solid var(--line);
  font-size: var(--fs-micro);
  font-weight: 600;
  color: var(--text-2);
}
.who {
  flex: 1;
  min-width: 0;
  line-height: 1.2;
}
.who b {
  display: block;
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--text-1);
}
.who span {
  font-size: var(--fs-micro);
  color: var(--text-3);
}
.settings-btn {
  flex: none;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-3);
  font-size: 16px;
  cursor: pointer;
  transition:
    background-color var(--dur-fast) var(--ease-standard),
    color var(--dur-fast) var(--ease-standard);
}
.settings-btn:hover {
  background: var(--surface-2);
  color: var(--text-1);
}
.settings-btn:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}
.settings-btn__label {
  font-size: var(--fs-body-sm);
  white-space: nowrap;
}
.rail.is-collapsed .settings-btn {
  width: 28px;
  padding: 0;
  justify-content: center;
}
.collapse-btn {
  width: 28px;
  height: 28px;
  flex: none;
  display: grid;
  place-items: center;
  border: 1px solid transparent;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-3);
  font-size: 16px;
  cursor: pointer;
  transition:
    background-color var(--dur-fast) var(--ease-standard),
    color var(--dur-fast) var(--ease-standard);
}
.collapse-btn:hover {
  background: var(--surface-2);
  color: var(--text-1);
}
.collapse-btn:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}
</style>
