<template>
  <section class="page" data-module="audit">
    <header class="page-head">
      <div>
        <h2>采集审计日志管理</h2>
        <p class="page-desc">维护采集审计的操作类型、执行记录与复核状态，为采集合规审计提供可追溯的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记审计记录</button>
        <button class="btn" type="button" @click="exportRows">导出采集审计日志清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="btn"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </div>

    <template v-if="activeTab === 'list'">
    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无采集审计日志数据，可先登记审计记录</td>
        </tr>
      </tbody>
    </table>
    </template>

    <template v-else-if="activeTab === 'archive'">
      <div class="panel">
        <div class="panel-head">
          <span class="section-title" style="margin: 0">采集审计记录生成的待归档清单</span>
          <span class="hint">
            待归档 {{ archiveStats['待归档'] ?? 0 }} · 已归档 {{ archiveStats['已归档'] ?? 0 }} ·
            已取代 {{ archiveStats['已取代'] ?? 0 }}
          </span>
        </div>
        <table class="data-table">
          <thead>
            <tr>
              <th>清单编号</th><th>报表编号</th><th>快照编号</th><th>统计周期</th>
              <th>模板编号</th><th>模板版本</th><th>生成时间</th><th>状态</th><th>回写状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in archives" :key="String(item.id)">
              <td>{{ item['清单编号'] }}</td>
              <td>{{ item['报表编号'] }}</td>
              <td>{{ item['快照编号'] }}</td>
              <td>{{ item['统计周期'] }}</td>
              <td>{{ item['模板编号'] }}</td>
              <td>{{ item['模板版本'] }}</td>
              <td>{{ item['生成时间'] }}</td>
              <td>{{ item['状态'] }}</td>
              <td>{{ item['回写状态'] }}<span v-if="item['回写结果']"> · {{ item['回写结果'] }}</span></td>
            </tr>
            <tr v-if="!archives.length">
              <td colspan="9" class="empty-state">暂无待归档清单，先生成一份统计报表快照</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <template v-else>
      <div class="panel">
        <div class="section-title" style="margin-top: 0">生成结果回写到审计清单</div>
        <table class="data-table">
          <thead>
            <tr>
              <th>记录编号</th><th>采集计划</th><th>执行人</th><th>执行时间</th>
              <th>结果摘要</th><th>清单编号</th><th>清单状态</th><th>回写结果</th>
              <th>快照编号</th><th>模板版本</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in backwrites" :key="String(item['审计记录id'])">
              <td>{{ item['记录编号'] }}</td>
              <td>{{ item['采集计划'] }}</td>
              <td>{{ item['执行人'] }}</td>
              <td>{{ item['执行时间'] }}</td>
              <td>{{ item['结果摘要'] }}</td>
              <td>{{ item['清单编号'] || '—' }}</td>
              <td>{{ item['清单状态'] }}</td>
              <td>{{ item['回写结果'] }}</td>
              <td>{{ item['快照编号'] || '—' }}</td>
              <td>{{ item['模板版本'] || '—' }}</td>
            </tr>
            <tr v-if="!backwrites.length">
              <td colspan="10" class="empty-state">暂无统计报表生成记录</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集审计日志记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/audit'
const columns = ["记录编号", "操作类型", "采集计划", "执行人", "执行时间", "结果摘要", "变更内容", "复核状态"]
const actions = ["标记复核", "归档记录"]
const statuses = ["已记录", "已复核", "已归档"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const tabs = [
  { key: 'list', label: '审计记录' },
  { key: 'archive', label: '待归档清单' },
  { key: 'backwrite', label: '回写结果' },
]
const activeTab = ref('list')
const archives = ref<Row[]>([])
const archiveStats = ref<Record<string, number>>({})
const backwrites = ref<Row[]>([])
const stats = computed(() => [
  { label: '审计记录总数', value: total.value },
  { label: '已复核记录', value: rows.value.filter((row) => row.status === '已复核').length },
  { label: '待归档清单', value: archiveStats.value['待归档'] ?? 0 },
])

function switchTab(tab: string) {
  activeTab.value = tab
  if (tab === 'archive') void loadArchives()
  if (tab === 'backwrite') void loadBackwrites()
}

async function loadArchives() {
  try {
    const response = await request(`${ENDPOINT}/archive-clause`)
    if (!response.ok) throw new Error('待归档清单读取失败')
    const payload = await response.json()
    archives.value = payload.items ?? []
    archiveStats.value = payload['统计'] ?? {}
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '待归档清单读取失败'
  }
}

async function loadBackwrites() {
  try {
    const response = await request(`${ENDPOINT}/backwrite`)
    if (!response.ok) throw new Error('审计清单回写结果读取失败')
    const payload = await response.json()
    backwrites.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '审计清单回写结果读取失败'
  }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '审计记录登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('采集审计日志动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集审计日志操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('审计记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集审计日志列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadArchives()
})
</script>
