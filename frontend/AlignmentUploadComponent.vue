<template>
  <div class="alignment-upload-component">
    <!-- 上传区域 -->
    <div class="upload-section">
      <h3>📁 上传评测数据</h3>
      
      <!-- 对齐模式开关 -->
      <div class="alignment-toggle">
        <label class="toggle-label">
          <input 
            type="checkbox" 
            v-model="enableAlignment"
            @change="onAlignmentToggle"
          />
          <span class="toggle-text">启用多模型对齐模式</span>
          <span class="toggle-hint">（自动识别同一数据的多模型输出并对齐）</span>
        </label>
      </div>

      <!-- 模型配置（对齐模式开启时显示） -->
      <div v-if="enableAlignment" class="model-config">
        <h4>模型识别配置</h4>
        <div class="model-tags-input">
          <label>模型标记（从文件名识别）：</label>
          <div class="tags-list">
            <span 
              v-for="(tag, index) in modelTags" 
              :key="index"
              class="tag"
            >
              {{ tag }}
              <button @click="removeTag(index)">×</button>
            </span>
            <input 
              v-model="newTag"
              @keyup.enter="addTag"
              placeholder="输入标记如 e2e 按回车"
              class="tag-input"
            />
          </div>
        </div>
        <div class="model-names-input">
          <label>模型显示名称（可选）：</label>
          <input 
            v-model="modelNamesInput"
            placeholder="如: 端到端模型,Pipeline模型"
            class="names-input"
          />
        </div>
      </div>

      <!-- 文件上传 -->
      <div class="upload-area" 
           @drop.prevent="handleDrop"
           @dragover.prevent
           @click="triggerFileInput"
           :class="{ 'drag-over': isDragging, 'has-files': files.length > 0 }"
      >
        <input 
          ref="fileInput"
          type="file"
          webkitdirectory
          directory
          multiple
          @change="handleFileSelect"
          class="hidden-input"
        />
        <input 
          ref="zipInput"
          type="file"
          accept=".zip"
          @change="handleZipSelect"
          class="hidden-input"
        />
        
        <div v-if="files.length === 0" class="upload-placeholder">
          <div class="upload-icon">📂</div>
          <p>点击选择文件夹 或 拖拽文件/压缩包到此处</p>
          <p class="upload-hint">支持：文件夹、.zip压缩包、多文件选择</p>
          <div class="upload-buttons">
            <button @click.stop="triggerFolderSelect" class="btn-secondary">
              选择文件夹
            </button>
            <button @click.stop="triggerZipSelect" class="btn-secondary">
              选择ZIP
            </button>
          </div>
        </div>
        
        <div v-else class="files-preview">
          <div class="files-header">
            <span>已选择 {{ files.length }} 个文件</span>
            <button @click.stop="clearFiles" class="btn-text">清空</button>
          </div>
          <div class="files-list">
            <div v-for="(file, index) in previewFiles" :key="index" class="file-item">
              <span class="file-icon">{{ getFileIcon(file.name) }}</span>
              <span class="file-name" :title="file.name">{{ file.name }}</span>
              <span class="file-size">{{ formatSize(file.size) }}</span>
            </div>
            <div v-if="files.length > previewLimit" class="more-files">
              还有 {{ files.length - previewLimit }} 个文件...
            </div>
          </div>
        </div>
      </div>

      <!-- 上传按钮 -->
      <div class="upload-actions">
        <button 
          @click="uploadFiles"
          :disabled="files.length === 0 || isUploading"
          class="btn-primary"
        >
          <span v-if="isUploading">
            <i class="spinner"></i> 处理中...
          </span>
          <span v-else>
            {{ enableAlignment ? '对齐并预览' : '直接上传' }}
          </span>
        </button>
      </div>
    </div>

    <!-- 对齐结果预览 -->
    <div v-if="alignmentResult" class="preview-section">
      <h3>📊 对齐结果预览</h3>
      
      <!-- 统计信息 -->
      <div class="stats-cards">
        <div class="stat-card">
          <div class="stat-value">{{ alignmentResult.stats.total_groups }}</div>
          <div class="stat-label">数据组数</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ alignmentResult.stats.total_rows }}</div>
          <div class="stat-label">总行数</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ alignmentResult.stats.models_detected.length }}</div>
          <div class="stat-label">检测到模型</div>
        </div>
      </div>

      <!-- 模型信息 -->
      <div class="models-info">
        <h4>检测到的模型</h4>
        <div class="model-badges">
          <span 
            v-for="model in alignmentResult.stats.models_detected" 
            :key="model"
            class="model-badge"
          >
            {{ model }}
          </span>
        </div>
      </div>

      <!-- 数据组列表 -->
      <div class="groups-list">
        <h4>数据组详情</h4>
        <div 
          v-for="group in alignmentResult.groups" 
          :key="group.group_id"
          class="group-item"
          :class="{ 'has-error': group.errors.length > 0 }"
        >
          <div class="group-header" @click="toggleGroup(group.group_id)">
            <span class="toggle-icon">{{ expandedGroups.includes(group.group_id) ? '▼' : '▶' }}</span>
            <span class="group-id">{{ group.group_id }}</span>
            <span class="group-rows">{{ group.row_count }} 行</span>
            <span v-if="group.errors.length > 0" class="error-badge">
              {{ group.errors.length }} 个警告
            </span>
          </div>
          
          <div v-if="expandedGroups.includes(group.group_id)" class="group-details">
            <!-- 文件列表 -->
            <div class="group-files">
              <div v-for="(file, model) in group.files" :key="model" class="file-row">
                <span class="model-label">{{ model }}:</span>
                <span class="file-path">{{ file }}</span>
              </div>
            </div>
            
            <!-- 错误信息 -->
            <div v-if="group.errors.length > 0" class="group-errors">
              <div v-for="(error, idx) in group.errors" :key="idx" class="error-item">
                ⚠️ {{ error }}
              </div>
            </div>
            
            <!-- 数据预览 -->
            <div class="data-preview">
              <button @click="loadPreviewData(group.group_id)" class="btn-text">
                加载数据预览
              </button>
              <div v-if="previewData[group.group_id]" class="preview-table-wrapper">
                <table class="preview-table">
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>ASR原文</th>
                      <th v-for="model in alignmentResult.stats.models_detected" :key="model">
                        {{ model }}译文
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in previewData[group.group_id].slice(0, 5)" :key="row.sentence_id">
                      <td>{{ row.sentence_id }}</td>
                      <td>{{ cleanValue(row.asr_text) }}</td>
                      <td v-for="model in alignmentResult.stats.models_detected" :key="model"
                          :class="{ 'empty-cell': !cleanValue(row[`${model}_translation`]) }"
                      >
                        {{ cleanValue(row[`${model}_translation`]) || '-' }}
                      </td>
                    </tr>
                  </tbody>
                </table>
                <div v-if="previewData[group.group_id].length > 5" class="more-rows">
                  还有 {{ previewData[group.group_id].length - 5 }} 行...
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 操作按钮 -->
      <div class="preview-actions">
        <button @click="cancelAlignment" class="btn-secondary">
          取消
        </button>
        <button @click="confirmAndImport" :disabled="isImporting" class="btn-primary">
          <span v-if="isImporting">
            <i class="spinner"></i> 导入中...
          </span>
          <span v-else>
            ✓ 确认并导入评测
          </span>
        </button>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  name: 'AlignmentUploadComponent',
  
  data() {
    return {
      // 对齐配置
      enableAlignment: false,
      modelTags: ['e2e', 'pipeline', 'baseline'],
      modelNamesInput: '',
      newTag: '',
      
      // 文件上传
      files: [],
      isDragging: false,
      isUploading: false,
      previewLimit: 10,
      
      // 对齐结果
      alignmentResult: null,
      expandedGroups: [],
      previewData: {},
      isImporting: false,
      
      // 临时存储的对齐结果ID
      tempAlignmentId: null
    }
  },
  
  computed: {
    previewFiles() {
      return this.files.slice(0, this.previewLimit)
    },
    
    modelNames() {
      return this.modelNamesInput.split(',').map(n => n.trim()).filter(Boolean)
    }
  },
  
  methods: {
    // 对齐开关
    onAlignmentToggle() {
      if (this.enableAlignment) {
        this.$emit('mode-change', 'alignment')
      } else {
        this.$emit('mode-change', 'direct')
      }
    },
    
    // 标签管理
    addTag() {
      if (this.newTag && !this.modelTags.includes(this.newTag)) {
        this.modelTags.push(this.newTag)
        this.newTag = ''
      }
    },
    
    removeTag(index) {
      this.modelTags.splice(index, 1)
    },
    
    // 文件选择
    triggerFileInput() {
      this.$refs.fileInput.click()
    },
    
    triggerFolderSelect() {
      this.$refs.fileInput.click()
    },
    
    triggerZipSelect() {
      this.$refs.zipInput.click()
    },
    
    handleFileSelect(event) {
      const selected = Array.from(event.target.files)
      this.addFiles(selected)
    },
    
    handleZipSelect(event) {
      const file = event.target.files[0]
      if (file) {
        this.files = [file]
      }
    },
    
    handleDrop(event) {
      this.isDragging = false
      const dropped = Array.from(event.dataTransfer.files)
      this.addFiles(dropped)
    },
    
    addFiles(newFiles) {
      // 过滤有效文件
      const validFiles = newFiles.filter(f => {
        const ext = f.name.split('.').pop().toLowerCase()
        return ['xlsx', 'xls', 'txt', 'json', 'jsonl', 'zip'].includes(ext)
      })
      this.files = [...this.files, ...validFiles]
    },
    
    clearFiles() {
      this.files = []
      this.alignmentResult = null
      this.previewData = {}
      this.tempAlignmentId = null
    },
    
    getFileIcon(filename) {
      const ext = filename.split('.').pop().toLowerCase()
      const icons = {
        xlsx: '📊', xls: '📊',
        txt: '📄',
        json: '📋', jsonl: '📋',
        zip: '📦'
      }
      return icons[ext] || '📄'
    },
    
    formatSize(bytes) {
      if (bytes < 1024) return bytes + ' B'
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
      return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
    },
    
    truncate(text, maxLen) {
      if (!text) return '-'
      return text.length > maxLen ? text.slice(0, maxLen) + '...' : text
    },
    
    cleanValue(val) {
      if (val === null || val === undefined) return ''
      const s = String(val).trim()
      if (s === '' || s.toLowerCase() === 'nan') return ''
      return s
    },
    
    // 上传处理
    async uploadFiles() {
      if (this.files.length === 0) return
      
      this.isUploading = true
      
      try {
        const formData = new FormData()
        
        // 添加文件
        this.files.forEach(file => {
          formData.append('files', file)
        })
        
        // 添加配置
        if (this.enableAlignment) {
          formData.append('enable_alignment', 'true')
          formData.append('model_tags', JSON.stringify(this.modelTags))
          if (this.modelNames.length > 0) {
            formData.append('model_names', JSON.stringify(this.modelNames))
          }
        }
        
        const response = await fetch('/api/v1/alignment/upload', {
          method: 'POST',
          body: formData
        })
        
        if (!response.ok) {
          throw new Error(`上传失败: ${response.status}`)
        }
        
        const result = await response.json()
        
        if (result.success) {
          this.tempAlignmentId = result.data.alignment_id
          
          if (this.enableAlignment) {
            // 对齐模式：显示预览
            this.alignmentResult = result.data
            this.$emit('alignment-complete', result.data)
          } else {
            // 直接上传模式：触发父组件处理
            this.$emit('upload-complete', result.data)
          }
        } else {
          throw new Error(result.message || '处理失败')
        }
      } catch (error) {
        this.$emit('error', error.message)
        alert(`上传失败: ${error.message}`)
      } finally {
        this.isUploading = false
      }
    },
    
    // 展开/收起组
    toggleGroup(groupId) {
      const idx = this.expandedGroups.indexOf(groupId)
      if (idx > -1) {
        this.expandedGroups.splice(idx, 1)
      } else {
        this.expandedGroups.push(groupId)
      }
    },
    
    // 加载预览数据
    async loadPreviewData(groupId) {
      if (this.previewData[groupId]) return
      
      try {
        const response = await fetch(`/api/v1/alignment/preview/${this.tempAlignmentId}/${groupId}`)
        const result = await response.json()
        
        if (result.success) {
          this.$set(this.previewData, groupId, result.data.rows)
        }
      } catch (error) {
        console.error('加载预览失败:', error)
      }
    },
    
    // 取消对齐
    cancelAlignment() {
      if (this.tempAlignmentId) {
        fetch(`/api/v1/alignment/cancel/${this.tempAlignmentId}`, { method: 'DELETE' })
      }
      this.alignmentResult = null
      this.previewData = {}
      this.tempAlignmentId = null
    },
    
    // 确认并导入
    async confirmAndImport() {
      if (!this.tempAlignmentId) return
      
      this.isImporting = true
      
      try {
        const response = await fetch(`/api/v1/alignment/confirm/${this.tempAlignmentId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            import_to_evaluation: true
          })
        })
        
        const result = await response.json()
        
        if (result.success) {
          this.$emit('import-complete', result.data)
          this.clearFiles()
          alert('导入成功！')
        } else {
          throw new Error(result.message || '导入失败')
        }
      } catch (error) {
        this.$emit('error', error.message)
        alert(`导入失败: ${error.message}`)
      } finally {
        this.isImporting = false
      }
    }
  }
}
</script>

<style scoped>
.alignment-upload-component {
  padding: 20px;
}

.upload-section {
  margin-bottom: 30px;
}

.alignment-toggle {
  margin: 15px 0;
  padding: 15px;
  background: #f5f5f5;
  border-radius: 8px;
}

.toggle-label {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}

.toggle-text {
  font-weight: 500;
}

.toggle-hint {
  color: #666;
  font-size: 0.9em;
}

.model-config {
  margin: 15px 0;
  padding: 15px;
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid #e0e0e0;
}

.tags-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  background: #e3f2fd;
  border-radius: 4px;
  font-size: 0.9em;
}

.tag button {
  background: none;
  border: none;
  cursor: pointer;
  color: #666;
}

.tag-input {
  padding: 4px 10px;
  border: 1px solid #ddd;
  border-radius: 4px;
  width: 150px;
}

.upload-area {
  border: 2px dashed #ccc;
  border-radius: 12px;
  padding: 40px;
  text-align: center;
  cursor: pointer;
  transition: all 0.3s;
}

.upload-area.drag-over {
  border-color: #2196f3;
  background: #e3f2fd;
}

.upload-area.has-files {
  border-style: solid;
  border-color: #4caf50;
  background: #f1f8e9;
}

.hidden-input {
  display: none;
}

.upload-placeholder {
  color: #666;
}

.upload-icon {
  font-size: 48px;
  margin-bottom: 10px;
}

.upload-hint {
  color: #999;
  font-size: 0.9em;
}

.upload-buttons {
  margin-top: 15px;
  display: flex;
  gap: 10px;
  justify-content: center;
}

.files-preview {
  text-align: left;
}

.files-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  font-weight: 500;
}

.files-list {
  max-height: 200px;
  overflow-y: auto;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  background: white;
  border-radius: 4px;
  margin-bottom: 5px;
}

.file-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-size {
  color: #999;
  font-size: 0.9em;
}

.more-files {
  text-align: center;
  color: #999;
  padding: 10px;
}

.upload-actions {
  margin-top: 20px;
  text-align: center;
}

/* 预览区域 */
.preview-section {
  margin-top: 30px;
  padding: 20px;
  background: #fafafa;
  border-radius: 12px;
}

.stats-cards {
  display: flex;
  gap: 20px;
  margin: 20px 0;
}

.stat-card {
  flex: 1;
  padding: 20px;
  background: white;
  border-radius: 8px;
  text-align: center;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.stat-value {
  font-size: 32px;
  font-weight: bold;
  color: #2196f3;
}

.stat-label {
  color: #666;
  margin-top: 5px;
}

.models-info {
  margin: 20px 0;
}

.model-badges {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 10px;
}

.model-badge {
  padding: 6px 14px;
  background: #e3f2fd;
  color: #1976d2;
  border-radius: 20px;
  font-size: 0.9em;
}

.groups-list {
  margin: 20px 0;
}

.group-item {
  margin-bottom: 10px;
  background: white;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid #e0e0e0;
}

.group-item.has-error {
  border-color: #ff9800;
}

.group-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 15px;
  cursor: pointer;
  background: #f5f5f5;
}

.group-header:hover {
  background: #eeeeee;
}

.group-id {
  font-weight: 500;
  flex: 1;
}

.group-rows {
  color: #666;
  font-size: 0.9em;
}

.error-badge {
  padding: 2px 8px;
  background: #ff9800;
  color: white;
  border-radius: 10px;
  font-size: 0.8em;
}

.group-details {
  padding: 15px;
  border-top: 1px solid #e0e0e0;
}

.group-files {
  margin-bottom: 15px;
}

.file-row {
  display: flex;
  gap: 10px;
  padding: 5px 0;
  font-size: 0.9em;
}

.model-label {
  font-weight: 500;
  color: #666;
  min-width: 80px;
}

.group-errors {
  margin: 15px 0;
  padding: 10px;
  background: #fff3e0;
  border-radius: 4px;
}

.error-item {
  color: #e65100;
  font-size: 0.9em;
  padding: 3px 0;
}

.preview-table-wrapper {
  margin-top: 15px;
  overflow-x: auto;
}

.preview-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9em;
}

.preview-table th,
.preview-table td {
  padding: 10px;
  text-align: left;
  border-bottom: 1px solid #e0e0e0;
  white-space: pre-wrap;
  word-break: break-word;
  max-width: 400px;
}

.preview-table th {
  background: #f5f5f5;
  font-weight: 500;
}

.empty-cell {
  color: #999;
  font-style: italic;
}

.more-rows {
  text-align: center;
  color: #999;
  padding: 10px;
}

.preview-actions {
  display: flex;
  justify-content: flex-end;
  gap: 15px;
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid #e0e0e0;
}

/* 按钮样式 */
.btn-primary {
  padding: 12px 30px;
  background: #2196f3;
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 1em;
  cursor: pointer;
  transition: background 0.3s;
}

.btn-primary:hover:not(:disabled) {
  background: #1976d2;
}

.btn-primary:disabled {
  background: #ccc;
  cursor: not-allowed;
}

.btn-secondary {
  padding: 10px 20px;
  background: white;
  color: #666;
  border: 1px solid #ddd;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.3s;
}

.btn-secondary:hover {
  background: #f5f5f5;
}

.btn-text {
  background: none;
  border: none;
  color: #2196f3;
  cursor: pointer;
  font-size: 0.9em;
}

.btn-text:hover {
  text-decoration: underline;
}

.spinner {
  display: inline-block;
  width: 16px;
  height: 16px;
  border: 2px solid #fff;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
