<script setup lang="ts">
/**
 * kjgl→kjgl01 同步按钮（数据资源·目录页工具栏）
 *
 * 仅当前档位为 kjgl 时显示：点击后二次确认（DROP 重拷 kjgl01 全表），
 * 同步成功后 emit('synced') 交由父级刷新 Schema 图谱。
 */
import { onMounted, ref } from 'vue'
import { ElButton } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { ApiError } from '@/api'
import { confirm, toast } from '@/components'
import { syncKjgl01 } from '@/api/resources'
import { getDbSettings } from '@/api/settings'

const emit = defineEmits<{ synced: [] }>()

const visible = ref(false)
const syncing = ref(false)

onMounted(async () => {
  try {
    const res = await getDbSettings()
    visible.value = res.current_profile === 'kjgl'
  } catch {
    visible.value = false
  }
})

async function onSync() {
  const ok = await confirm.l2({
    title: '同步 kjgl → kjgl01',
    message: '从 kjgl 生产库全量重拷数据到 kjgl01 问数副本，并刷新 Schema 文档。',
    impacts: [
      'kjgl01 业务库全部表将被 DROP 后重建（问数副本，数秒内完成）',
      'kjgl 生产库全程只读；fz01 / sc01 不受影响',
      '同步期间针对 kjgl01 的问数查询可能短暂失败',
    ],
    confirmText: '开始同步',
  })
  if (!ok) return
  syncing.value = true
  try {
    const res = await syncKjgl01()
    if (!res.all_match) {
      toast.danger({
        title: '同步完成但行数不一致',
        desc: res.mismatched.map((m) => `${m.table}: 源 ${m.source} / 副本 ${m.target}`).join('；'),
      })
      return
    }
    toast.success(
      `同步完成：${res.table_count} 张表 · ${res.total_rows} 行 · 外键 ${res.fk_count} 个，`
      + `耗时 ${(res.duration_ms / 1000).toFixed(1)}s`,
    )
    emit('synced')
  } catch (e) {
    toast.danger({ title: '同步失败', desc: e instanceof ApiError ? e.message : String(e) })
  } finally {
    syncing.value = false
  }
}
</script>

<template>
  <ElButton
    v-if="visible"
    class="kjgl-sync"
    :icon="Refresh"
    :loading="syncing"
    @click="onSync"
  >
    同步 kjgl→kjgl01
  </ElButton>
</template>
