"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import axios from "axios";
import { Save, RefreshCw, Plus, Trash2, Cpu, Zap, X } from "lucide-react";

const API_BASE = "http://localhost:8000";

interface JudgeConfig {
  model: string;
  api_url: string;
  api_key_set: boolean;
  api_key_suffix?: string;
  max_input_tokens: number;
  max_output_tokens: number;
  concurrency: number;
  timeout: number;
  batch_api_enabled: boolean;
  prompt_cache_enabled: boolean;
}

interface Product {
  id: string;
  name: string;
  type: string;
  url: string;
  key: string;
  model_name?: string;
}

interface BatchTask {
  batch_id: string;
  status: string;
  request_counts: { completed: number; failed: number; total: number };
  created_at: number;
  completed_at: number | null;
  output_file_id: string;
  error_file_id: string;
  errors: any;
  metadata: Record<string, string>;
}

export default function ModelConfigPage() {
  const [judgeConfig, setJudgeConfig] = useState<JudgeConfig | null>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [messageType, setMessageType] = useState<"success" | "error">("success");
  const [batchTasks, setBatchTasks] = useState<BatchTask[]>([]);
  const [batchLoading, setBatchLoading] = useState(false);
  const pollRef = useRef<NodeJS.Timeout | null>(null);

  const [showAddProduct, setShowAddProduct] = useState(false);
  const [newProdName, setNewProdName] = useState("");
  const [newProdUrl, setNewProdUrl] = useState("");
  const [newProdKey, setNewProdKey] = useState("");
  const [newProdCode, setNewProdCode] = useState("");

  const [judgeForm, setJudgeForm] = useState({
    api_key: "", model: "", api_url: "",
    max_input_tokens: 200000, max_output_tokens: 64000,
    concurrency: 3, timeout: 90,
    batch_api_enabled: false, prompt_cache_enabled: false,
  });

  useEffect(() => {
    loadConfigs();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  const loadConfigs = async () => {
    try {
      setLoading(true);
      const [judgeRes, productsRes] = await Promise.all([
        axios.get(`${API_BASE}/api/config/judge`),
        axios.get(`${API_BASE}/api/products`),
      ]);
      setJudgeConfig(judgeRes.data);
      setProducts(productsRes.data);
      setJudgeForm({
        api_key: "", model: judgeRes.data.model || "", api_url: judgeRes.data.api_url || "",
        max_input_tokens: judgeRes.data.max_input_tokens || 200000,
        max_output_tokens: judgeRes.data.max_output_tokens || 64000,
        concurrency: judgeRes.data.concurrency || 3, timeout: judgeRes.data.timeout || 90,
        batch_api_enabled: judgeRes.data.batch_api_enabled || false,
        prompt_cache_enabled: judgeRes.data.prompt_cache_enabled || false,
      });
      if (judgeRes.data.batch_api_enabled) loadBatchTasks();
    } catch (error) {
      showMsg("加载配置失败", "error");
    } finally { setLoading(false); }
  };

  const loadBatchTasks = useCallback(async () => {
    try {
      setBatchLoading(true);
      const res = await axios.get(`${API_BASE}/api/batch/list?limit=20`);
      if (res.data.status === "success" && res.data.data) {
        const tasks: BatchTask[] = (res.data.data.data || []).map((item: any) => ({
          batch_id: item.id || "", status: item.status || "unknown",
          request_counts: item.request_counts || { completed: 0, failed: 0, total: 0 },
          created_at: item.created_at || 0, completed_at: item.completed_at,
          output_file_id: item.output_file_id || "", error_file_id: item.error_file_id || "",
          errors: item.errors, metadata: item.metadata || {},
        }));
        setBatchTasks(tasks);
        const hasActive = tasks.some(t => ["validating", "in_progress", "finalizing"].includes(t.status));
        if (hasActive && !pollRef.current) pollRef.current = setInterval(() => loadBatchTasks(), 5000);
        else if (!hasActive && pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      }
    } catch (error) { console.error("Failed to load batch tasks:", error); }
    finally { setBatchLoading(false); }
  }, []);

  const handleJudgeConfigSave = async () => {
    try {
      setSaving(true);
      const updateData: any = {};
      if (judgeForm.api_key.trim()) updateData.api_key = judgeForm.api_key;
      if (judgeForm.model.trim()) updateData.model = judgeForm.model;
      if (judgeForm.api_url.trim()) updateData.api_url = judgeForm.api_url;
      updateData.max_input_tokens = judgeForm.max_input_tokens;
      updateData.max_output_tokens = judgeForm.max_output_tokens;
      updateData.concurrency = judgeForm.concurrency;
      updateData.timeout = judgeForm.timeout;
      updateData.batch_api_enabled = judgeForm.batch_api_enabled;
      updateData.prompt_cache_enabled = judgeForm.prompt_cache_enabled;
      const res = await axios.put(`${API_BASE}/api/config/judge`, updateData);
      if (res.data.status === "success") {
        showMsg("Judge 配置保存成功!", "success");
        loadConfigs();
        setJudgeForm(prev => ({ ...prev, api_key: "" }));
      }
    } catch (error: any) {
      showMsg(error.response?.data?.detail || "保存失败", "error");
    } finally { setSaving(false); }
  };

  const handleAddProduct = async () => {
    if (!newProdName) return;
    try {
      await axios.post(`${API_BASE}/api/products`, { id: Date.now().toString(), name: newProdName, type: "openrouter", url: newProdUrl, key: newProdKey, code_snippet: newProdCode });
      showMsg("产品添加成功", "success");
      setNewProdName(""); setNewProdUrl(""); setNewProdKey(""); setNewProdCode("");
      setShowAddProduct(false);
      loadConfigs();
    } catch (e) { showMsg("添加失败", "error"); }
  };

  const handleDeleteProduct = async (id: string) => {
    if (!confirm("确定删除该产品?")) return;
    try { await axios.delete(`${API_BASE}/api/products/${id}`); loadConfigs(); }
    catch (e) { showMsg("删除失败", "error"); }
  };

  const handleCancelBatch = async (batchId: string) => {
    if (!confirm("确定取消此任务?")) return;
    try { await axios.post(`${API_BASE}/api/batch/${batchId}/cancel`); showMsg("取消请求已发送", "success"); loadBatchTasks(); }
    catch (error) { showMsg("取消失败", "error"); }
  };

  const handleViewResults = async (batchId: string) => {
    try {
      const res = await axios.get(`${API_BASE}/api/batch/${batchId}/results`);
      if (res.data.status === "success") {
        const results = res.data.results;
        alert(`Batch 结果：共 ${results.length} 条，成功 ${results.filter((r: any) => r.status === "success").length} 条`);
      } else showMsg(res.data.error || "获取结果失败", "error");
    } catch (error) { showMsg("获取结果失败", "error"); }
  };

  const showMsg = (msg: string, type: "success" | "error") => {
    setMessage(msg); setMessageType(type);
    setTimeout(() => setMessage(""), 3000);
  };

  const isClaudeModel = (m: string) => m.toLowerCase().includes("claude") || m.toLowerCase().includes("anthropic");

  if (loading) return <div className="empty-state"><div className="animate-spin-slow" style={{ width: 24, height: 24, border: "3px solid #e2e8f0", borderTopColor: "#059669", borderRadius: "50%", margin: "0 auto" }} /></div>;

  return (
    <div className="animate-fade-in" style={{ maxWidth: 960, margin: "0 auto" }}>
      {message && (
        <div style={{ padding: "0.75rem 1rem", marginBottom: "1rem", borderRadius: 8, background: messageType === "success" ? "#ecfdf5" : "#fef2f2", color: messageType === "success" ? "#047857" : "#dc2626", border: `1px solid ${messageType === "success" ? "#a7f3d0" : "#fecaca"}`, fontSize: "0.875rem", fontWeight: 500 }}>
          {message}
        </div>
      )}

      {/* ── Judge Config ── */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <div className="card-header">
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}><Cpu size={16} /> Judge 评测模型</span>
          <span className="badge badge-green">{judgeConfig?.api_key_set ? "已配置" : "未配置"}</span>
        </div>
        <div className="card-body">
          <div style={{ display: "flex", gap: 8, marginBottom: 12, fontSize: "0.8rem", color: "var(--text-muted)" }}>
            <span>模型: <code style={{ background: "#f1f5f9", padding: "1px 4px", borderRadius: 3 }}>{judgeConfig?.model}</code></span>
            {judgeConfig?.api_key_suffix && <span>· Key 后缀: ...{judgeConfig.api_key_suffix}</span>}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
            <div>
              <label className="form-label">API Key</label>
              <input className="form-input" type="password" value={judgeForm.api_key} onChange={e => setJudgeForm({ ...judgeForm, api_key: e.target.value })} placeholder={judgeConfig?.api_key_set ? "留空保持不变" : "输入 API Key"} />
            </div>
            <div>
              <label className="form-label">模型名称</label>
              <input className="form-input" value={judgeForm.model} onChange={e => setJudgeForm({ ...judgeForm, model: e.target.value })} placeholder="claude-opus-4-6" />
            </div>
            <div style={{ gridColumn: "1 / -1" }}>
              <label className="form-label">API URL</label>
              <input className="form-input" value={judgeForm.api_url} onChange={e => setJudgeForm({ ...judgeForm, api_url: e.target.value })} placeholder="https://aigw.netease.com/v1/chat/completions" />
            </div>
            <div>
              <label className="form-label">最大输入 Tokens</label>
              <input className="form-input" type="number" value={judgeForm.max_input_tokens} onChange={e => setJudgeForm({ ...judgeForm, max_input_tokens: parseInt(e.target.value) || 200000 })} />
            </div>
            <div>
              <label className="form-label">最大输出 Tokens</label>
              <input className="form-input" type="number" value={judgeForm.max_output_tokens} onChange={e => setJudgeForm({ ...judgeForm, max_output_tokens: parseInt(e.target.value) || 64000 })} />
            </div>
            <div>
              <label className="form-label">并发数</label>
              <input className="form-input" type="number" min={1} max={10} value={judgeForm.concurrency} onChange={e => setJudgeForm({ ...judgeForm, concurrency: parseInt(e.target.value) || 3 })} />
            </div>
            <div>
              <label className="form-label">超时 (秒)</label>
              <input className="form-input" type="number" value={judgeForm.timeout} onChange={e => setJudgeForm({ ...judgeForm, timeout: parseInt(e.target.value) || 90 })} />
            </div>
          </div>

          {/* Cost Optimization Toggles */}
          <div style={{ marginTop: "1.25rem" }}>
            <div className="section-title" style={{ display: "flex", alignItems: "center", gap: 6 }}><Zap size={14} /> 降本优化</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" }}>
              <ToggleCard checked={judgeForm.batch_api_enabled} onChange={val => setJudgeForm({ ...judgeForm, batch_api_enabled: val })} title="Batch API" desc="异步批量处理，降低 50% 成本，24h内完成" />
              <ToggleCard checked={judgeForm.prompt_cache_enabled} onChange={val => setJudgeForm({ ...judgeForm, prompt_cache_enabled: val })} title="Prompt Cache" desc={isClaudeModel(judgeForm.model) ? "Claude 专用，缓存重复 prompt 节省 token" : "仅对 Claude 系列生效"} />
            </div>
          </div>

          <div style={{ marginTop: "1.25rem", display: "flex", gap: 8 }}>
            <button className="btn btn-primary" onClick={handleJudgeConfigSave} disabled={saving}>
              <Save size={14} /> {saving ? "保存中..." : "保存配置"}
            </button>
            <button className="btn btn-secondary" onClick={loadConfigs}><RefreshCw size={14} /> 刷新</button>
          </div>
        </div>
      </div>

      {/* ── Batch Tasks ── */}
      {judgeForm.batch_api_enabled && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <div className="card-header">
            <span>📋 Batch 任务管理</span>
            <button className="btn btn-sm btn-ghost" onClick={() => loadBatchTasks()} disabled={batchLoading}>
              <RefreshCw size={12} /> {batchLoading ? "刷新中..." : "刷新"}
            </button>
          </div>
          <div className="card-body">
            {batchTasks.length === 0 ? (
              <div style={{ textAlign: "center", padding: "1.5rem", color: "var(--text-muted)", fontSize: "0.875rem" }}>暂无 Batch 任务</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {batchTasks.map(task => {
                  const rc = task.request_counts;
                  const progress = rc.total > 0 ? Math.round((rc.completed / rc.total) * 100) : 0;
                  const isActive = ["validating", "in_progress", "finalizing"].includes(task.status);
                  return (
                    <div key={task.batch_id} style={{ padding: "0.75rem 1rem", borderRadius: 8, border: `1px solid ${isActive ? "#bfdbfe" : "var(--border)"}`, background: isActive ? "#eff6ff" : "#fafbfc" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.8rem" }}>
                          <code style={{ color: "var(--text-muted)" }}>{task.batch_id.slice(0, 16)}...</code>
                          <span className={`badge ${task.status === "completed" ? "badge-green" : isActive ? "badge-blue" : "badge-red"}`}>{task.status}</span>
                        </div>
                        <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>{task.created_at ? new Date(task.created_at * 1000).toLocaleString("zh-CN") : "-"}</span>
                      </div>
                      {rc.total > 0 && (
                        <div style={{ marginBottom: 8 }}>
                          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 3 }}>
                            <span>{rc.completed}/{rc.total} 完成{rc.failed > 0 && <span style={{ color: "#dc2626" }}> ({rc.failed} 失败)</span>}</span>
                            <span style={{ fontWeight: 600 }}>{progress}%</span>
                          </div>
                          <div className="progress-bar"><div className="progress-bar-fill" style={{ width: `${progress}%` }} /></div>
                        </div>
                      )}
                      <div style={{ display: "flex", gap: 6 }}>
                        {isActive && <button className="btn btn-sm btn-danger" onClick={() => handleCancelBatch(task.batch_id)}>取消</button>}
                        {task.status === "completed" && <button className="btn btn-sm btn-primary" onClick={() => handleViewResults(task.batch_id)}>查看结果</button>}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Products ── */}
      <div className="card">
        <div className="card-header">
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}><Cpu size={16} /> 被测产品 ({products.length})</span>
          <button className="btn btn-sm btn-primary" onClick={() => setShowAddProduct(!showAddProduct)}>
            {showAddProduct ? <X size={14} /> : <Plus size={14} />} {showAddProduct ? "取消" : "添加产品"}
          </button>
        </div>
        <div className="card-body">
          {showAddProduct && (
            <div style={{ padding: "1rem", background: "#f8fafc", borderRadius: 8, border: "1px solid var(--border)", marginBottom: "1rem" }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" }}>
                <div><label className="form-label">产品名称 *</label><input className="form-input" value={newProdName} onChange={e => setNewProdName(e.target.value)} placeholder="e.g. GPT-4o" /></div>
                <div><label className="form-label">API URL</label><input className="form-input" value={newProdUrl} onChange={e => setNewProdUrl(e.target.value)} placeholder="https://..." /></div>
                <div><label className="form-label">API Key</label><input className="form-input" type="password" value={newProdKey} onChange={e => setNewProdKey(e.target.value)} /></div>
                <div style={{ display: "flex", alignItems: "flex-end" }}><button className="btn btn-primary" style={{ width: "100%" }} onClick={handleAddProduct}><Plus size={14} /> 添加</button></div>
              </div>
              <div style={{ marginTop: 8 }}>
                <label className="form-label">代码片段 (可选)</label>
                <textarea className="form-textarea" style={{ fontFamily: "monospace", fontSize: "0.8rem", minHeight: 60 }} value={newProdCode} onChange={e => setNewProdCode(e.target.value)} placeholder="Paste API example code..." />
              </div>
            </div>
          )}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {products.map(p => (
              <div key={p.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0.75rem 1rem", borderRadius: 8, border: "1px solid var(--border)", background: "#fafbfc" }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: "0.9rem" }}>{p.name}</div>
                  <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", display: "flex", gap: 8 }}>
                    <span className="badge badge-blue">{p.type}</span>
                    {p.model_name && <span>模型: {p.model_name}</span>}
                    <span style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.url}</span>
                  </div>
                </div>
                <button className="btn btn-sm btn-ghost" onClick={() => handleDeleteProduct(p.id)} title="删除产品" style={{ color: "#dc2626" }}><Trash2 size={14} /></button>
              </div>
            ))}
            {products.length === 0 && <div style={{ textAlign: "center", padding: "1.5rem", color: "var(--text-muted)" }}>暂无已配置产品</div>}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Toggle Card ──
function ToggleCard({ checked, onChange, title, desc }: { checked: boolean; onChange: (v: boolean) => void; title: string; desc: string }) {
  return (
    <div onClick={() => onChange(!checked)} style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "0.75rem", borderRadius: 8, border: `1px solid ${checked ? "#a7f3d0" : "var(--border)"}`, background: checked ? "#ecfdf5" : "#fafbfc", cursor: "pointer", transition: "all 0.2s" }}>
      <div style={{ width: 36, minWidth: 36, height: 20, borderRadius: 10, background: checked ? "#059669" : "#cbd5e0", position: "relative", transition: "background 0.2s", marginTop: 2 }}>
        <div style={{ width: 16, height: 16, borderRadius: "50%", background: "#fff", position: "absolute", top: 2, left: checked ? 18 : 2, transition: "left 0.2s", boxShadow: "0 1px 2px rgba(0,0,0,0.15)" }} />
      </div>
      <div>
        <div style={{ fontWeight: 600, fontSize: "0.85rem", color: "var(--text-main)" }}>{title}</div>
        <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: 2, lineHeight: 1.4 }}>{desc}</div>
      </div>
    </div>
  );
}
