<template>
  <section class="page" data-module="report">
    <header class="page-head">
      <div>
        <h2>采集统计报表管理</h2>
        <p class="page-desc">维护采集统计的维度配置、周期设置与分发策略；每次生成都会冻结统计维度、统计周期、指标集合、数据来源与转换模板版本，模板后续变更只影响新报表。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记统计报表</button>
        <button class="btn" type="button" @click="exportRows">导出采集统计报表清单</button>
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

    <!-- 生成面板：选择模板，模板停用时必须重新选择 -->
    <div v-if="generation.reportId" class="panel">
      <div class="panel-head">
        <span class="section-title" style="margin: 0">
          生成报表快照 · {{ generation.reportCode }}（{{ generation.period }}）
        </span>
        <button class="btn ghost" type="button" @click="closeGeneration">取消</button>
      </div>
      <form class="filter-bar" style="margin-bottom: 0" @submit.prevent="submitGeneration">
        <label class="filter-item">
          <span>转换模板</span>
          <select v-model="generation.templateCode" class="inline-select">
            <option value="">请选择可用模板</option>
            <option
              v-for="tpl in templates"
              :key="String(tpl.id)"
              :value="tpl['模板编号']"
              :disabled="tpl.status !== '已启用'"
            >
              {{ tpl['模板编号'] }} · {{ tpl['模板版本'] }} · {{ tpl.status }}
            </option>
          </select>
        </label>
        <label class="filter-item" style="flex-direction: row; display: flex; align-items: center; gap: 6px">
          <input v-model="generation.simulateFailure" type="checkbox" />
          <span>模拟中途失败（回归用）</span>
        </label>
        <button class="btn primary" type="submit">生成快照</button>
      </form>
      <p v-if="generation.templateDisabled" class="snapshot-note">
        原引用的转换模板 {{ generation.reportTemplate }} 已停用：历史快照仍可查看，再次生成请重新选择可用模板。
      </p>
      <p v-if="generation.message" class="hint">{{ generation.message }}</p>
    </div>

    <!-- 视图 1：报表列表 -->
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
              <button class="link" type="button" @click="openGeneration(row)">生成报表</button>
              <button
                v-for="action in secondaryActions"
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
            <td :colspan="columns.length + 1" class="empty-state">暂无采集统计报表数据，可先登记统计报表</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- 视图 2：报表快照视图 -->
    <template v-else-if="activeTab === 'snapshots'">
      <form class="filter-bar" @submit.prevent="reloadSnapshots">
        <label class="filter-item">
          <span>统计周期</span>
          <select v-model="snapshotFilters.period" class="inline-select">
            <option value="">全部周期</option>
            <option v-for="period in board['统计周期'] ?? []" :key="period" :value="period">{{ period }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>模板版本</span>
          <input v-model="snapshotFilters.template_version" placeholder="如 v2" />
        </label>
        <label class="filter-item">
          <span>快照 / 报表编号</span>
          <input v-model="snapshotFilters.keyword" placeholder="按快照编号或报表编号检索" />
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetSnapshotFilters">重置条件</button>
      </form>

      <h3 class="section-title">按模板版本排列的历史快照（{{ snapshotTotal }} 份）</h3>
      <div class="snapshot-grid">
        <article
          v-for="group in visibleGroups"
          :key="`${group['模板编号']}-${group['模板版本']}`"
          class="snapshot-card"
        >
          <div class="snapshot-card-head">
            <span class="snapshot-card-title">{{ group['模板编号'] }}</span>
            <span class="version-tag">{{ group['模板版本'] }}</span>
          </div>
          <p class="snapshot-meta">来源冻结 · 共 {{ group['快照数量'] }} 份快照</p>
          <ul class="frozen-list">
            <li v-for="field in frozenFieldNames" :key="field">
              <span>{{ field }}</span>
              <span>{{ group['冻结字段'][field] ?? '—' }}</span>
            </li>
          </ul>
          <div
            v-for="snap in group['快照']"
            :key="String(snap.id)"
            class="snapshot-affected"
          >
            <button class="link" type="button" @click="openSnapshot(snap.id)">
              {{ snap['快照编号'] }} · {{ snap['报表编号'] }}
            </button>
            <span class="version-tag" :class="{ backfill: snap['冻结来源'] === '历史回填' }">
              {{ snap['冻结来源'] }}
            </span>
            <div>周期 {{ snap['统计周期'] }} · 生成于 {{ snap['生成时间'] }}</div>
            <div v-if="snap['冲突提示']" class="snapshot-note">{{ snap['冲突提示'] }}</div>
          </div>
        </article>
      </div>

      <h3 class="section-title">按生成时间倒序的历史版本</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>快照编号</th><th>报表编号</th><th>模板编号</th><th>模板版本</th>
            <th>统计周期</th><th>生成时间</th><th>快照状态</th><th>冻结来源</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="snap in visibleHistory" :key="String(snap.id)">
            <td><button class="link" type="button" @click="openSnapshot(snap.id)">{{ snap['快照编号'] }}</button></td>
            <td>{{ snap['报表编号'] }}</td>
            <td>{{ snap['模板编号'] }}</td>
            <td>{{ snap['模板版本'] }}</td>
            <td>{{ snap['统计周期'] }}</td>
            <td>{{ snap['生成时间'] }}</td>
            <td>{{ snap['快照状态'] }}</td>
            <td>{{ snap['冻结来源'] }}</td>
          </tr>
          <tr v-if="!visibleHistory.length">
            <td :colspan="8" class="empty-state">暂无报表快照，先生成一份统计报表</td>
          </tr>
        </tbody>
      </table>

      <div v-if="snapshotDetail" class="panel" style="margin-top: 12px">
        <div class="panel-head">
          <span class="section-title" style="margin: 0">
            {{ snapshotDetail['快照编号'] }} · {{ snapshotDetail['报表编号'] }}（{{ snapshotDetail['统计周期'] }}）
          </span>
          <button class="btn ghost" type="button" @click="snapshotDetail = null">关闭</button>
        </div>
        <p class="hint">
          冻结模板版本 {{ snapshotDetail['模板版本'] }} · 当前模板版本 {{ snapshotDetail['当前模板版本'] }} ·
          {{ snapshotDetail['冲突处理'] }}
        </p>
        <p v-if="snapshotDetail['模板已变更']" class="snapshot-note">{{ snapshotDetail['版本冲突说明'] }}</p>
        <p v-else class="hint">{{ snapshotDetail['版本冲突说明'] }}</p>
        <table class="data-table">
          <thead><tr><th>字段</th><th>生成时冻结值</th><th>当前模板值</th></tr></thead>
          <tbody>
            <tr v-for="field in frozenFieldNames" :key="field">
              <td>{{ field }}</td>
              <td>{{ snapshotDetail['冻结模板字段'][field] ?? '—' }}</td>
              <td>{{ snapshotDetail['当前模板字段'][field] ?? '—' }}</td>
            </tr>
            <tr v-for="field in ['统计维度', '统计周期', '指标集合', '数据来源', '生成时间']" :key="field">
              <td>{{ field }}</td>
              <td>{{ snapshotDetail[field] ?? '—' }}</td>
              <td>—</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- 视图 3：同一周期的时间线 -->
    <template v-else>
      <form class="filter-bar" @submit.prevent="reloadTimeline">
        <label class="filter-item">
          <span>统计周期</span>
          <select v-model="timelinePeriod" class="inline-select">
            <option value="">全部周期</option>
            <option v-for="period in board['统计周期'] ?? []" :key="period" :value="period">{{ period }}</option>
          </select>
        </label>
        <button class="btn" type="submit">切换时间线</button>
      </form>
      <div class="timeline">
        <div
          v-for="item in timeline"
          :key="String(item.id)"
          class="timeline-item"
          :class="timelineClass(item['结果'])"
        >
          <div class="timeline-title">
            {{ item['开始时间'] }} · {{ item['报表编号'] }} · {{ item['结果'] }}
          </div>
          <div class="timeline-body">
            模板 {{ item['模板编号'] || '—' }} {{ item['模板版本'] || '' }}
            <span v-if="item['快照编号']"> · 快照 {{ item['快照编号'] }}</span>
            <span v-if="item['被取代快照']"> · 取代 {{ item['被取代快照'] }}</span>
          </div>
          <div v-if="item['失败原因']" class="timeline-body error-text">{{ item['失败原因'] }}</div>
        </div>
        <div v-if="!timeline.length" class="empty-state">该周期暂无生成记录</div>
      </div>
    </template>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集统计报表记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="successMessage" class="ok-text">{{ successMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type Dict = Record<string, any>

const ENDPOINT = '/api/report'
const columns = ["报表编号", "统计维度", "统计周期", "指标集合", "数据来源", "生成时间", "报表格式", "分发方式", "模板编号", "模板版本"]
const secondaryActions = ["归档报表", "删除报表"]
const tabs = [
  { key: 'list', label: '报表列表' },
  { key: 'snapshots', label: '报表快照视图' },
  { key: 'timeline', label: '生成时间线' },
]
const frozenFieldNames = ["输入格式", "输出格式", "映射规则", "编码设置", "分隔符", "换行策略"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const successMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const activeTab = ref('list')
const templates = ref<Dict[]>([])
const board = ref<Dict>({})
const snapshotFilters = reactive({ period: '', template_version: '', keyword: '' })
const snapshotDetail = ref<Dict | null>(null)
const timeline = ref<Dict[]>([])
const timelinePeriod = ref('')
const stats = computed(() => [
  { label: '统计报表总数', value: total.value },
  { label: '已生成报表', value: rows.value.filter((row) => row.status === '已生成').length },
  { label: '报表快照', value: snapshotTotal.value },
])
const generation = reactive({
  reportId: 0,
  reportCode: '',
  reportTemplate: '',
  period: '',
  templateCode: '',
  simulateFailure: false,
  templateDisabled: false,
  message: '',
})

const snapshotTotal = computed(() => Number(board.value['快照总数'] ?? 0))
const visibleGroups = computed<Dict[]>(() => {
  const groups: Dict[] = board.value['版本分组'] ?? []
  return groups.filter((group) => {
    if (snapshotFilters.template_version && group['模板版本'] !== snapshotFilters.template_version) {
      return false
    }
    const keyword = snapshotFilters.keyword
    const snapshots: Dict[] = group['快照'] ?? []
    const matched = snapshots.filter((snap) => {
      if (snapshotFilters.period && snap['统计周期'] !== snapshotFilters.period) return false
      if (keyword && !String(snap['快照编号'] ?? '').includes(keyword)
        && !String(snap['报表编号'] ?? '').includes(keyword)) return false
      return true
    })
    return matched.length > 0
  }).map((group) => ({
    ...group,
    '快照': (group['快照'] ?? []).filter((snap: Dict) => {
      if (snapshotFilters.period && snap['统计周期'] !== snapshotFilters.period) return false
      const keyword = snapshotFilters.keyword
      if (keyword && !String(snap['快照编号'] ?? '').includes(keyword)
        && !String(snap['报表编号'] ?? '').includes(keyword)) return false
      return true
    }),
  }))
})
const visibleHistory = computed<Dict[]>(() =>
  (board.value['历史版本'] ?? []).filter((snap: Dict) => {
    if (snapshotFilters.period && snap['统计周期'] !== snapshotFilters.period) return false
    if (snapshotFilters.template_version && snap['模板版本'] !== snapshotFilters.template_version) return false
    const keyword = snapshotFilters.keyword
    if (keyword && !String(snap['快照编号'] ?? '').includes(keyword)
      && !String(snap['报表编号'] ?? '').includes(keyword)) return false
    return true
  }),
)

function timelineClass(result: string) {
  if (result === '成功') return 'success'
  if (result === '历史回填') return 'backfill'
  return 'failed'
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function resetSnapshotFilters() {
  snapshotFilters.period = ''
  snapshotFilters.template_version = ''
  snapshotFilters.keyword = ''
  void loadBoard()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '统计报表登记入口尚未接入审批流'
}

function switchTab(tab: string) {
  activeTab.value = tab
  if (tab === 'snapshots') void loadBoard()
  if (tab === 'timeline') void loadTimeline()
}

async function loadTemplates() {
  try {
    const response = await request('/api/transform?size=200')
    if (!response.ok) return
    const payload = await response.json()
    templates.value = payload.items ?? []
  } catch {
    templates.value = []
  }
}

function openGeneration(row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  if (row.status === '已归档') {
    errorMessage.value = '已归档报表不能重新生成，请先恢复或登记新的统计报表'
    return
  }
  generation.reportId = Number(row.id)
  generation.reportCode = String(row['报表编号'] ?? '')
  generation.reportTemplate = String(row['模板编号'] ?? '')
  generation.period = String(row['统计周期'] ?? '')
  generation.templateCode = generation.reportTemplate
  generation.simulateFailure = false
  generation.message = ''
  const current = templates.value.find((item) => item['模板编号'] === generation.reportTemplate)
  generation.templateDisabled = (current?.status ?? '') === '已停用' || !current
  if (generation.templateDisabled) {
    generation.templateCode = ''
    generation.message = current
      ? `转换模板 ${generation.reportTemplate} 已停用，请重新选择可用模板`
      : `转换模板 ${generation.reportTemplate || '（未选择）'} 不可用，请重新选择可用模板`
  } else if (current?.status !== '已启用') {
    generation.message = `转换模板 ${generation.reportTemplate} 当前为${current?.status}，生成前请先启用或重新选择`
  }
}

function closeGeneration() {
  generation.reportId = 0
  generation.message = ''
}

async function submitGeneration() {
  if (!generation.templateCode) {
    generation.message = '请先选择一个可用模板再生成'
    return
  }
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${generation.reportId}/actions`, {
      method: 'POST',
      body: JSON.stringify({
        action: '生成报表',
        values: {
          action: '生成报表',
          '模板编号': generation.templateCode,
          '模拟失败': generation.simulateFailure,
        },
      }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '统计报表生成失败')
    }
    successMessage.value = payload.message
    closeGeneration()
    await reload()
    await loadTemplates()
    if (activeTab.value === 'snapshots') await loadBoard()
    if (activeTab.value === 'timeline') await loadTimeline()
  } catch (error) {
    generation.message = error instanceof Error ? error.message : '统计报表生成失败'
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
      throw new Error(payload.message || '采集统计报表动作未生效，请稍后重试')
    }
    successMessage.value = payload.message
    await reload()
    if (activeTab.value === 'snapshots') await loadBoard()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集统计报表操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('统计报表列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集统计报表列表读取失败'
  }
}

async function loadBoard() {
  try {
    const response = await request(`${ENDPOINT}/snapshots/board`)
    if (!response.ok) throw new Error('报表快照看板读取失败')
    board.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报表快照看板读取失败'
  }
}

async function reloadSnapshots() {
  await loadBoard()
}

async function openSnapshot(snapshotId: number) {
  try {
    const response = await request(`${ENDPOINT}/snapshots/${snapshotId}`)
    if (!response.ok) throw new Error('报表快照读取失败')
    snapshotDetail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报表快照读取失败'
  }
}

async function loadTimeline() {
  const query = timelinePeriod.value ? `?period=${encodeURIComponent(timelinePeriod.value)}` : ''
  try {
    const response = await request(`${ENDPOINT}/timeline${query}`)
    if (!response.ok) throw new Error('生成时间线读取失败')
    const payload = await response.json()
    timeline.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '生成时间线读取失败'
  }
}

async function reloadTimeline() {
  await loadTimeline()
}

onMounted(() => {
  void reload()
  void loadTemplates()
  void loadBoard()
})
</script>
