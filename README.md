# 🐕 PTSD - Product Test Smart Dog

AI 驱动的产品智能评测系统，支持多模型对比、Judge 自动评分、人工复核和历史结果管理。

## ✨ 核心功能

### 🔬 三种评测模式
| 模式 | 说明 | 适用场景 |
|------|------|----------|
| **Judge Only** | 导入含问答的文件，Judge 模型自动评分 | 已有模型输出，只需评分 |
| **Excel Batch** | 上传 Prompt 列表，调用各产品 API 获取回答后评分 | 端到端批量测试 |
| **Single Case** | 手动输入单条 Prompt，实时调用各产品对比 | 快速调试验证 |

### 📊 评测能力
- **多维度评分** — scale（1-10）、binary（是/否）、categorical（分类） 三种打分类型
- **AI 自动生成维度** — 输入场景描述，一键生成匹配的评测维度
- **多 Run 稳定性分析** — 同一数据多次评测，观察趋势和方差
- **高级统计** — 加权平均、熵权法、PCA 综合排名
- **ZIP 批量项目** — 打包多文件一次评测，支持全文传入 / 滑窗两种上下文模式
- **V2 上下文评分** — 支持文件级上下文 + 行级附加字段，适合翻译/同传等复杂场景

### 📂 历史结果管理
- 所有评测结果自动持久化到 `eval_results/` 目录
- Dashboard 页面可浏览历史评测列表，点击加载完整结果
- 人工复核修改的分数自动回写到原始结果文件

### 👥 人工复核
- 盲审模式：隐藏模型名称，避免偏见
- 逐题对比，支持拖拽调整答案区域
- 自动识别 binary/categorical 维度，呈现对应控件
- 同步到 Dashboard + 导出 Excel

### ⏹️ 评测中断
- 所有评测模式均支持一键中断
- 前端取消 HTTP 请求 + 后端终止剩余任务
- 已完成的部分结果自动保留

## 📁 项目结构

```
AItester0227/
├── backend/                    # Python 后端 (FastAPI)
│   ├── main.py                # 主程序 + API 路由
│   ├── judge_batch_context.py # V2 上下文评分核心
│   ├── judge_batch_context_api.py  # V2 API 注册
│   ├── judge_batch_project_api.py  # ZIP 项目评测
│   ├── judge_caller_factory.py     # Judge 调用工厂
│   ├── eval_task_manager.py   # 评测任务管理
│   ├── batch_api_service.py   # Batch API 服务
│   ├── batch_api_routes.py    # Batch API 路由
│   ├── prompt_cache_builder.py # Prompt 缓存构建
│   ├── judge_prompt.txt       # Judge 提示词模板
│   ├── dimension_presets.json # 维度预设
│   └── requirements.txt       # Python 依赖
├── frontend/                   # Next.js 前端
│   ├── app/
│   │   ├── page.tsx           # 主页面（工作台/Dashboard/人工复核）
│   │   ├── globals.css        # 全局样式
│   │   ├── components/
│   │   │   ├── HumanReviewPage.tsx  # 人工复核组件
│   │   │   └── ModelConfigPage.tsx  # 模型配置组件
│   │   └── config/page.tsx    # 配置页面
│   └── package.json
├── eval_results/              # 评测结果存档（运行时生成）
├── install.bat / install.sh   # 一键安装依赖
├── start.bat / start.sh       # 一键启动服务
└── README.md
```

## 🚀 快速开始

### 环境要求
- **Python 3.8+**
- **Node.js 16+**（含 npm）

### 1. 安装依赖

**Windows:**
```cmd
双击 install.bat
```

**macOS / Linux:**
```bash
chmod +x install.sh start.sh
./install.sh
```

### 2. 启动服务

**Windows:**
```cmd
双击 start.bat
```

**macOS / Linux:**
```bash
./start.sh
```

### 3. 开始使用

1. 浏览器打开 **http://localhost:3000**
2. 在「模型管理」页面配置 Judge 模型（API Key、Model、URL）
3. 选择评测模式，上传数据，开始评测

## ⚙️ 配置说明

### Judge 模型配置

三种配置方式（优先级从高到低）：

