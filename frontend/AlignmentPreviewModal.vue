<template>
  <div class="preview-modal-overlay" @click.self="close">
    <div class="preview-modal">
      <div class="modal-header">
        <h3>📋 对齐结果预览</h3>
        <button class="close-btn" @click="close">&times;</button>
      </div>
      
      <div class="modal-body">
        <!-- 文件信息 -->
        <div class="file-info">
          <div class="info-item">
            <span class="label">批次ID:</span>
            <span class="value">{{ result.batch_id }}</span>
          </div>
          <div class="info-item">
            <span class="label">数据组数:</span>
            <span class="value">{{ result.summary.total_groups }} 组</span>
          </div>
          <div class="info-item">
            <span class="label">总行数:</span>
            <span class="value">{{ result.summary.total_rows }} 行</span>
          </div>
          <div class="info-item">
            <span class="label">模型数:</span>
            <span class="value">{{ result.summary.model_count }} 个</span>
          </div>
        </div>

        <!-- 模型信息 -->
        <div class="models-section">
          <h4>🤖 检测到的模型</h4>
          <div class="models-list">
            <div 
              v-for="(model, idx) in result.summary.models" 
              :key="idx"
              class="model-tag"
              :style="{ backgroundColor: getModelColor(idx) }"
            >
              {{ model.display_name }}
              <small>({{ model.tag }})</small>
            </div>
          </div>
        </div>

        <!-- 数据组列表 -->
        <div class="groups-section">
          <h4>📁 数据组详情</h4>
          <div class="groups-list">
            <div 
              v-for="group in result.groups" 
              :key="group.group_id"
              class="group-item"
              :class="{ expanded: expandedGroups.includes(group.group_id) }"
            >
              <div class="group-header" @click="toggleGroup(group.group_id)">
                <span class="expand-icon">{{ expandedGroups.includes(group.group_id) ? '▼' : '▶' }}</span>
                <span class="group-name">{{ group.group_id }}</span>
                <span class="group-rows">{{ group.row_count }} 行</span>
                <span class="group-files">{{ group.files.length }} 个文件</span>
              </div>
              
              <div v-if="expandedGroups.includes(group.group_id)" class="group-details">
                <div class="files-list">
                  <div v-for="file in group.files" :key="file" class="file-item">
                    📄 {{ file }}
                  </div>
                </div>
                
                <!-- 预览表格 -->
                <div class="preview-table-wrapper">
                  <table class="preview-table">
                    <thead>
                      <tr>
                        <th>ID</th>
                        <th>ASR原文</th>
                        <th v-for="model in result.summary.models" :key="model.tag">
                          {{ model.display_name }}
                        </th>
                        <th>上下文</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(row, idx) in getGroupPreview(group.group_id)" :key="idx">
                        <td class="cell-id">{{ row.sentence_id }}</td>
                        <td class="cell-asr" :title="row.asr_text">
                          {{ truncate(row.asr_text, 30) }}
                        </td>
                        <td 
                          v-for="model in result.summary.models" 
                          :key="model.tag"
                          class="cell-translation"
                          :title="row.translations?.[model.tag]"
                        >
                          <span v-if="row.translations?.[model.tag]" class="has-translation">
                            {{ truncate(row.translations[model.tag], 25) }}
                          </span>
                          <span v-else class="no-translation">—</span>
                        </td>
                        <td class="cell-context">
                          <span v-if="row.context_before" :title="row.context_before">
                            前{{ row.context_before.split('|').length }}句
                          </span>
                          <span v-else>—</span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                  <div v-if="group.row_count > 5" class="more-rows">
                    ... 还有 {{ group.row_count - 5 }} 行数据
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 对齐统计 -->
        <div class="stats-section">
          <h4>📊 对齐统计</h4>
          <div class="stats-grid">
            <div class="stat-item">
              <span class="stat-value">{{ result.summary.total_rows }}</span>
              <span class="stat-label">总行数</span>
            </div>
            <div class="stat-item">
              <span class="stat-value">{{ result.summary.model_count }}</span>
              <span class="stat-label">模型数</span>
            </div>
            <div class="stat-item">
              <span class="stat-value">{{ result.groups.length }}</span>
              <span class="stat-label">数据组</span>
            </div>
            <div class="stat-item">
              <span class="stat-value">{{ formatSize(result.output_files?.zip_size || 0) }}</span>
              <span class="stat-label">ZIP大小</span>
            </div>
          </div>
        </div>
      </div>

      <div class="modal-footer">
        <button class="btn-secondary" @click="close">取消</button>
        <button class="btn-danger" @click="realign">重新对齐</button>
        <button class="btn-primary" @click="confirm" :disabled="confirming">
          {{ confirming ? '导入中...' : '✓ 确认导入评测' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  name: 'AlignmentPreviewModal',
  
  props: {
    result: {
      type: Object,
      required: true
    }
  },
  
  data() {
    return {
      expandedGroups: [],
      confirming: false,
      previewData: {}
    }
  },
  
  mounted() {
    // 默认展开第一个组
    if (this.result.groups?.length > 0) {
      this.expandedGroups.push(this.result.groups[0].group_id)
      this.loadGroupPreview(this.result.groups[0].group_id)
    }
  },
  
  methods: {
    close() {
      this.$emit('close')
    },
    
    confirm() {
      this.confirming = true
      this.$emit('confirm', this.result)
    },
    
    realign() {
      this.$emit('realign')
    },
    
    toggleGroup(groupId) {
      const idx = this.expandedGroups.indexOf(groupId)
      if (idx > -1) {
        this.expandedGroups.splice(idx, 1)
      } else {
        this.expandedGroups.push(groupId)
        this.loadGroupPreview(groupId)
      }
    },
    
    async loadGroupPreview(groupId) {
      if (this.previewData[groupId]) return
      
      try {
        const response = await fetch(
          `/api/alignment/preview/${this.result.batch_id}/${groupId}?limit=5`
        )
        const data = await response.json()
        if (data.success) {
          this.$set(this.previewData, groupId, data.preview)
        }
      } catch (error) {
        console.error('加载预览失败:', error)
      }
    },
    
    getGroupPreview(groupId) {
      return this.previewData[groupId] || []
    },
    
    truncate(text, length) {
      if (!text) return ''
      const s = String(text).trim()
      if (s.toLowerCase() === 'nan') return ''
      return s.length > length ? s.slice(0, length) + '...' : s
    },
    
    formatSize(bytes) {
      if (bytes === 0) return '0 B'
      const k = 1024
      const sizes = ['B', 'KB', 'MB', 'GB']
      const i = Math.floor(Math.log(bytes) / Math.log(k))
      return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
    },
    
    getModelColor(idx) {
      const colors = ['#e3f2fd', '#f3e5f5', '#e8f5e9', '#fff3e0', '#fce4ec']
      return colors[idx % colors.length]
    }
  }
}
</script>

<style scoped>
.preview-modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 20px;
}

