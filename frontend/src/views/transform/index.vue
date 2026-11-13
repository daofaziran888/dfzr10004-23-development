<template>
  <section class="page" data-module="transform">
    <header class="page-head">
      <div>
        <h2>采集格式转换管理</h2>
        <p class="page-desc">维护采集格式转换的输入输出映射、编码配置与模板管理；修改映射规则会升模板版本，清单同时显示模板被哪些统计报表引用。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记转换模板</button>
        <button class="btn" type="button" @click="exportRows">导出采集格式转换清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="panel">
      <div class="section-title" style="margin-top: 0">模板被报表引用情况</div>
      <div class="reference-list">
        <div v-for="item in references" :key="String(item['模板id'])">
          <strong>{{ item['模板编号'] }} · {{ item['模板版本'] }}</strong>
          <span>（{{ item['模板状态'] }}）被 {{ item['引用报表数'] }} 条统计报表引用</span>
          <span v-for="report in item['引用报表']" :key="`${item['模板编号']}-${report['报表编号']}`">
            · {{ report['报表编号'] }}（{{ report['统计周期'] }}，{{ report['引用方式'] }}，快照版本
            {{ (report['快照版本'] ?? []).join('、') || '—' }}）
          </span>
        </div>
      </div>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>引用报表</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="reference-list">
            <div v-if="Number(row['引用报表数'] ?? 0) === 0">未被引用</div>
            <div v-for="report in row['引用报表'] ?? []" :key="String(report['报表编号'])">
              {{ report['报表编号'] }}（{{ report['报表状态'] }}）
            </div>
          </td>
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
            <button class="link" type="button" @click="openMapping(row)">更新映射规则</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无采集格式转换数据，可先登记转换模板</td>
        </tr>
      </tbody>
    </table>

    <div v-if="mapping.editing" class="panel" style="margin-top: 12px">
      <div class="panel-head">
        <span class="section-title" style="margin: 0">更新映射规则 · {{ mapping.templateCode }}</span>
        <button class="btn ghost" type="button" @click="mapping.editing = false">取消</button>
      </div>
      <form class="filter-bar" @submit.prevent="submitMapping">
        <label class="filter-item" style="flex: 1">
          <span>映射规则（保存后模板版本递增）</span>
          <input v-model="mapping.value" style="width: 100%" />
        </label>
        <button class="btn primary" type="submit">保存并升版</button>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集格式转换记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="successMessage" class="ok-text">{{ successMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>
type Dict = Record<string, any>

const ENDPOINT = '/api/transform'
const columns = ["模板编号", "输入格式", "输出格式", "映射规则", "编码设置", "分隔符", "换行策略", "是否启用", "模板版本"]
const actions = ["提交校验", "启用模板", "停用模板"]
const statuses = ["草稿", "待校验", "已启用", "已停用"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const successMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const references = ref<Dict[]>([])
const mapping = reactive({ editing: false, templateId: 0, templateCode: '', value: '' })

const stats = computed(() => [
  { label: '转换模板总数', value: total.value },
  { label: '待校验模板', value: rows.value.filter((row) => row.status === '待校验').length },
  { label: '已启用模板', value: rows.value.filter((row) => row.status === '已启用').length },
  { label: '被引用模板', value: references.value.filter((item) => Number(item['引用报表数'] ?? 0) > 0).length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '转换模板登记入口尚未接入审批流'
}

function openMapping(row: Row) {
  mapping.editing = true
  mapping.templateId = Number(row.id)
  mapping.templateCode = String(row['模板编号'] ?? '')
  mapping.value = String(row['映射规则'] ?? '')
}

async function submitMapping() {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${mapping.templateId}/actions`, {
      method: 'POST',
      body: JSON.stringify({
        action: '更新映射规则',
        values: { action: '更新映射规则', '映射规则': mapping.value },
      }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '映射规则更新失败')
    successMessage.value = payload.message
    mapping.editing = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '映射规则更新失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action, values: { action } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '采集格式转换动作未生效，请稍后重试')
    }
    successMessage.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集格式转换操作失败'
  }
}

async function loadReferences() {
  try {
    const response = await request(`${ENDPOINT}/references`)
    if (!response.ok) throw new Error('模板引用清单读取失败')
    const payload = await response.json()
    references.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '模板引用清单读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('转换模板列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await loadReferences()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集格式转换列表读取失败'
  }
}

onMounted(reload)
</script>