1. **前端页面**（推荐）— 在「模型管理」页面可视化配置，保存即生效
2. **配置文件** — 编辑 `backend/judge_config.json`
3. **环境变量** — 在 `backend/.env` 中设置

```json
// backend/judge_config.json 示例
{
  "api_key": "your-api-key",
  "model": "gemini-2.5-flash",
  "api_url": "https://aigw.netease.com/v1/chat/completions",
  "max_input_tokens": 200000,
  "max_output_tokens": 64000,
  "concurrency": 3,
  "timeout": 90,
  "batch_api_enabled": false,
  "prompt_cache_enabled": false
}
```

### 被测产品配置

在主页面侧边栏或「模型管理」页面添加：
- **OpenRouter / OpenAI 兼容 API** — 粘贴代码片段自动解析
- **Dify Workflow** — 填写 API URL 和 Key

## 📖 使用指南

### Judge Only 模式（最常用）

1. 准备数据文件（Excel / JSON / JSONL），包含「问题」和「模型回答」列
2. 在评测工作台选择「Judge Only」模式
3. 上传文件，映射列（问题列、模型列、可选的图片列/轮次列）
4. 输入评测场景，点击「AI 生成维度」
5. 点击「🚀 Judge Only 评测」
6. 评测完成后自动跳转 Dashboard 查看结果

**V2 批量上下文模式：**
- 切换 API 版本为 V2
- 可设置文件级上下文（文本/文件）
- 可勾选行级附加上下文字段
- 适合翻译质量评估等需要参考上下文的场景

### ZIP 项目评测

1. 准备 ZIP 文件（包含 manifest.json 和多个 JSONL 数据文件）
2. 在 Judge Only 模式下选择「ZIP 上传」
3. 解析后可全选/清空/逐个选择文件
4. 每个文件显示行数、传入模式（全文/滑窗）
5. 点击评测，通过 SSE 流式查看进度

### 查看历史评测

1. 切换到「评测报告」页面
2. 点击「刷新列表」加载所有历史评测记录
3. 点击任一记录 → 加载到 Dashboard（雷达图、排行榜、明细表）
4. 切换到「人工复核」页面可逐题审核和修改分数
5. 点击「同步到 Dashboard」保存修改（自动回写磁盘）

## 🔧 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/judge-only-excel` | POST | Judge Only 评测 (V1) |
| `/api/judge-only-excel-batch` | POST | Judge Only 批量上下文评测 (V2) |
| `/api/judge-batch-project` | POST | ZIP 项目评测 (SSE) |
| `/api/batch-evaluate-excel` | POST | Excel Batch 评测 |
| `/api/test` | POST | 单产品测试 |
| `/api/evaluate` | POST | Judge 评分 |
| `/api/generate-dimensions` | POST | AI 生成评测维度 |
| `/api/eval-history` | GET | 列出历史评测 |
| `/api/eval-history/{run_id}` | GET | 加载历史评测详情 |
| `/api/eval-history/update-scores` | POST | 更新人工复核分数 |
| `/api/eval/create-session` | POST | 创建评测会话（中断支持） |
| `/api/eval/abort/{session_id}` | POST | 中断评测 |
| `/api/config/judge` | GET/PUT | Judge 模型配置 |
| `/api/products` | GET/POST/DELETE | 产品管理 |
| `/docs` | GET | Swagger API 文档 |

## 🐛 故障排除

| 问题 | 解决方案 |
|------|----------|
| 后端启动失败 | 检查 `.venv` 是否存在，运行 `install.bat/sh` |
| 端口 8000 被占用 | 关闭占用进程，或重新运行 `start.bat/sh` |
| Judge 模型调用失败 | 检查 API Key、模型名称、API URL 是否正确 |
| 前端无法连接后端 | 确认后端已启动，检查浏览器控制台报错 |
| 历史评测加载为空 | 检查 `eval_results/` 目录是否有结果文件 |

## 🛠️ 技术栈

**后端:** FastAPI · Pydantic · Pandas · scikit-learn · OpenPyXL

**前端:** Next.js 14 · TypeScript · Recharts · Axios · Lucide Icons

---

**当前版本**: v3.0  
**最后更新**: 2026-03-30