.preview-modal {
  background: white;
  border-radius: 12px;
  width: 90%;
  max-width: 1200px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.modal-header {
  padding: 20px 24px;
  border-bottom: 1px solid #e0e0e0;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.modal-header h3 {
  margin: 0;
  font-size: 1.25rem;
  color: #333;
}

.close-btn {
  background: none;
  border: none;
  font-size: 24px;
  cursor: pointer;
  color: #666;
  padding: 0;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  transition: all 0.2s;
}

.close-btn:hover {
  background: #f5f5f5;
  color: #333;
}

.modal-body {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.file-info {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
  padding: 16px;
  background: #f8f9fa;
  border-radius: 8px;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.info-item .label {
  font-size: 0.75rem;
  color: #666;
  text-transform: uppercase;
}

.info-item .value {
  font-size: 1rem;
  font-weight: 600;
  color: #333;
}

.models-section,
.groups-section,
.stats-section {
  margin-bottom: 24px;
}

.models-section h4,
.groups-section h4,
.stats-section h4 {
  margin: 0 0 12px 0;
  font-size: 1rem;
  color: #333;
}

.models-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.model-tag {
  padding: 6px 12px;
  border-radius: 16px;
  font-size: 0.875rem;
  font-weight: 500;
}

.model-tag small {
  opacity: 0.7;
  margin-left: 4px;
}

.groups-list {
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  overflow: hidden;
}

.group-item {
  border-bottom: 1px solid #e0e0e0;
}

.group-item:last-child {
  border-bottom: none;
}

.group-header {
  padding: 12px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  background: #fafafa;
  transition: background 0.2s;
}

.group-header:hover {
  background: #f0f0f0;
}

.expand-icon {
  font-size: 0.75rem;
  color: #666;
  width: 16px;
}

.group-name {
  flex: 1;
  font-weight: 500;
  color: #333;
}

.group-rows,
.group-files {
  font-size: 0.875rem;
  color: #666;
  background: #e0e0e0;
  padding: 2px 8px;
  border-radius: 4px;
}

.group-details {
  padding: 16px;
  background: white;
}

.files-list {
  margin-bottom: 16px;
  padding: 12px;
  background: #f8f9fa;
  border-radius: 6px;
}

.file-item {
  font-size: 0.875rem;
  color: #555;
  padding: 4px 0;
}

.preview-table-wrapper {
  overflow-x: auto;
}

.preview-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}

.preview-table th,
.preview-table td {
  padding: 10px 12px;
  text-align: left;
  border-bottom: 1px solid #e0e0e0;
}

.preview-table th {
  background: #f5f5f5;
  font-weight: 600;
  color: #333;
  white-space: nowrap;
}

.preview-table td {
  color: #555;
}

.cell-id {
  font-weight: 600;
  color: #1976d2;
}

.cell-asr,
.cell-translation {
  max-width: 300px;
  white-space: pre-wrap;
  word-break: break-word;
}

.no-translation {
  color: #999;
  font-style: italic;
}

.has-translation {
  color: #2e7d32;
}

.more-rows {
  text-align: center;
  padding: 12px;
  color: #666;
  font-size: 0.875rem;
  background: #f8f9fa;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}

.stat-item {
  text-align: center;
  padding: 20px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 8px;
  color: white;
}

.stat-value {
  display: block;
  font-size: 2rem;
  font-weight: 700;
}

.stat-label {
  display: block;
  font-size: 0.875rem;
  opacity: 0.9;
  margin-top: 4px;
}

.modal-footer {
  padding: 16px 24px;
  border-top: 1px solid #e0e0e0;
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

.btn-secondary,
.btn-danger,
.btn-primary {
  padding: 10px 20px;
  border-radius: 6px;
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  border: none;
}

.btn-secondary {
  background: #f5f5f5;
  color: #555;
}

.btn-secondary:hover {
  background: #e0e0e0;
}

.btn-danger {
  background: #ffebee;
  color: #c62828;
}

.btn-danger:hover {
  background: #ffcdd2;
}

.btn-primary {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

.btn-primary:hover:not(:disabled) {
  opacity: 0.9;
  transform: translateY(-1px);
}

.btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

@media (max-width: 768px) {
  .file-info,
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  
  .preview-modal {
    width: 95%;
    max-height: 95vh;
  }
}
</style>
