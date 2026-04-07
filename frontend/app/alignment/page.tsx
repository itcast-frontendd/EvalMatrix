"use client";

import { useState } from "react";
import AlignmentUpload from "../components/AlignmentUpload";
import AlignmentPreviewModal from "../components/AlignmentPreviewModal";
import AlignmentProgress from "../components/AlignmentProgress";

interface AlignmentResult {
  alignment_id: string;
  status: string;
  file_count: number;
  group_count: number;
  output_dir: string;
  manifest: any;
  preview_url?: string;
}

export default function AlignmentPage() {
  const [alignmentResult, setAlignmentResult] = useState<AlignmentResult | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<any>(null);

  const handleAlignmentComplete = (result: AlignmentResult) => {
    setAlignmentResult(result);
    setShowPreview(true);
  };

  const handleConfirmImport = async () => {
    if (!alignmentResult) return;

    setImporting(true);
    try {
      const response = await fetch(
        `http://localhost:8000/api/alignment/confirm/${alignmentResult.alignment_id}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            auto_import_to_judge: true,
          }),
        }
      );

      const result = await response.json();
      setImportResult(result);

      if (result.status === "success") {
        alert(`导入成功！\n评测任务ID: ${result.judge_result?.batch_id || "N/A"}`);
        setShowPreview(false);
        setAlignmentResult(null);
      } else {
        alert(`导入失败: ${result.message}`);
      }
    } catch (error) {
      alert(`导入出错: ${error}`);
    } finally {
      setImporting(false);
    }
  };

  const handleCancel = () => {
    setShowPreview(false);
    setAlignmentResult(null);
    setImportResult(null);
  };

  return (
    <div className="min-h-screen bg-gray-50 py-8">
      <div className="max-w-6xl mx-auto px-4">
        {/* 页面标题 */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-gray-900 mb-2">
            多模型数据对齐
          </h1>
          <p className="text-gray-600">
            上传包含多模型输出的文件夹或压缩包，自动对齐后导入评测系统
          </p>
        </div>

        {/* 使用说明 */}
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
          <h3 className="font-semibold text-blue-900 mb-2">📋 使用说明</h3>
          <ol className="list-decimal list-inside text-sm text-blue-800 space-y-1">
            <li>
              准备数据：确保同一数据组的文件具有相同前缀（如{" "}
              <code>row_4_xxx</code>）
            </li>
            <li>
              模型标记：文件名中包含模型标识（如{" "}
              <code>__e2e__</code>、<code>__pipeline__</code>）
            </li>
            <li>
              文件格式：每组包含 <code>__transcript_origin.txt</code> +{" "}
              <code>__sentences.xlsx</code>
            </li>
            <li>上传处理：支持文件夹拖拽或ZIP压缩包上传</li>
            <li>预览确认：对齐结果预览无误后导入评测系统</li>
          </ol>
        </div>

        {/* 上传组件 */}
        <AlignmentUpload
          apiUrl="http://localhost:8000"
          onAlignmentComplete={handleAlignmentComplete}
        />

        {/* 进度显示 */}
        {alignmentResult?.alignment_id && (
          <div className="mt-6">
            <AlignmentProgress
              alignmentId={alignmentResult.alignment_id}
              apiUrl="http://localhost:8000"
              onComplete={(data) => {
                console.log("Alignment completed:", data);
              }}
            />
          </div>
        )}

        {/* 预览弹窗 */}
        {alignmentResult && (
          <AlignmentPreviewModal
            isOpen={showPreview}
            alignmentId={alignmentResult.alignment_id}
            apiUrl="http://localhost:8000"
            onClose={handleCancel}
            onConfirm={handleConfirmImport}
            loading={importing}
          />
        )}

        {/* 导入结果 */}
        {importResult && (
          <div className="mt-6 bg-white rounded-lg shadow p-6">
            <h3 className="font-semibold text-gray-900 mb-4">导入结果</h3>
            <pre className="bg-gray-50 p-4 rounded text-sm overflow-auto">
              {JSON.stringify(importResult, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
