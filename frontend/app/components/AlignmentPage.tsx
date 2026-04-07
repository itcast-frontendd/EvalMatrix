"use client";

import React, { useState, useRef, useCallback, useEffect } from "react";
import { Upload, FolderOpen, FileArchive, Settings, Eye, CheckCircle, AlertCircle, Loader2, Download, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import JSZip from "jszip";

/* ================================================================
   AlignmentPage — 数据批量导入（单模型/多模型通用）
   流程: 上传zip/文件夹 → 后端对齐 → 预览结果 → 确认导入Judge工作台
   ================================================================ */

const API = "http://localhost:8000";

interface AlignmentPageProps {
  onImportZip?: (file: File) => void;
}

interface AlignJob {
  job_id: string;
  status: string;
  progress: number;
  message: string;
  group_count: number;
  file_count: number;
  errors: string[];
}

interface PreviewData {
  file_info: any;
  total_files: number;
  file_index: number;
  total_rows: number;
  columns: string[];
  data: any[];
  model_keys: string[];
  model_display_names: Record<string, string>;
}

// ── 主组件 ──
export default function AlignmentPage({ onImportZip }: AlignmentPageProps) {
  // 配置
  const [modelTags, setModelTags] = useState("e2e,pipeline");
  const [modelNames, setModelNames] = useState("端到端模型,Pipeline模型");
  const [showConfig, setShowConfig] = useState(false);

  // 上传
  const fileRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState("");

  // 文件夹 input 需要手动设置 webkitdirectory 属性（React JSX 不可靠）
  useEffect(() => {
    if (folderRef.current) {
      folderRef.current.setAttribute("webkitdirectory", "");
      folderRef.current.setAttribute("mozdirectory", "");
      folderRef.current.setAttribute("directory", "");
    }
  }, []);

  // 任务
  const [job, setJob] = useState<AlignJob | null>(null);
  const [polling, setPolling] = useState(false);

  // 预览
  const [preview, setPreview] = useState<PreviewData | null>(null);
  const [previewIdx, setPreviewIdx] = useState(0);

  // 确认
  const [confirming, setConfirming] = useState(false);
  const [confirmResult, setConfirmResult] = useState<any>(null);

  // ── 轮询任务状态 ──
  useEffect(() => {
    if (!job || !polling) return;
    if (job.status === "completed" || job.status === "failed") {
      setPolling(false);
      if (job.status === "completed") loadPreview(job.job_id, 0);
      return;
    }
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`${API}/api/alignment/status/${job.job_id}`);
        const data = await res.json();
        setJob(data);
        if (data.status === "completed" || data.status === "failed") {
          setPolling(false);
          if (data.status === "completed") loadPreview(data.job_id, 0);
        }
      } catch (e) {
        console.error("Poll error", e);
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [job?.job_id, job?.status, polling]);

  // ── 上传 ZIP 文件到后端 ──
  const submitZip = async (file: File) => {
    setUploading(true);
    setUploadMsg("");
    setConfirmResult(null);
    setPreview(null);
    setJob(null);

    const tags = modelTags.split(",").map(s => s.trim()).filter(Boolean);
    const names = modelNames.split(",").map(s => s.trim()).filter(Boolean);

    const config = JSON.stringify({
      model_tags: tags,
      model_names: names.length === tags.length ? names : null,
    });

    const fd = new FormData();
    fd.append("file", file);
    fd.append("config", config);

    try {
      const res = await fetch(`${API}/api/alignment/upload`, { method: "POST", body: fd });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || "上传失败");
      }
      const data = await res.json();
      setJob({ job_id: data.job_id, status: "processing", progress: 5, message: "已提交...", group_count: 0, file_count: 0, errors: [] });
      setPolling(true);
    } catch (e: any) {
      alert("上传失败: " + e.message);
    } finally {
      setUploading(false);
      setUploadMsg("");
    }
  };

  // ── ZIP 文件上传 ──
  const handleZipSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) submitZip(f);
    e.target.value = "";
  };

  // ── 文件夹上传：前端打包成 ZIP 再提交 ──
  const handleFolderSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    e.target.value = "";

    setUploading(true);
    setUploadMsg("正在打包文件夹...");

    try {
      const zip = new JSZip();

      // 收集所有 .xlsx / .txt / .xls 文件
      let addedCount = 0;
      const skippedExts = new Set<string>();
      console.log(`[FolderUpload] Total files from browser: ${files.length}`);
      for (let i = 0; i < files.length; i++) {
        const f = files[i];
        const relPath = (f as any).webkitRelativePath || f.name;
        const name = f.name.toLowerCase();
        const ext = name.substring(name.lastIndexOf("."));
        console.log(`[FolderUpload] file[${i}]: name="${f.name}", relPath="${relPath}", ext="${ext}", size=${f.size}`);
        if (ext === ".xlsx" || ext === ".xls" || ext === ".txt") {
          const buf = await f.arrayBuffer();
          zip.file(relPath, buf);
          addedCount++;
        } else {
          skippedExts.add(ext);
        }
      }
      console.log(`[FolderUpload] Added: ${addedCount}, Skipped exts: ${[...skippedExts].join(", ")}`);

      if (addedCount === 0) {
        alert(`文件夹中未找到 .xlsx / .txt 文件（共 ${files.length} 个文件，扩展名: ${[...skippedExts].join(", ")}）`);
        setUploading(false);
        setUploadMsg("");
        return;
      }

      setUploadMsg(`正在压缩 ${addedCount} 个文件...`);
      const blob = await zip.generateAsync({ type: "blob" });
      const zipFile = new File([blob], "folder_upload.zip", { type: "application/zip" });

      setUploadMsg("上传中...");
      await submitZip(zipFile);
    } catch (e: any) {
      alert("文件夹打包失败: " + e.message);
      setUploading(false);
      setUploadMsg("");
    }
  };

  // ── 预览 ──
  const loadPreview = async (jobId: string, idx: number) => {
    try {
      const res = await fetch(`${API}/api/alignment/preview/${jobId}?file_index=${idx}&max_rows=15`);
      const data = await res.json();
      setPreview(data);
      setPreviewIdx(idx);
    } catch (e) {
      console.error("Preview error", e);
    }
  };

  // ── 确认导入 ──
  const handleConfirm = async () => {
    if (!job) return;
    setConfirming(true);
    try {
      const confirmRes = await fetch(`${API}/api/alignment/confirm/${job.job_id}`, { method: "POST" });
      const confirmData = await confirmRes.json();

      const zipRes = await fetch(`${API}/api/alignment/download/${job.job_id}?file_type=zip`);
      if (!zipRes.ok) throw new Error("下载ZIP失败");
      const blob = await zipRes.blob();
      const zipFile = new File([blob], "eval_project.zip", { type: "application/zip" });

      if (onImportZip) {
        onImportZip(zipFile);
        setConfirmResult({ ...confirmData, imported: true });
      } else {
        setConfirmResult(confirmData);
      }
    } catch (e: any) {
      alert("确认失败: " + e.message);
    } finally {
      setConfirming(false);
    }
  };

  // ── 下载 ──
  const handleDownload = () => {
    if (!job) return;
    window.open(`${API}/api/alignment/download/${job.job_id}?file_type=zip`, "_blank");
  };

  // ── 重置 ──
  const handleReset = () => {
    setJob(null);
    setPreview(null);
    setConfirmResult(null);
    setPolling(false);
    setUploadMsg("");
  };

  // ── 值显示：空白/nan 转为空字符串 ──
  const displayValue = (val: any): string => {
    if (val === null || val === undefined) return "";
    const s = String(val).trim();
    if (s === "" || s.toLowerCase() === "nan") return "";
    return s;
  };

  // ── 渲染 ──
  return (
    <div style={{ padding: "0 0 40px 0" }}>
      {/* 说明 */}
      <div className="card" style={{ marginBottom: 16, background: "#f0f7ff", border: "1px solid #bdd7ff" }}>
        <div className="card-body" style={{ padding: "14px 20px" }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>📋 使用说明</div>
          <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13, color: "#333", lineHeight: 1.8 }}>
            <li>支持<b>单模型</b>和<b>多模型</b>数据：同一组文件共享前缀（如 <code>row_4_xxx</code>），含 <code>__sentences.xlsx</code>（必须）+ <code>__transcript_origin.txt</code>（可选）</li>
            <li>文件名需包含模型标记（如 <code>__e2e__</code>），系统自动按 <code>row_N</code> / <code>lang_N</code> 前缀分组</li>
            <li>上传 <b>.zip 文件</b>或<b>选择文件夹</b> → 自动处理 → 预览确认 → 一键导入评测工作台</li>
          </ol>
        </div>
      </div>

      {/* 配置面板 */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-header" style={{ cursor: "pointer" }} onClick={() => setShowConfig(!showConfig)}>
          <span><Settings size={16} style={{ marginRight: 6, verticalAlign: -2 }} />模型配置</span>
          <span style={{ fontSize: 12, color: "#888", marginLeft: 8 }}>
            当前: {modelTags.split(",").length} 个模型标记
          </span>
          {showConfig ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </div>
        {showConfig && (
          <div className="card-body" style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 200 }}>
              <label style={{ fontSize: 13, fontWeight: 500 }}>模型标识符（逗号分隔）</label>
              <input className="form-input" value={modelTags} onChange={e => setModelTags(e.target.value)}
                placeholder="e2e, pipeline, model_a" style={{ marginTop: 4 }} />
              <div style={{ fontSize: 11, color: "#999", marginTop: 4 }}>从文件名 __标记__ 中匹配</div>
            </div>
            <div style={{ flex: 1, minWidth: 200 }}>
              <label style={{ fontSize: 13, fontWeight: 500 }}>显示名称（逗号分隔）</label>
              <input className="form-input" value={modelNames} onChange={e => setModelNames(e.target.value)}
                placeholder="端到端模型, Pipeline模型" style={{ marginTop: 4 }} />
            </div>
          </div>
        )}
      </div>

      {/* 上传区 */}
      {!job && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-body" style={{ textAlign: "center", padding: 40 }}>
            {/* 隐藏的 input */}
            <input ref={fileRef} type="file" accept=".zip" style={{ display: "none" }}
              onChange={handleZipSelect} />
            <input ref={folderRef} type="file"
              style={{ display: "none" }}
              onChange={handleFolderSelect} />

            <div style={{ display: "flex", justifyContent: "center", gap: 16, marginBottom: 16 }}>
              <div style={{ width: 56, height: 56, borderRadius: 28, background: "#e8f4fd", display: "flex", alignItems: "center", justifyContent: "center" }}>
                <Upload size={28} color="#1a73e8" />
              </div>
            </div>
            <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 6 }}>上传数据</div>
            <div style={{ fontSize: 13, color: "#888", marginBottom: 20 }}>
              支持 .zip 文件或直接选择文件夹（含原文 txt + 模型 xlsx）
            </div>

            {uploading && uploadMsg && (
              <div style={{ fontSize: 13, color: "#1a73e8", marginBottom: 12, display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
                <Loader2 size={14} className="spin" /> {uploadMsg}
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "center", gap: 12 }}>
              <button className="btn btn-primary" onClick={() => fileRef.current?.click()} disabled={uploading}>
                <FileArchive size={14} style={{ marginRight: 6 }} />选择 ZIP 文件
              </button>
              <button className="btn btn-secondary" onClick={() => folderRef.current?.click()} disabled={uploading}
                style={{ borderColor: "#1a73e8", color: "#1a73e8" }}>
                <FolderOpen size={14} style={{ marginRight: 6 }} />选择文件夹
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 进度 */}
      {job && job.status !== "completed" && job.status !== "failed" && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-body">
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
              <Loader2 size={18} className="spin" color="#1a73e8" />
              <span style={{ fontWeight: 500 }}>{job.message}</span>
            </div>
            <div style={{ background: "#e5e7eb", borderRadius: 4, height: 8 }}>
              <div style={{ width: `${job.progress}%`, height: 8, borderRadius: 4, background: "#1a73e8", transition: "width 0.3s" }} />
            </div>
            <div style={{ textAlign: "right", fontSize: 12, color: "#888", marginTop: 4 }}>{job.progress}%</div>
          </div>
        </div>
      )}

      {/* 失败 */}
      {job?.status === "failed" && (
        <div className="card" style={{ marginBottom: 16, border: "1px solid #fca5a5" }}>
          <div className="card-body">
            <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#dc2626" }}>
              <AlertCircle size={18} /> <b>处理失败</b>
            </div>
            <div style={{ marginTop: 8, fontSize: 13 }}>{job.message}</div>
            <button className="btn btn-secondary btn-sm" style={{ marginTop: 12 }} onClick={handleReset}>重新上传</button>
          </div>
        </div>
      )}

      {/* 预览 */}
      {job?.status === "completed" && preview && (
        <>
          {/* 统计卡片 */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 16 }}>
            {[
              { label: "数据组数", value: job.file_count, color: "#059669", bg: "#ecfdf5" },
              { label: "总行数", value: preview.total_rows, color: "#1a73e8", bg: "#e8f4fd" },
              { label: "模型数", value: preview.model_keys?.length || 0, color: "#d97706", bg: "#fffbeb" },
              { label: "警告", value: job.errors?.length || 0, color: job.errors?.length ? "#dc2626" : "#059669", bg: job.errors?.length ? "#fef2f2" : "#ecfdf5" },
            ].map((s, i) => (
              <div key={i} className="card" style={{ background: s.bg, textAlign: "center", padding: "16px 8px" }}>
                <div style={{ fontSize: 28, fontWeight: 700, color: s.color }}>{s.value}</div>
                <div style={{ fontSize: 12, color: "#666" }}>{s.label}</div>
              </div>
            ))}
          </div>

          {/* 文件切换 */}
          {preview.total_files > 1 && (
            <div style={{ marginBottom: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
              {Array.from({ length: preview.total_files }, (_, i) => (
                <button key={i} className={`btn btn-sm ${i === previewIdx ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => loadPreview(job.job_id, i)}>
                  文件 {i + 1}
                </button>
              ))}
            </div>
          )}

          {/* 预览表格 */}
          <div className="card" style={{ marginBottom: 16 }}>
            <div className="card-header">
              <span><Eye size={16} style={{ marginRight: 6, verticalAlign: -2 }} />
                数据预览 — {preview.file_info?.file_id} ({preview.file_info?.row_count} 行)
              </span>
            </div>
            <div style={{ overflowX: "auto" }}>
              <table className="data-table" style={{ fontSize: 12, width: "100%" }}>
                <thead>
                  <tr>
                    <th style={{ width: 50 }}>ID</th>
                    <th style={{ minWidth: 200 }}>ASR 原文</th>
                    {(preview.model_keys || []).map(k => (
                      <th key={k} style={{ minWidth: 200 }}>
                        {preview.model_display_names?.[k] || k.replace("translation_", "")}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.data.map((row, ri) => (
                    <tr key={ri}>
                      <td>{row.sentence_id}</td>
                      <td style={{ maxWidth: 400, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{displayValue(row.asr_text)}</td>
                      {(preview.model_keys || []).map(k => {
                        const val = displayValue(row[k]);
                        return (
                          <td key={k} style={{ maxWidth: 400, whiteSpace: "pre-wrap", wordBreak: "break-word", color: val ? "#111" : "#ccc" }}>
                            {val || "—"}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {preview.total_rows > preview.data.length && (
              <div style={{ textAlign: "center", padding: "8px 0", fontSize: 12, color: "#999" }}>
                显示前 {preview.data.length} 行，共 {preview.total_rows} 行
              </div>
            )}
          </div>

          {/* 警告 */}
          {job.errors && job.errors.length > 0 && (
            <div className="card" style={{ marginBottom: 16, border: "1px solid #fcd34d" }}>
              <div className="card-body">
                <div style={{ fontWeight: 500, color: "#92400e", marginBottom: 8 }}>⚠️ 处理警告</div>
                {job.errors.map((e, i) => (
                  <div key={i} style={{ fontSize: 12, color: "#78350f", padding: "2px 0" }}>• {e}</div>
                ))}
              </div>
            </div>
          )}

          {/* 操作按钮 */}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 12 }}>
            <button className="btn btn-secondary" onClick={handleReset}>
              <Trash2 size={14} style={{ marginRight: 4 }} />取消
            </button>
            <button className="btn btn-secondary" onClick={handleDownload}>
              <Download size={14} style={{ marginRight: 4 }} />下载 ZIP
            </button>
            <button className="btn btn-primary" onClick={handleConfirm} disabled={confirming}>
              {confirming
                ? <><Loader2 size={14} className="spin" style={{ marginRight: 4 }} />导入中...</>
                : <><CheckCircle size={14} style={{ marginRight: 4 }} />确认并导入评测</>}
            </button>
          </div>
        </>
      )}

      {/* 导入结果 */}
      {confirmResult && (
        <div className="card" style={{ marginTop: 16, border: "1px solid #86efac" }}>
          <div className="card-body">
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
              <CheckCircle size={18} color="#16a34a" /> <b style={{ color: "#16a34a" }}>导入成功</b>
            </div>
            <div style={{ fontSize: 13, color: "#333" }}>
              {confirmResult.imported
                ? "已自动导入到评测工作台，请切换到「评测工作台」查看"
                : <>ZIP 包路径: <code>{confirmResult.zip_path}</code></>}
            </div>
            <div style={{ fontSize: 12, color: "#888", marginTop: 4 }}>
              {confirmResult.imported
                ? "在工作台中配置评测维度和场景后即可启动 Judge 评测"
                : "可在 \"评测工作台\" 中使用此 ZIP 包启动 Judge 评测"}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
