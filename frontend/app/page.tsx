"use client";

import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import axios from "axios";
import Link from "next/link";
import HumanReviewPage from "./components/HumanReviewPage";
import ModelConfigPage from "./components/ModelConfigPage";
import AlignmentPage from "./components/AlignmentPage";
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer, Legend, Tooltip as RechartsTooltip, BarChart, Bar, XAxis, YAxis, CartesianGrid, LineChart, Line } from 'recharts';
import { Sparkles, Play, BarChart2, List, Settings, Plus, Terminal, Trash2, CheckSquare, Square, TrendingUp, Upload, ChevronDown, ChevronRight, Layout, LayoutTemplate, Download, FlaskConical, Users, Cpu, Eye, Code, X, StopCircle, AlertTriangle, GitCompareArrows } from 'lucide-react';
import clsx from 'clsx';

import ReactMarkdown from 'react-markdown';

interface Product {
    id: string;
    name: string;
    type: string;
    url: string;
    key: string;
    code_snippet?: string;
    model_name?: string;
}

interface Dimension {
    name: string;
    description: string;
    weight: number;
    type?: "scale" | "binary" | "categorical";
    options?: string[];
}

import * as XLSX from 'xlsx';

interface EvaluationResult {
    scores: { [key: string]: number | string | boolean };
    reasoning: string;
    answer?: string;
    latency?: number;
    question?: string;
}

const COLORS = ['#10b981', '#3b82f6', '#f59e0b', '#8b5cf6', '#ec4899'];

// HumanReview replaced by HumanReviewPage

export default function Home() {
  const [activePage, setActivePage] = useState<"workbench" | "dashboard" | "review" | "models" | "alignment">("workbench");
  const [activeTab, setActiveTab] = useState<string>("manual");
  const [showPresetSave, setShowPresetSave] = useState(false);
  const [showRequestPreview, setShowRequestPreview] = useState(false);
  
  // Common State
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState<{[key: string]: boolean}>({});
  
  // Manual Mode State
  const [text, setText] = useState("");
  const [imageUrls, setImageUrls] = useState("");
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [results, setResults] = useState<{[key: string]: any}>({});

  // Auto Mode State
  const [scenario, setScenario] = useState("");
  const [evaluationMode, setEvaluationMode] = useState<"single" | "batch" | "judge-only">("single");
  const [excelFile, setExcelFile] = useState<File | null>(null);
  const [autoText, setAutoText] = useState(""); 
  const [autoImageUrls, setAutoImageUrls] = useState(""); 
  const [autoImageFiles, setAutoImageFiles] = useState<File[]>([]);
  
  const [dimensions, setDimensions] = useState<Dimension[]>([]);
  
  // State for Batch Evaluation
  const [runCount, setRunCount] = useState(1);
  const [currentRun, setCurrentRun] = useState(0);
  const [aggregatedResults, setAggregatedResults] = useState<{[key: string]: any}>({});
  const [latestAnswers, setLatestAnswers] = useState<{[key: string]: string}>({});
  const [allQuestions, setAllQuestions] = useState<string[]>([]);
  const [allAnswersMap, setAllAnswersMap] = useState<{[key: string]: string[]}>({});
  const [selectedProductIds, setSelectedProductIds] = useState<string[]>([]);
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [isGeneratingDims, setIsGeneratingDims] = useState(false);
  const [batchRunHistory, setBatchRunHistory] = useState<any[]>([]);
  const [allRunsData, setAllRunsData] = useState<any[]>([]);
  const [viewRunIndex, setViewRunIndex] = useState<number>(0);
  const [advancedStats, setAdvancedStats] = useState<{entropy_weights?: {[key:string]: number}, pca_scores?: {[key:string]: number}}>({});
  const [scoringMethod, setScoringMethod] = useState<"weighted" | "entropy" | "pca">("weighted");

  // Judge Only State
  const [judgeExcelFile, setJudgeExcelFile] = useState<File | null>(null);
  const [judgeMapping, setJudgeMapping] = useState<{question: string, models: string[], image: string, runtime: string}>({question: "", models: [], image: "", runtime: ""});
  const [excelHeaders, setExcelHeaders] = useState<string[]>([]);
  const [showMappingModal, setShowMappingModal] = useState(false);
  const [dimensionPresets, setDimensionPresets] = useState<{[key:string]: Dimension[]}>({});
  const [presetName, setPresetName] = useState("");
  
  // JSON/JSONL Support State
  const [judgeFileType, setJudgeFileType] = useState<"excel" | "json" | "jsonl" | "zip">("excel");
  const [questionPrefix, setQuestionPrefix] = useState("");
  const [detectingFields, setDetectingFields] = useState(false);
  const [sampleRecord, setSampleRecord] = useState<any>(null);

  // Batch Project (ZIP) State
  const [zipManifest, setZipManifest] = useState<any>(null);
  const [zipParsing, setZipParsing] = useState(false);
  const [zipSelectedFiles, setZipSelectedFiles] = useState<string[]>([]);
  const [zipProjectRunning, setZipProjectRunning] = useState(false);
  const [zipProjectResults, setZipProjectResults] = useState<any>(null);
  const [zipCurrentFile, setZipCurrentFile] = useState("");
  const [zipProgress, setZipProgress] = useState<any>(null); // {percent, file_id, batch_completed, batch_total, ...}
  const [zipRunId, setZipRunId] = useState("");
  const [zipFileResults, setZipFileResults] = useState<any[]>([]); // 逐文件累积结果

  // Batch Context (V2) State
  const [judgeApiVersion, setJudgeApiVersion] = useState<"v1" | "v2">("v1");
  const [batchSize, setBatchSize] = useState(3);
  const [batchConcurrency, setBatchConcurrency] = useState(3);
  const [fileContextMode, setFileContextMode] = useState<"none" | "text" | "file">("none");
  const [fileContextText, setFileContextText] = useState("");
  const [fileContextFile, setFileContextFile] = useState<File | null>(null);
  const [contextFieldCols, setContextFieldCols] = useState<string[]>([]);

  // Abort/Interrupt State
  const [evalSessionId, setEvalSessionId] = useState<string | null>(null);
  const [isAborting, setIsAborting] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Prompt Preview State
  const [showPromptPreview, setShowPromptPreview] = useState(false);
  const [promptPreviewData, setPromptPreviewData] = useState<any>(null);
  const [loadingPromptPreview, setLoadingPromptPreview] = useState(false);

  // History State
  const [evalHistory, setEvalHistory] = useState<any[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [historyDimensions, setHistoryDimensions] = useState<Dimension[]>([]);

  // Abort handler
  const handleAbortEvaluation = useCallback(async () => {
    setIsAborting(true);
    try {
      // 1. Cancel the HTTP request in-flight (frontend side)
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      // 2. Tell backend to stop processing remaining tasks
      if (evalSessionId) {
        await axios.post(`http://localhost:8000/api/eval/abort/${evalSessionId}`).catch(() => {});
      }
    } catch (e) {
      console.error("[ABORT] Error during abort:", e);
    }
    // Note: isAborting and isEvaluating will be reset in the finally block of the eval function
  }, [evalSessionId]);

  // ── History: fetch list ──
  const fetchEvalHistory = useCallback(async () => {
    setLoadingHistory(true);
    try {
      const res = await axios.get("http://localhost:8000/api/eval-history?limit=50");
      setEvalHistory(res.data.runs || []);
    } catch (e) {
      console.error("[ERROR] Failed to fetch eval history:", e);
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  // ── History: load a specific run into dashboard ──
  const loadHistoryRun = useCallback(async (runId: string) => {
    setLoadingHistory(true);
    try {
      const res = await axios.get(`http://localhost:8000/api/eval-history/${runId}`);
      if (res.data.status === "success") {
        const { summary } = res.data;
        
        // Use top-level normalized fields from API (works for both ZIP and judge-only)
        const results = res.data.results || {};
        const runsData = res.data.runs_data || [results];
        const trendHistory = res.data.trend_history || [];
        const advStats = res.data.advanced_stats || {};
        
        // Extract dimensions from normalized dimensions_full
        const dimsFromApi = res.data.dimensions_full || [];
        const loadedDims: Dimension[] = dimsFromApi.length > 0
          ? dimsFromApi.map((d: any) => ({ name: d.name, description: d.description || "", weight: d.weight || 1, type: d.type || "scale" }))
          : [];
        
        // Fallback: infer dimension names from avgScores if no dims provided
        if (loadedDims.length === 0 && Object.keys(results).length > 0) {
          const firstPid = Object.keys(results)[0];
          const avgKeys = Object.keys(results[firstPid]?.avgScores || {});
          avgKeys.forEach(k => {
            loadedDims.push({ name: k, description: "", weight: 1, type: "scale" });
          });
        }
        
        // Set state
        setAggregatedResults(results);
        setAllRunsData(runsData);
        setBatchRunHistory(trendHistory);
        setAdvancedStats(advStats);
        setCurrentRunId(runId);
        setHistoryDimensions(loadedDims);
        
        // Also set dimensions so dashboard can use them
        if (loadedDims.length > 0) {
          setDimensions(loadedDims);
        }
        
        // Set scenario
        if (summary?.eval_config_used?.scenario) {
          setScenario(summary.eval_config_used.scenario);
        }
        
        // Extract questions from results
        const firstModel = Object.keys(results)[0];
        if (firstModel && results[firstModel]?.allQuestions) {
          setAllQuestions(results[firstModel].allQuestions);
        } else if (firstModel && results[firstModel]?.runs) {
          setAllQuestions(results[firstModel].runs.map((r: any) => r.question || "").filter(Boolean));
        }
        
        setViewRunIndex(0);
        setActivePage("dashboard");
      } else {
        alert("加载失败: " + (res.data.error || "未知错误"));
      }
    } catch (e: any) {
      alert("加载历史结果失败: " + e.message);
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  // ── History: save human review scores back ──
  const saveHumanReviewScores = useCallback(async (updates: {row: number, model: string, scores: any, file_id?: string}[]) => {
    if (!currentRunId || updates.length === 0) return;
    try {
      const res = await axios.post("http://localhost:8000/api/eval-history/update-scores", {
        run_id: currentRunId,
        updates,
      });
      if (res.data.status === "success") {
        console.log(`[INFO] Saved ${res.data.updated_count} score updates to ${currentRunId}`);
        return true;
      } else {
        alert("保存失败: " + res.data.error);
        return false;
      }
    } catch (e: any) {
      alert("保存分数失败: " + e.message);
      return false;
    }
  }, [currentRunId]);

  // New Product Form
  const [newProdName, setNewProdName] = useState("");
  const [newProdUrl, setNewProdUrl] = useState("");
  const [newProdKey, setNewProdKey] = useState("");
  const [newProdCode, setNewProdCode] = useState("");

  
  // Dashboard View State
  const [expandedRows, setExpandedRows] = useState<number[]>([]);

  useEffect(() => {
      fetchProducts();
      fetchPresets();
  }, []);

  const fetchPresets = async () => {
      try {
          const res = await axios.get("http://localhost:8000/api/dimension-presets");
          setDimensionPresets(res.data);
      } catch (e) {
          console.error("Failed to fetch presets");
      }
  };

  const savePreset = async () => {
      if (!presetName || dimensions.length === 0) return alert("Enter name and add dimensions");
      try {
          const formData = new FormData();
          formData.append("name", presetName);
          formData.append("dimensions", JSON.stringify(dimensions));
          const res = await axios.post("http://localhost:8000/api/dimension-presets", formData);
          if (res.data.status === "failed") {
              alert("Failed to save preset: " + res.data.error);
              return;
          }
          await fetchPresets();
          setPresetName("");
          alert("Preset saved!");
      } catch (e: any) {
          alert("Failed to save preset: " + (e.response?.data?.detail || e.message));
      }
  };

  const loadPreset = (name: string) => {
      if (dimensionPresets[name]) {
          // Ensure all dims have a type
          const safeDims = dimensionPresets[name].map(d => ({
              ...d,
              type: d.type || "scale"
          }));
          setDimensions(safeDims);
      }
  };

  const deletePreset = async (name: string) => {
      if(!confirm("Delete this preset?")) return;
      try {
          await axios.delete(`http://localhost:8000/api/dimension-presets/${name}`);
          await fetchPresets();
      } catch (e) {
          alert("Failed to delete preset");
      }
  };

  const fetchProducts = async () => {
      try {
          const res = await axios.get("http://localhost:8000/api/products");
          setProducts(res.data);
          // Default select all new products
          setSelectedProductIds(prev => {
              const newIds = res.data.map((p: Product) => p.id);
              return Array.from(new Set([...prev, ...newIds]));
          });
      } catch (e) {
          console.error("Failed to fetch products", e);
      }
  };

  const handleAddProduct = async () => {
      if (!newProdName) return;
      const newProduct = {
          id: Date.now().toString(),
          name: newProdName,
          type: "openrouter",
          url: newProdUrl,
          key: newProdKey,
          code_snippet: newProdCode
      };
      
      try {
          await axios.post("http://localhost:8000/api/products", newProduct);
          await fetchProducts();
          setNewProdName("");
          setNewProdUrl("");
          setNewProdKey("");
          setNewProdCode("");
      } catch (e) {
          alert("Failed to add product");
      }
  };

  const handleDeleteProduct = async (id: string) => {
      if(!confirm("Are you sure you want to delete this product?")) return;
      try {
          await axios.delete(`http://localhost:8000/api/products/${id}`);
          await fetchProducts();
          setSelectedProductIds(prev => prev.filter(pid => pid !== id));
      } catch (e) {
          alert("Failed to delete product");
      }
  };

  const toggleProductSelection = (id: string) => {
      setSelectedProductIds(prev => 
          prev.includes(id) ? prev.filter(pid => pid !== id) : [...prev, id]
      );
  };

  const handleManualTest = async (product: Product) => {
    const formData = new FormData();
    formData.append("text", text);
    formData.append("product_id", product.id);
    if (imageUrls) formData.append("image_urls", imageUrls);
    if (imageFiles.length > 0) {
        imageFiles.forEach(file => {
            formData.append("images", file);
        });
    }
    
    setLoading(prev => ({...prev, [product.id]: true}));

    try {
      const res = await axios.post("http://localhost:8000/api/test", formData, {
        headers: { "Content-Type": "multipart/form-data" }
      });
      setResults(prev => ({...prev, [product.id]: res.data}));
    } catch (e: any) {
      const err = e.response?.data || e.message;
      setResults(prev => ({...prev, [product.id]: { error: err }}));
    } finally {
      setLoading(prev => ({...prev, [product.id]: false}));
    }
  };

  const handleGenerateDimensions = async () => {
      if (!scenario) return alert("Please enter a scenario first");
      setIsGeneratingDims(true);
      try {
          const res = await axios.post("http://localhost:8000/api/generate-dimensions", { scenario });
          if (res.data.status === "success") {
              setDimensions(res.data.dimensions);
          } else {
              alert("Error: " + res.data.error);
          }
      } catch (e: any) {
          alert("Failed to generate dimensions: " + e.message);
      } finally {
          setIsGeneratingDims(false);
      }
  };

  const handleDimensionChange = (index: number, field: keyof Dimension, value: any) => {
      const newDims = [...dimensions];
      (newDims[index] as any)[field] = value;
      setDimensions(newDims);
  };

  const handleAddDimension = () => {
      setDimensions([...dimensions, { name: "New Dimension", description: "Description...", weight: 1 }]);
  };

  const handleRemoveDimension = (index: number) => {
      const newDims = [...dimensions];
      newDims.splice(index, 1);
      setDimensions(newDims);
  };

  const handleJudgeOnlyUpload = async (file: File) => {
      setJudgeExcelFile(file);
      
      // Detect file type from extension
      const fileName = file.name.toLowerCase();
      let detectedType: "excel" | "json" | "jsonl" = "excel";
      if (fileName.endsWith('.json')) {
          detectedType = "json";
      } else if (fileName.endsWith('.jsonl')) {
          detectedType = "jsonl";
      }
      setJudgeFileType(detectedType);
      
      if (detectedType === "excel") {
          // Existing Excel logic
          const reader = new FileReader();
          reader.onload = (e) => {
              const data = e.target?.result;
              import('xlsx').then(xlsx => {
                  const workbook = xlsx.read(data, {type: 'binary'});
                  const sheet = workbook.Sheets[workbook.SheetNames[0]];
                  const json = xlsx.utils.sheet_to_json(sheet, {header: 1});
                  if (json && json.length > 0) {
                      const headers = json[0] as string[];
                      setExcelHeaders(headers);
                      
                      // Auto-Map Logic
                      const newMapping = { question: "", models: [], image: "", runtime: "" };
                      
                      // 1. Question
                      const qCol = headers.find(h => h.toLowerCase().includes("question") || h.toLowerCase().includes("input") || h.toLowerCase().includes("prompt"));
                      if (qCol) newMapping.question = qCol;
                      
                      // 2. Image
                      const imgCol = headers.find(h => h.toLowerCase().includes("image") || h.toLowerCase().includes("img") || h.toLowerCase().includes("photo"));
                      if (imgCol) newMapping.image = imgCol;
                      
                      // 3. Runtime
                      const runCol = headers.find(h => h.toLowerCase() === "runtime" || h.toLowerCase() === "run" || h.toLowerCase() === "run_id");
                      if (runCol) newMapping.runtime = runCol;
                      
                      // 4. Models (All others that are not reserved)
                      const reserved = [qCol, imgCol, runCol].filter(Boolean);
                      const potentialModels = headers.filter(h => !reserved.includes(h) && h.toLowerCase() !== "id" && h.toLowerCase() !== "row");
                      newMapping.models = []; // Don't auto-select all columns, let user pick

                      setJudgeMapping(newMapping);
                      setSampleRecord(null);
                      setShowMappingModal(true);
                  }
              });
          };
          reader.readAsBinaryString(file);
      } else {
          // JSON/JSONL logic - detect fields via API
          setDetectingFields(true);
          const formData = new FormData();
          formData.append("file", file);
          formData.append("file_type", detectedType);
          
          try {
              const res = await axios.post("http://localhost:8000/api/detect-fields", formData);
              if (res.data.status === "success") {
                  const fields = res.data.fields;
                  setExcelHeaders(fields);
                  setSampleRecord(res.data.sample_record);
                  
                  // Auto-map
                  const newMapping = { question: "", models: [], image: "", runtime: "" };
                  
                  const qField = fields.find((f: string) => f.toLowerCase().includes("question") || f.toLowerCase().includes("query") || f.toLowerCase().includes("prompt") || f.toLowerCase().includes("input"));
                  if (qField) newMapping.question = qField;
                  
                  const imgField = fields.find((f: string) => f.toLowerCase().includes("image") || f.toLowerCase().includes("img"));
                  if (imgField) newMapping.image = imgField;
                  
                  const runField = fields.find((f: string) => f.toLowerCase() === "runtime" || f.toLowerCase() === "run" || f.toLowerCase().includes("run_id"));
                  if (runField) newMapping.runtime = runField;
                  
                  // Auto-detect answer fields
                  const answerFields = fields.filter((f: string) => 
                      f.toLowerCase().includes("answer") || 
                      f.toLowerCase().includes("response") || 
                      f.toLowerCase().includes("output") ||
                      f.toLowerCase().endsWith("_answer") ||
                      f.toLowerCase().endsWith("_response")
                  );
                  newMapping.models = answerFields;
                  
                  setJudgeMapping(newMapping);
                  setShowMappingModal(true);
              } else {
                  alert("Failed to detect fields: " + res.data.error);
              }
          } catch (err: any) {
              console.error("[ERROR] Field detection failed:", err);
              alert("Failed to detect fields: " + (err.response?.data?.error || err.message));
          } finally {
              setDetectingFields(false);
          }
      }
  };

  // ── ZIP Project: Parse manifest ──
  const handleZipUpload = async (file: File) => {
    setJudgeExcelFile(file);
    setJudgeFileType("zip");
    setZipParsing(true);
    setZipManifest(null);
    setZipProjectResults(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await axios.post("http://localhost:8000/api/parse-manifest", formData);
      if (res.data.status === "success") {
        setZipManifest(res.data);
        // Auto-select all files
        const allIds = (res.data.files || []).filter((f: any) => f.exists_in_zip).map((f: any) => f.file_id);
        setZipSelectedFiles(allIds);
        // Auto-load dimensions from manifest
        const dims = res.data.eval_config?.default_dimensions;
        if (dims && dims.length > 0) {
          setDimensions(dims);
        }
        // Auto-load scenario
        const sc = res.data.eval_config?.default_scenario;
        if (sc) {
          setScenario(sc);
        }
      } else {
        alert("ZIP解析失败: " + (res.data.error || "未知错误"));
      }
    } catch (e: any) {
      alert("ZIP上传失败: " + (e.message || "网络错误"));
    } finally {
      setZipParsing(false);
    }
  };

  // ── ZIP Project: Run evaluation (SSE stream) ──
  const zipAbortControllerRef = useRef<AbortController | null>(null);

  const handleAbortZipProject = useCallback(() => {
    if (zipAbortControllerRef.current) {
      zipAbortControllerRef.current.abort();
    }
    setZipProjectRunning(false);
    setZipCurrentFile("已中断");
  }, []);

  const runZipProject = async () => {
    if (!judgeExcelFile || !zipManifest) return;
    setZipProjectRunning(true);
    setZipProjectResults(null);
    setZipProgress(null);
    setZipCurrentFile("准备中...");
    setZipFileResults([]);
    setZipRunId("");

    // Create abort controller for zip SSE stream
    const controller = new AbortController();
    zipAbortControllerRef.current = controller;

    try {
      const formData = new FormData();
      formData.append("file", judgeExcelFile);
      if (zipSelectedFiles.length < (zipManifest.files || []).length) {
        formData.append("selected_files", JSON.stringify(zipSelectedFiles));
      }
      if (scenario) formData.append("override_scenario", scenario);
      if (dimensions.length > 0) formData.append("override_dimensions", JSON.stringify(dimensions));

      // SSE via fetch (axios doesn't support streaming)
      const resp = await fetch("http://localhost:8000/api/judge-batch-project", {
        method: "POST",
        body: formData,
        signal: controller.signal,
      });

      if (!resp.ok || !resp.body) {
        alert("评测请求失败: " + resp.statusText);
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      const fileResultsAcc: any[] = [];

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // 解析 SSE 事件
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let currentEvent = "";
        let currentData = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            currentData = line.slice(6);
            try {
              const data = JSON.parse(currentData);
              if (currentEvent === "init") {
                setZipRunId(data.run_id || "");
                setZipProgress({ percent: 0, total_rows: data.total_rows, total_files: data.total_files });
              } else if (currentEvent === "file_start") {
                setZipCurrentFile(`${data.file_id} (${data.row_count} 行)`);
              } else if (currentEvent === "progress") {
                setZipProgress((prev: any) => ({ ...prev, ...data }));
              } else if (currentEvent === "file_done") {
                fileResultsAcc.push(data);
                setZipFileResults([...fileResultsAcc]);
                setZipProgress((prev: any) => ({ ...prev, percent: data.percent }));
              } else if (currentEvent === "done") {
                setZipRunId(data.run_id || "");
                setZipProjectResults({
                  project_summary: data.summary,
                  file_results: fileResultsAcc,
                  run_id: data.run_id,
                  csv_available: data.csv_available,
                });

                // ── 将ZIP项目结果合并到 aggregatedResults，使Dashboard和HumanReview可用 ──
                const mergedResults: {[key: string]: any} = {};
                for (const fr of fileResultsAcc) {
                  if (fr.status !== "success" || !fr.results) continue;
                  for (const [modelName, modelData] of Object.entries(fr.results as {[key: string]: any})) {
                    if (!mergedResults[modelName]) {
                      mergedResults[modelName] = {
                        avgScores: {},
                        runs: [],
                        sampleReasoning: "",
                        sampleAnswer: "",
                        allAnswers: [],
                        allQuestions: [],
                        allLatencies: [],
                      };
                    }
                    const m = mergedResults[modelName];
                    m.runs = [...m.runs, ...(modelData.runs || [])];
                    m.allAnswers = [...m.allAnswers, ...(modelData.allAnswers || [])];
                    m.allQuestions = [...m.allQuestions, ...(modelData.allQuestions || [])];
                    m.allLatencies = [...m.allLatencies, ...(modelData.allLatencies || [])];
                    m.sampleReasoning = modelData.sampleReasoning || m.sampleReasoning;
                    m.sampleAnswer = modelData.sampleAnswer || m.sampleAnswer;
                  }
                }
                // 重新计算合并后的平均分
                for (const [modelName, modelData] of Object.entries(mergedResults)) {
                  const avgScores: {[dim: string]: number} = {};
                  const allRuns = (modelData as any).runs || [];
                  if (allRuns.length > 0) {
                    const dimKeys = Object.keys(allRuns[0].scores || {});
                    for (const dk of dimKeys) {
                      const vals = allRuns
                        .map((r: any) => r.scores?.[dk])
                        .filter((v: any) => v !== undefined && v !== null && typeof v === "number");
                      avgScores[dk] = vals.length > 0
                        ? parseFloat((vals.reduce((a: number, b: number) => a + b, 0) / vals.length).toFixed(1))
                        : 0;
                    }
                  }
                  (modelData as any).avgScores = avgScores;
                }
                if (Object.keys(mergedResults).length > 0) {
                  setAggregatedResults(mergedResults);
                  // 同步 allRunsData 以使 HumanReview 可用
                  setAllRunsData([mergedResults]);
                }
              } else if (currentEvent === "error") {
                alert("评测错误: " + (data.error || "未知错误"));
              }
            } catch { /* ignore parse errors */ }
            currentEvent = "";
            currentData = "";
          }
        }
      }
    } catch (e: any) {
      if (e.name === 'AbortError') {
        console.log("[INFO] ZIP project evaluation aborted by user");
      } else {
        alert("评测请求失败: " + (e.message || "网络错误"));
      }
    } finally {
      setZipProjectRunning(false);
      setZipCurrentFile("");
      zipAbortControllerRef.current = null;
    }
  };

  // ── Build request preview ──
  const getRequestPreview = () => {
    if (evaluationMode === "judge-only") {
      if (judgeFileType === "zip") {
        return {
          _comment: "📦 ZIP 批量评测 (SSE Stream)",
          endpoint: "POST /api/judge-batch-project",
          content_type: "multipart/form-data",
          body: {
            file: judgeExcelFile ? `📎 ${judgeExcelFile.name} (${(judgeExcelFile.size / 1024).toFixed(1)} KB)` : "(未选择文件)",
            selected_files: zipSelectedFiles.length > 0 ? zipSelectedFiles : "(全部文件)",
            override_scenario: scenario || "(未设置)",
            override_dimensions: dimensions.length > 0 ? dimensions.map(d => ({name: d.name, type: d.type || "scale", weight: d.weight})) : "(未设置)",
          },
        };
      }

      const apiUrl = judgeApiVersion === "v2"
        ? "/api/judge-only-excel-batch"
        : "/api/judge-only-excel";
      
      const baseBody: any = {
        file: judgeExcelFile ? `📎 ${judgeExcelFile.name} (${(judgeExcelFile.size / 1024).toFixed(1)} KB)` : "(未选择文件)",
        file_type: judgeFileType,
        scenario: scenario || "(未设置)",
        dimensions: dimensions.length > 0 ? dimensions.map(d => ({name: d.name, type: d.type || "scale", weight: d.weight})) : "(未设置维度)",
        mapping: {
          question: judgeMapping.question || "(未映射)",
          models: judgeMapping.models.length > 0 ? judgeMapping.models : "(未映射)",
          image: judgeMapping.image || "(无)",
          runtime: judgeMapping.runtime || "(无)",
          ...(judgeApiVersion === "v2" && contextFieldCols.length > 0 ? { context_fields: contextFieldCols } : {}),
        },
      };

      if (questionPrefix) {
        baseBody.question_prefix = questionPrefix;
      }

      if (judgeApiVersion === "v2") {
        baseBody.batch_size = batchSize;
        baseBody.concurrency = batchConcurrency;
        if (fileContextMode === "text" && fileContextText.trim()) {
          baseBody.file_context = `(文本, ${fileContextText.length} 字符)`;
        } else if (fileContextMode === "file" && fileContextFile) {
          baseBody.file_context = `📎 ${fileContextFile.name}`;
        }
      }

      return {
        _comment: `🔬 Judge Only 评测 (${judgeApiVersion.toUpperCase()})`,
        endpoint: `POST ${apiUrl}`,
        content_type: "multipart/form-data",
        body: baseBody,
      };
    }

    if (evaluationMode === "batch") {
      return {
        _comment: "📊 Excel Batch 评测",
        endpoint: "POST /api/batch-evaluate-excel",
        content_type: "multipart/form-data",
        body: {
          file: excelFile ? `📎 ${excelFile.name} (${(excelFile.size / 1024).toFixed(1)} KB)` : "(未选择文件)",
          product_ids: selectedProductIds.map(id => products.find(p => p.id === id)?.name || id).join(", ") || "(未选择)",
          scenario: scenario || "(未设置)",
          dimensions: dimensions.length > 0 ? dimensions.map(d => ({name: d.name, type: d.type || "scale", weight: d.weight})) : "(未设置维度)",
          run_count: runCount,
        },
      };
    }

    // Single case
    return {
      _comment: "🧪 Single Case 评测",
      flow: [
        {
          step: "1. 调用各产品",
          endpoint: "POST /api/test",
          body: {
            text: autoText || scenario || "(未输入)",
            product_id: "(逐个调用)",
            image_urls: autoImageUrls || "(无)",
            images: autoImageFiles.length > 0 ? `📎 ${autoImageFiles.length} 文件` : "(无)",
          },
        },
        {
          step: "2. Judge 评分",
          endpoint: "POST /api/evaluate",
          body: {
            scenario: scenario || "(未设置)",
            dimensions: dimensions.length > 0 ? dimensions.map(d => d.name) : "(未设置维度)",
            results: "(各产品回答)",
          },
        },
      ],
      selected_products: selectedProductIds.map(id => products.find(p => p.id === id)?.name || id),
      run_count: runCount,
    };
  };

  // ── Prompt Preview: 调用后端构建实际 prompt 并展示 ──
  const fetchPromptPreview = async () => {
    if (dimensions.length === 0) {
      alert("请先设置评分维度");
      return;
    }
    
    // ZIP mode: use dedicated zip preview endpoint
    if (judgeFileType === "zip" && judgeExcelFile && zipManifest) {
      setLoadingPromptPreview(true);
      setPromptPreviewData(null);
      try {
        const formData = new FormData();
        formData.append("file", judgeExcelFile);
        formData.append("scenario", scenario);
        formData.append("dimensions", JSON.stringify(dimensions));
        if (zipSelectedFiles.length > 0) {
          formData.append("preview_file_id", zipSelectedFiles[0]);
        }
        // 使用 manifest 中的 recommended_batch_size，使预览与实际评测一致
        const zipBatchSize = zipManifest?.eval_config?.recommended_batch_size || 3;
        formData.append("preview_rows", String(zipBatchSize));
        const resp = await axios.post("http://localhost:8000/api/preview-prompt-zip", formData);
        setPromptPreviewData(resp.data);
        setShowPromptPreview(true);
      } catch (err: any) {
        console.error("ZIP Prompt preview failed:", err);
        setPromptPreviewData({ error: err?.response?.data?.error || err.message || "预览失败" });
        setShowPromptPreview(true);
      } finally {
        setLoadingPromptPreview(false);
      }
      return;
    }

    // Non-zip mode
    if (!judgeExcelFile) {
      alert("请先上传文件并设置评分维度");
      return;
    }
    setLoadingPromptPreview(true);
    setPromptPreviewData(null);
    try {
      const formData = new FormData();
      formData.append("file", judgeExcelFile);
      formData.append("scenario", scenario);
      formData.append("dimensions", JSON.stringify(dimensions));
      const mappingObj: any = {
        question: judgeMapping.question,
        models: judgeMapping.models,
      };
      if (judgeApiVersion === "v2" && contextFieldCols.length > 0) {
        mappingObj.context_fields = contextFieldCols;
      }
      formData.append("mapping", JSON.stringify(mappingObj));
      formData.append("file_type", judgeFileType);
      formData.append("api_version", judgeApiVersion);
      formData.append("batch_size", String(batchSize));
      formData.append("preview_rows", String(Math.min(batchSize, 2)));
      if (questionPrefix) {
        formData.append("question_prefix", questionPrefix);
      }
      // file_context
      if (judgeApiVersion === "v2") {
        if (fileContextMode === "text" && fileContextText.trim()) {
          formData.append("file_context", fileContextText);
        } else if (fileContextMode === "file" && fileContextFile) {
          const fcText = await fileContextFile.text();
          formData.append("file_context", fcText);
        }
      }
      const resp = await axios.post("/api/preview-prompt", formData);
      setPromptPreviewData(resp.data);
      setShowPromptPreview(true);
    } catch (err: any) {
      console.error("Prompt preview failed:", err);
      setPromptPreviewData({ error: err?.response?.data?.error || err.message || "预览失败" });
      setShowPromptPreview(true);
    } finally {
      setLoadingPromptPreview(false);
    }
  };

  const runJudgeOnly = async () => {
      console.log("[DEBUG] runJudgeOnly called");
      console.log("[DEBUG] judgeExcelFile:", judgeExcelFile);
      console.log("[DEBUG] judgeMapping:", judgeMapping);
      console.log("[DEBUG] dimensions:", dimensions);
      
      if (!judgeExcelFile) {
          alert("Please upload an Excel file first");
          return;
      }
      if (!judgeMapping.question || judgeMapping.models.length === 0) {
          alert("Please map columns first");
          return;
      }
      if (dimensions.length === 0) {
          alert("Dimensions required");
          return;
      }

      console.log("[DEBUG] All validations passed, starting evaluation...");
      setIsEvaluating(true);
      console.log("[DEBUG] isEvaluating set to true");
      
      // Create abort controller and eval session
      const controller = new AbortController();
      abortControllerRef.current = controller;
      
      let sessionId: string | null = null;
      try {
        const sessionRes = await axios.post("http://localhost:8000/api/eval/create-session");
        sessionId = sessionRes.data.session_id;
        setEvalSessionId(sessionId);
      } catch (e) {
        console.warn("[WARN] Failed to create eval session, continuing without abort support");
      }
      
      const isMergeMode = batchRunHistory.length > 0;
      console.log("[DEBUG] isMergeMode:", isMergeMode);
      
      if (!isMergeMode) {
          // Only cleanup if NOT merging
          setAggregatedResults({});
          setLatestAnswers({});
          setBatchRunHistory([]);
          setAllRunsData([]);
          setViewRunIndex(0);
          setAdvancedStats({});
          setRunCount(1); 
          setCurrentRun(1);
      }

      const formData = new FormData();
      formData.append("file", judgeExcelFile);
      formData.append("file_type", judgeFileType);  // New: file type
      formData.append("question_prefix", questionPrefix || "");  // New: question prefix
      formData.append("scenario", scenario || "Manual Judge");
      formData.append("dimensions", JSON.stringify(dimensions));
      
      // V2: include context_fields in mapping
      if (judgeApiVersion === "v2") {
          const mappingV2 = {
              ...judgeMapping,
              context_fields: contextFieldCols,
          };
          formData.append("mapping", JSON.stringify(mappingV2));
          formData.append("batch_size", batchSize.toString());
          formData.append("concurrency", batchConcurrency.toString());
          
          // File context
          if (fileContextMode === "text" && fileContextText.trim()) {
              formData.append("file_context", fileContextText);
          } else if (fileContextMode === "file" && fileContextFile) {
              const ctxContent = await fileContextFile.text();
              formData.append("file_context", ctxContent);
          }
      } else {
          formData.append("mapping", JSON.stringify(judgeMapping));
      }
      
      if (isMergeMode) {
          formData.append("expected_run_count", batchRunHistory.length.toString());
      }
      
      // Add eval session ID for abort support
      if (sessionId) {
          formData.append("eval_session_id", sessionId);
      }

      const apiUrl = judgeApiVersion === "v2"
          ? "http://localhost:8000/api/judge-only-excel-batch"
          : "http://localhost:8000/api/judge-only-excel";

      console.log("[DEBUG] Preparing to send request...");
      console.log("[DEBUG] API URL:", apiUrl);
      console.log("[DEBUG] API Version:", judgeApiVersion);
      console.log("[DEBUG] FormData entries:");
      Array.from(formData.entries()).forEach(pair => {
          console.log(`  ${pair[0]}: ${typeof pair[1] === 'object' ? (pair[1] as any).constructor.name : pair[1]}`);
      });

      try {
          console.log("[DEBUG] Sending POST request...");
          const res = await axios.post(apiUrl, formData, { signal: controller.signal });
          console.log("[DEBUG] Response received:", res.status);
          console.log("[DEBUG] Response data:", res.data);
          if (res.data.status === "success" || res.data.status === "aborted") {
              if (res.data.status === "aborted") {
                  console.log("[INFO] Evaluation was aborted, showing partial results");
              }
              const newResults = res.data.results;
              const newRunsData = res.data.runs_data || [newResults];
              const newTrendHistory = res.data.trend_history || [];

              if (res.data.advanced_stats) {
                   // If merging, maybe we should merge stats? For now just overwrite or keep?
                   // Simplest is to update if new data has stats.
                   setAdvancedStats(res.data.advanced_stats);
              }
              
              if (isMergeMode) {
                  // MERGE LOGIC
                  
                  // 1. Merge Aggregated Results
                  setAggregatedResults(prev => ({...prev, ...newResults}));
                  
                  // 2. Merge Runs Data (Run by Run)
                  setAllRunsData(prev => {
                      const merged = [...prev];
                      newRunsData.forEach((runData: any, idx: number) => {
                          if (merged[idx]) {
                              merged[idx] = { ...merged[idx], ...runData };
                          } else {
                              // Should not happen if validation passed, but just in case
                              merged[idx] = runData;
                          }
                      });
                      return merged;
                  });
                  
                  // 3. Merge Trend History
                  setBatchRunHistory(prev => {
                      const merged = [...prev];
                      newTrendHistory.forEach((hPoint: any, idx: number) => {
                          if (merged[idx]) {
                              merged[idx] = { ...merged[idx], ...hPoint };
                          } else {
                              merged[idx] = hPoint;
                          }
                      });
                      return merged;
                  });
                  
              } else {
                  // REPLACE LOGIC
                  setAggregatedResults(newResults);
                  setAllRunsData(newRunsData);
                  setBatchRunHistory(newTrendHistory);
                  setRunCount(newRunsData.length);
                  setCurrentRun(newRunsData.length);
                  setViewRunIndex(newRunsData.length - 1);
              }

              // Update questions list from the first product of the result
              const firstPid = Object.keys(newResults)[0];
              const mergedQuestions = new Set([...allQuestions, ...(newResults[firstPid]?.allQuestions || [])]);
              setAllQuestions(Array.from(mergedQuestions));
              
              setShowMappingModal(false);
              setJudgeExcelFile(null);
              // Track the run_id for history linkage
              if (res.data.run_id) {
                  setCurrentRunId(res.data.run_id);
              }
              setActivePage("dashboard"); // Switch to report tab
          } else {
              alert("Error: " + res.data.error);
          }
      } catch (e: any) {
          if (axios.isCancel(e) || e.name === 'AbortError' || e.code === 'ERR_CANCELED') {
              console.log("[INFO] Request was cancelled by user");
          } else {
              console.error("[ERROR] Judge evaluation failed:", e);
              console.error("[ERROR] Error details:", {
                  message: e.message,
                  response: e.response,
                  request: e.request,
                  config: e.config
              });
              alert("Judge failed: " + e.message);
          }
      } finally {
          console.log("[DEBUG] Evaluation complete, setting isEvaluating to false");
          setIsEvaluating(false);
          setEvalSessionId(null);
          abortControllerRef.current = null;
      }
  };

  const runBatchEvaluation = async () => {
      if (selectedProductIds.length === 0) return alert("Please select at least one product");
      if (dimensions.length === 0) return alert("Please generate dimensions first");
      
      setIsEvaluating(true);
      setAggregatedResults({});
      setLatestAnswers({});
      setBatchRunHistory([]);
      setAllRunsData([]);
      setCurrentRun(0);
      setViewRunIndex(0);

      // Create abort controller and eval session
      const controller = new AbortController();
      abortControllerRef.current = controller;
      
      let sessionId: string | null = null;
      try {
        const sessionRes = await axios.post("http://localhost:8000/api/eval/create-session");
        sessionId = sessionRes.data.session_id;
        setEvalSessionId(sessionId);
      } catch (e) {
        console.warn("[WARN] Failed to create eval session, continuing without abort support");
      }

      try {
          if (evaluationMode === "batch") {
             if (!excelFile) {
                 alert("Please upload an Excel file");
                 setIsEvaluating(false);
                 return;
             }
             
             const cumulativeScores: { [pid: string]: { [dim: string]: number } } = {};
             const tempBatchHistory: any[] = [];
             const tempAllRuns: any[] = [];
             
             for (let i = 0; i < runCount; i++) {
                 // Check abort before each run
                 if (controller.signal.aborted) {
                     console.log(`[ABORT] Batch evaluation aborted before run ${i+1}`);
                     break;
                 }
                 setCurrentRun(i + 1);
                 
                 const formData = new FormData();
                 formData.append("file", excelFile);
                 formData.append("product_ids", selectedProductIds.join(","));
                 formData.append("scenario", scenario || "General Batch Evaluation");
                 formData.append("dimensions", JSON.stringify(dimensions));
                 if (sessionId) {
                     formData.append("eval_session_id", sessionId);
                 }
                 
                 const res = await axios.post("http://localhost:8000/api/batch-evaluate-excel", formData, {
                     signal: controller.signal,
                 });
                 
                 if (res.data.status === "success") {
                     const runResults = res.data.results;
                     if (res.data.advanced_stats) {
                         setAdvancedStats(res.data.advanced_stats);
                     }
                     
                     // Store full run data
                     tempAllRuns.push(runResults);
                     setAllRunsData([...tempAllRuns]);
                     // Auto switch view to latest run
                     setViewRunIndex(i);
                     
                     // 1. Update History for Trend Chart
                     const historyPoint: any = { run: i + 1 };
                     
                     // 2. Process Results & Accumulate Averages
                     const currentAggregated: {[key: string]: any} = {};
                     
                     // We need to access the previous aggregated results to merge, 
                     // but inside the loop state updates might not be reflected immediately if we rely on state.
                     // So we build 'currentAggregated' based on 'runResults' and 'cumulativeScores'.
                     
                     Object.keys(runResults).forEach(pid => {
                        const p = products.find(prod => prod.id === pid);
                        const result = runResults[pid];
                        
                        // Calculate Weighted Score for this Run (for Trend)
                        let totalScore = 0;
                        let maxScore = 0;
                        dimensions.forEach(dim => {
                            if (dim.type === "categorical") return;
                            let val = result.avgScores[dim.name];
                            if (typeof val !== 'number') val = 0; // Guard against non-number
                            if (dim.type === "binary" && val <= 1) val *= 10;
                            totalScore += val * dim.weight;
                            maxScore += 10 * dim.weight;
                        });
                        const weightedAvg = maxScore > 0 ? (totalScore / maxScore) * 10 : 0;
                        if (p) historyPoint[p.name] = parseFloat(weightedAvg.toFixed(1));
                        
                        // Accumulate Scores
                        if (!cumulativeScores[pid]) cumulativeScores[pid] = {};
                        dimensions.forEach(dim => {
                            cumulativeScores[pid][dim.name] = (cumulativeScores[pid][dim.name] || 0) + (result.avgScores[dim.name] || 0);
                        });
                        
                        // Calculate Grand Average
                        const grandAvg: {[key: string]: number} = {};
                        dimensions.forEach(dim => {
                            grandAvg[dim.name] = parseFloat((cumulativeScores[pid][dim.name] / (i + 1)).toFixed(1));
                        });
                        
                        // Construct merged result
                        // We keep the 'runs', 'sampleReasoning' etc from the LATEST run.
                        // But overwrite 'avgScores' with Grand Average.
                        currentAggregated[pid] = {
                            ...result,
                            avgScores: grandAvg
                        };
                     });
                     
                     tempBatchHistory.push(historyPoint);
                     setBatchRunHistory([...tempBatchHistory]); // Ensure a new array reference
                     
                     // Force Aggregated Update based on Accumulator
                     // We reconstruct the full aggregated object from scratch to avoid state sync issues
                     const newAggregated: {[key: string]: any} = {};
                     Object.keys(currentAggregated).forEach(pid => {
                         newAggregated[pid] = currentAggregated[pid];
                     });
                     
                     // Merge with existing (if any keys were missed, though batch usually has same keys)
                     setAggregatedResults(prev => ({...prev, ...newAggregated}));
 
                      // Update latestAnswers with sample answers from the latest batch
                      const newAnswers: {[key: string]: string} = {};
                      const newAllAnswers: {[key: string]: string[]} = {};
                      let questions: string[] = [];
                      
                      Object.keys(runResults).forEach(pid => {
                         const result = runResults[pid];
                         if (result.sampleAnswer) {
                             newAnswers[pid] = result.sampleAnswer;
                         } else {
                             newAnswers[pid] = "(Answer not available)";
                         }
                         
                         if (result.allAnswers) {
                             newAllAnswers[pid] = result.allAnswers;
                         }
                         
                         if (result.allQuestions && questions.length === 0) {
                             questions = result.allQuestions;
                         }
                      });
                      setLatestAnswers(newAnswers);
                      setAllAnswersMap(newAllAnswers);
                      setAllQuestions(questions);
                      
                  } else {
                      console.error("Batch evaluation failed for run " + (i+1) + ": " + res.data.error);
                      // alert("Batch evaluation failed: " + res.data.error); 
                      // Don't break loop on error? Or maybe break? Let's continue but warn.
                  }
              }
              setActivePage("dashboard"); // Switch to report tab
           } else {
              // Single Case Loop
              const targetProducts = products.filter(p => selectedProductIds.includes(p.id));
              
              // Temporary storage for all runs
              const allRunsData: {[key: string]: EvaluationResult[]} = {};
              targetProducts.forEach(p => allRunsData[p.id] = []);

              for (let i = 0; i < runCount; i++) {
                  // Check abort before each run
                  if (controller.signal.aborted) {
                      console.log(`[ABORT] Single evaluation aborted before run ${i+1}`);
                      break;
                  }
                  setCurrentRun(i + 1);
                  console.log(`Starting Run ${i+1}/${runCount}...`);

                  // 1. Concurrent Test
                  const testPromises = targetProducts.map(async (p) => {
                     const formData = new FormData();
                     const promptToSend = autoText.trim() ? autoText : scenario;
                     formData.append("text", promptToSend);
                     formData.append("product_id", p.id);
                     if (autoImageUrls) formData.append("image_urls", autoImageUrls);
                     if (autoImageFiles.length > 0) {
                         autoImageFiles.forEach(file => {
                             formData.append("images", file);
                         });
                     }

                     try {
                         const res = await axios.post("http://localhost:8000/api/test", formData, {
                            headers: { "Content-Type": "multipart/form-data" }
                         });
                         return { 
                             product_id: p.id, 
                             name: p.name,
                             answer: res.data.answer || JSON.stringify(res.data) 
                         };
                     } catch (e) {
                         return { product_id: p.id, name: p.name, answer: "Error running test" };
                     }
                  });

                  const testResults = await Promise.all(testPromises);
                  
                  // Update latest answers for display
                  const answersMap: {[key:string]: string} = {};
                  testResults.forEach(t => answersMap[t.product_id] = t.answer);
                  setLatestAnswers(answersMap); // Update UI with latest answer

                  // 2. Evaluate Batch
                  const evalPayload = {
                      scenario: scenario || "General Evaluation", // Fallback if scenario is empty
                      dimensions,
                      results: testResults
                  };
                  console.log("DEBUG: Sending Evaluate Request", evalPayload);
                  
                  const evalRes = await axios.post("http://localhost:8000/api/evaluate", evalPayload);

                  if (evalRes.data.status === "success") {
                      const evals = evalRes.data.evaluations;
                      targetProducts.forEach(p => {
                          if (evals[p.id]) {
                              const ans = testResults.find(t => t.product_id === p.id)?.answer;
                              allRunsData[p.id].push({ ...evals[p.id], answer: ans });
                          }
                      });
                      
                      // Incremental Update: Calculate Aggregates immediately
                      const currentAggregated: {[key: string]: any} = {};
                      targetProducts.forEach(p => {
                          const runs = allRunsData[p.id];
                          if (runs.length === 0) return;

                          const avgScores: {[key: string]: number} = {};
                          dimensions.forEach(dim => {
                              const sum = runs.reduce((acc: number, r: any) => acc + (Number(r.scores[dim.name]) || 0), 0);
                              avgScores[dim.name] = parseFloat((sum / runs.length).toFixed(1));
                          });

                          currentAggregated[p.id] = {
                              avgScores,
                              runs: [...runs], // Copy array
                              sampleReasoning: runs[runs.length-1]?.reasoning || ""
                          };
                      });
                      setAggregatedResults(currentAggregated);

                  } else {
                      console.warn(`Run ${i+1} failed evaluation:`, evalRes.data.error);
                  }
              }
              setAllQuestions([autoText || scenario]);
              setActivePage("dashboard"); // Switch to report tab
          }

      } catch (e: any) {
          if (axios.isCancel(e) || e.name === 'AbortError' || e.code === 'ERR_CANCELED') {
              console.log("[INFO] Batch evaluation cancelled by user");
          } else {
              console.error("Eval Error:", e);
              const detail = e.response?.data?.detail ? JSON.stringify(e.response.data.detail) : e.message;
              alert("Process failed: " + detail);
          }
      } finally {
          setIsEvaluating(false);
          setIsAborting(false);
          setCurrentRun(0);
          setEvalSessionId(null);
          abortControllerRef.current = null;
      }
  };

  // Visualization Helpers
  const getRadarData = () => {
      const numericDims = dimensions.filter(d => d.type !== "categorical");
      if (!numericDims.length) return [];
      
      return numericDims.map(dim => {
          const dataPoint: any = { subject: dim.name, fullMark: 10 };
          Object.keys(aggregatedResults).forEach(pid => {
              let p = products.find(prod => prod.id === pid);
              if (!p) {
                  p = { id: pid, name: pid, type: 'judge-only', url: '', key: '' };
              }
              
              if (p) {
                  let val = aggregatedResults[pid].avgScores[dim.name] || 0;
                  // Scale Binary to 10 for visual
                  if (dim.type === "binary" && val <= 1) val *= 10;
                  dataPoint[p.name] = val;
              }
          });
          return dataPoint;
      });
  };

  const handleExport = () => {
    const wb = XLSX.utils.book_new();
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);

    // 1. Overview Sheet
    // Prepare data for all 3 scoring methods to calculate rankings
    const productsData = Object.keys(aggregatedResults).map(pid => {
        let p = products.find(prod => prod.id === pid);
        if (!p) p = { id: pid, name: pid, type: 'judge-only', url: '', key: '' };
        const res = aggregatedResults[pid];

        // 1. Weighted Score
        let totalScore = 0;
        let maxScore = 0;
        dimensions.forEach(dim => {
            if (dim.type === "categorical") return;
            let val = res.avgScores[dim.name] || 0;
            if (dim.type === "binary" && val <= 1) val *= 10;
            totalScore += val * dim.weight;
            maxScore += 10 * dim.weight;
        });
        const weightedScore = maxScore > 0 ? (totalScore / maxScore) * 10 : 0;

        // 2. Entropy Score
        let entropyScore = 0;
        if (advancedStats.entropy_weights) {
            dimensions.forEach(dim => {
                if (dim.type === "categorical") return;
                let val = res.avgScores[dim.name] || 0;
                if (dim.type === "binary" && val <= 1) val *= 10;
                let w = advancedStats.entropy_weights?.[dim.name] || 0;
                entropyScore += val * w;
            });
        }

        // 3. PCA Score
        const pcaScore = advancedStats.pca_scores?.[pid] || 0;

        return {
            pid,
            name: p.name,
            res,
            weightedScore,
            entropyScore,
            pcaScore
        };
    });

    // Calculate Rankings
    const sortedByWeighted = [...productsData].sort((a, b) => b.weightedScore - a.weightedScore);
    const sortedByEntropy = [...productsData].sort((a, b) => b.entropyScore - a.entropyScore);
    const sortedByPCA = [...productsData].sort((a, b) => b.pcaScore - a.pcaScore);

    const getRank = (pid: string, sortedList: any[]) => sortedList.findIndex(item => item.pid === pid) + 1;

    const overviewData = productsData.map(item => {
        const row: any = {
            "Product": item.name,
            "Weighted Score": parseFloat(item.weightedScore.toFixed(2)),
            "Weighted Rank": getRank(item.pid, sortedByWeighted),
            "Entropy Score": parseFloat(item.entropyScore.toFixed(2)),
            "Entropy Rank": getRank(item.pid, sortedByEntropy),
            "PCA Score": parseFloat(item.pcaScore.toFixed(2)),
            "PCA Rank": getRank(item.pid, sortedByPCA),
            "Avg Latency (ms)": 0 // TODO: Calculate average latency if available
        };

        // Add per-dimension scores
        dimensions.forEach(dim => {
             row[dim.name] = item.res.avgScores[dim.name];
        });

        return row;
    });
    const wsOverview = XLSX.utils.json_to_sheet(overviewData);
    XLSX.utils.book_append_sheet(wb, wsOverview, "Overview");

    // 2. Details Sheet (Wide Format)
    const detailsData: any[] = [];
    
    // Determine source of data (Batch vs Single)
    const isBatchExcel = allRunsData.length > 0;
    
    if (isBatchExcel) {
        // Excel Batch Mode: allRunsData = [Run1Result, Run2Result...]
        allRunsData.forEach((runResult, runIdx) => {
            const pids = Object.keys(runResult);
            if (pids.length === 0) return;
            
            // Assuming all products have same questions in a run
            const firstPid = pids[0];
            const questions = runResult[firstPid].runs || [];
            
            questions.forEach((qItem: any, qIdx: number) => {
                const row: any = {
                    "Run #": runIdx + 1,
                    "Question": qItem.question || `Q${qIdx+1}`
                };
                
                pids.forEach(pid => {
                    let p = products.find(prod => prod.id === pid);
                    const pName = p ? p.name : pid;
                    const res = runResult[pid].runs[qIdx];
                    
                    if (res) {
                        row[`${pName} Answer`] = res.answer;
                        row[`${pName} Score`] = JSON.stringify(res.scores);
                        row[`${pName} Reasoning`] = res.reasoning;
                        // row[`${pName} Latency`] = res.latency; // If available
                    }
                });
                detailsData.push(row);
            });
        });
    } else {
        // Single/Judge Mode: aggregatedResults[pid].runs = [Run1, Run2...] (where each run is 1 question)
        // Actually for Single Eval, "runs" stores the history of single evaluations.
        // Each entry in 'runs' is one evaluation of the current scenario/text.
        
        const pids = Object.keys(aggregatedResults);
        if (pids.length > 0) {
            const firstPid = pids[0];
            const numRuns = aggregatedResults[firstPid].runs.length;
            
            for (let i = 0; i < numRuns; i++) {
                const row: any = {
                    "Run #": i + 1,
                    "Question": allQuestions[0] || scenario // Usually Single Eval has one current question, but history might be different?
                    // Wait, Single Eval 'runs' accumulates history. 'allQuestions' might track them?
                    // Check logic: setAllQuestions([autoText || scenario]) (Line 636) - it overwrites!
                    // So we might lose history questions if we don't track them.
                    // But 'runs' has the result. Does 'runs' item have 'question'?
                    // Let's check backend return for Single Eval.
                };
                // If 'runs' item doesn't have question, we might fallback to current 'autoText'.
                
                pids.forEach(pid => {
                    let p = products.find(prod => prod.id === pid);
                    const pName = p ? p.name : pid;
                    const res = aggregatedResults[pid].runs[i];
                    
                    if (res) {
                         // Check if res has question
                         if (res.question && !row["Question"]) row["Question"] = res.question;
                         
                         row[`${pName} Answer`] = res.answer;
                         row[`${pName} Score`] = JSON.stringify(res.scores);
                         row[`${pName} Reasoning`] = res.reasoning;
                    }
                });
                if (!row["Question"]) row["Question"] = "Unknown (Single Eval)";
                detailsData.push(row);
            }
        }
    }
    
    const wsDetails = XLSX.utils.json_to_sheet(detailsData);
    XLSX.utils.book_append_sheet(wb, wsDetails, "Details");

    // 3. Dimensions Sheet
    const wsDims = XLSX.utils.json_to_sheet(dimensions);
    XLSX.utils.book_append_sheet(wb, wsDims, "Dimensions");

    XLSX.writeFile(wb, `PTSD_Report_${timestamp}.xlsx`);
  };

  const toggleRow = (idx: number) => {
      setExpandedRows(prev => 
          prev.includes(idx) ? prev.filter(i => i !== idx) : [...prev, idx]
      );
  };

  const getLeaderboard = () => {
      return Object.keys(aggregatedResults).map(pid => {
          let p = products.find(prod => prod.id === pid);
          if (!p) {
               p = { id: pid, name: pid, type: 'judge-only', url: '', key: '' };
          }
          
          const res = aggregatedResults[pid];
          let finalScore = 0;
          
          if (scoringMethod === "pca") {
              // Use PCA Score directly
              finalScore = advancedStats.pca_scores?.[pid] || 0;
          } else {
              // Weighted Sum (Standard or Entropy)
              let totalScore = 0;
              let totalWeight = 0;
              
              dimensions.forEach(dim => {
                  if (dim.type === "categorical") return;
                  
                  let w = dim.weight;
                  if (scoringMethod === "entropy" && advancedStats.entropy_weights) {
                      w = advancedStats.entropy_weights[dim.name] || 0;
                  }
                  
                  let val = res.avgScores[dim.name] || 0;
                  // Normalize Binary to 0-10
                  if (dim.type === "binary" && val <= 1) val *= 10;
                  
                  totalScore += val * w;
                  totalWeight += (scoringMethod === "entropy" ? w : 10 * w); 
                  // Note: Entropy weights sum to 1. Standard weights are 1-10.
                  // Wait, for standard weighted avg: score = Sum(v*w) / Sum(10*w) * 10
                  // For entropy: Sum(v*w) where Sum(w)=1. v is 0-10.
              });
              
              if (scoringMethod === "entropy") {
                   finalScore = totalScore; // v is 0-10, w sums to 1. Result is 0-10.
              } else {
                   // Standard
                   let maxPossible = 0;
                   dimensions.forEach(d => { if(d.type!=="categorical") maxPossible += 10 * d.weight; });
                   finalScore = maxPossible > 0 ? (totalScore / maxPossible) * 100 : 0; // Wait, previous was / max * 10
                   // Previous: (totalScore / maxScore) * 10
                   // Revert to previous logic for standard
                   let stdTotal = 0;
                   let stdMax = 0;
                   dimensions.forEach(dim => {
                        if (dim.type === "categorical") return;
                        let val = res.avgScores[dim.name] || 0;
                        if (dim.type === "binary" && val <= 1) val *= 10;
                        stdTotal += val * dim.weight;
                        stdMax += 10 * dim.weight;
                   });
                   finalScore = stdMax > 0 ? (stdTotal / stdMax) * 10 : 0;
              }
          }
          
          return { ...p, score: finalScore.toFixed(1), runs: res.runs.length };
      }).filter(Boolean).sort((a: any, b: any) => parseFloat(b.score) - parseFloat(a.score));
  };

  const getTrendData = () => {
      // Prioritize batchRunHistory for trend
      if (batchRunHistory.length > 0) {
          return batchRunHistory.map(h => {
              const point: any = { run: h.run };
              Object.keys(h).forEach(key => {
                  if (key !== "run") {
                      point[key] = h[key];
                  }
              });
              return point;
          });
      }
      
      const pids = Object.keys(aggregatedResults);
      if (pids.length === 0) return [];

      // Fallback: If no history (e.g. single run or judge only), create a single point
      const point: any = { run: 1 };
      pids.forEach(pid => {
            let p = products.find(prod => prod.id === pid);
            const name = p ? p.name : pid;
            // Calculate current score
            const res = aggregatedResults[pid];
            let totalScore = 0;
            let maxScore = 0;
            dimensions.forEach(dim => {
                if (dim.type === "categorical") return;
                let val = res.avgScores[dim.name] || 0;
                if (dim.type === "binary" && val <= 1) val *= 10;
                totalScore += val * dim.weight;
                maxScore += 10 * dim.weight;
            });
            const weightedAvg = maxScore > 0 ? (totalScore / maxScore) * 10 : 0;
            point[name] = parseFloat(weightedAvg.toFixed(1));
      });
      return [point];
  };

  const handleHumanReviewSync = (humanScores: {[key: string]: any}) => {
      // 1. Create a deep copy of current states
      const newAllRunsData = [...allRunsData];
      const newAggregatedResults = { ...aggregatedResults };
      
      // Collect updates for backend persistence
      const backendUpdates: {row: number, model: string, scores: any, file_id?: string}[] = [];
      
      // 2. Iterate through all human modified scores
      Object.keys(humanScores).forEach(qId => {
          const modelScores = humanScores[qId];
          Object.keys(modelScores).forEach(modelId => {
              const data = modelScores[modelId];
              const meta = data._metadata;
              if (!meta) return;

              const { pid, runIdx, qIdx } = meta;
              const rowIdx = meta.rowIdx ?? qIdx;
              const cleanScores = { ...data };
              delete cleanScores._metadata;

              // Collect for backend save
              backendUpdates.push({
                  row: rowIdx,
                  model: pid,
                  scores: cleanScores,
              });

              // Update All Runs Data
              if (newAllRunsData[runIdx] && newAllRunsData[runIdx][pid]) {
                  const runItem = newAllRunsData[runIdx][pid].runs[qIdx];
                  if (runItem) {
                      runItem.scores = cleanScores;
                  }
              }

              // Update Aggregated Results (for Single Mode fallback or quick view)
              if (runIdx === 0 && newAggregatedResults[pid]) {
                  const runItem = newAggregatedResults[pid].runs[qIdx];
                  if (runItem) {
                      runItem.scores = cleanScores;
                  }
              }
          });
      });

      // 3. Recalculate Averages for Aggregated Results
       // This is crucial for the Dashboard Radar Chart and Summary
       Object.keys(newAggregatedResults).forEach(pid => {
           const result = newAggregatedResults[pid];
           const allScoresAcrossRuns: {[dim: string]: number[]} = {};
           
           const isBatch = newAllRunsData.length > 0;
           const source = isBatch ? newAllRunsData : [newAggregatedResults];

           // Collect all scores for this product from all runs
           source.forEach(runData => {
               if (runData[pid] && runData[pid].runs) {
                   runData[pid].runs.forEach((qItem: any) => {
                       Object.keys(qItem.scores).forEach(dim => {
                           if (typeof qItem.scores[dim] === 'number') {
                               if (!allScoresAcrossRuns[dim]) allScoresAcrossRuns[dim] = [];
                               allScoresAcrossRuns[dim].push(qItem.scores[dim]);
                           }
                       });
                   });
               }
           });

          // Compute new averages
          const newAvgScores: {[dim: string]: number} = {};
          Object.keys(allScoresAcrossRuns).forEach(dim => {
              const values = allScoresAcrossRuns[dim];
              const avg = values.reduce((a, b) => a + b, 0) / values.length;
              newAvgScores[dim] = parseFloat(avg.toFixed(1));
          });

          result.avgScores = newAvgScores;
      });

      // 4. Recalculate Trend History (for Trend Chart)
      if (batchRunHistory.length > 0) {
          const newHistory = batchRunHistory.map((h, rIdx) => {
              const point = { ...h };
              const runData = newAllRunsData[rIdx];
              if (runData) {
                  Object.keys(runData).forEach(pid => {
                      const p = products.find(prod => prod.id === pid);
                      if (p) {
                          const result = runData[pid];
                          let totalScore = 0;
                          let maxScore = 0;
                          dimensions.forEach(dim => {
                              if (dim.type === "categorical") return;
                              let val = result.avgScores ? result.avgScores[dim.name] : 0;
                              // If avgScores is not updated per run, we might need to compute it here
                              // But in batch evaluation, each run has its own avgScores for that specific run
                              
                              // Re-calculate run-level average if necessary
                              const runScores = result.runs.map((r: any) => r.scores[dim.name]).filter((v: any) => typeof v === 'number');
                              val = runScores.length > 0 ? runScores.reduce((a: number, b: number) => a + b, 0) / runScores.length : 0;

                              if (dim.type === "binary" && val <= 1) val *= 10;
                              totalScore += val * dim.weight;
                              maxScore += 10 * dim.weight;
                          });
                          const weightedAvg = maxScore > 0 ? (totalScore / maxScore) * 10 : 0;
                          point[p.name] = parseFloat(weightedAvg.toFixed(1));
                      }
                  });
              }
              return point;
          });
          setBatchRunHistory(newHistory);
      }

      // 5. Update state to trigger re-render of Dashboard
      setAllRunsData(newAllRunsData);
      setAggregatedResults(newAggregatedResults);
      
      // 6. Persist to backend if viewing a history run
      if (currentRunId && backendUpdates.length > 0) {
          saveHumanReviewScores(backendUpdates).then(success => {
              if (success) {
                  console.log(`[INFO] ${backendUpdates.length} score updates saved to disk for ${currentRunId}`);
              }
          });
      }
  };

  const humanReviewData = useMemo(() => {
    // Determine source: AllRunsData (Batch) or AggregatedResults (Single/Legacy)
    // In Batch Mode, allRunsData contains an array of results for each run.
    // In Single Mode, allRunsData state is empty, so we fall back to aggregatedResults.
    const isBatchMode = allRunsData && allRunsData.length > 0;
    const sourceData = isBatchMode ? allRunsData : [aggregatedResults];
    
    if (sourceData.length === 0 || Object.keys(sourceData[0]).length === 0) return null;

    const productsList = products; 
    const processedQuestions: { [key: string]: any } = {};

    sourceData.forEach((runData, runIdx) => {
        Object.keys(runData).forEach(pid => {
            const product = productsList.find(p => p.id === pid) || { id: pid, name: pid, type: "unknown", url: "", key: "" };
            const prodRunData = runData[pid];
            
            if (prodRunData.runs && Array.isArray(prodRunData.runs)) {
                prodRunData.runs.forEach((runItem: any, qIdx: number) => {
                    const qText = runItem.question || allQuestions[qIdx] || `Question ${qIdx + 1}`;
                    const qId = `q-${qIdx}`; 

                    if (!processedQuestions[qId]) {
                        processedQuestions[qId] = {
                            id: qId,
                            text: qText,
                            answers: []
                        };
                    }
                    
                    // Create composite Model Name if multiple runs in Batch Mode
                    let displayModelName = product.name;
                    if (isBatchMode && sourceData.length > 1) {
                        displayModelName = `${product.name} (Run ${runIdx + 1})`;
                    }

                    processedQuestions[qId].answers.push({
                        modelId: displayModelName, 
                        content: runItem.answer || "(No answer)",
                        scores: runItem.scores || {},
                        reasoning: runItem.reasoning || "",
                        reviewed: false,
                        sourceMetadata: {
                            pid: pid,
                            runIdx: runIdx,
                            qIdx: qIdx,
                            rowIdx: runItem._row_idx ?? qIdx,  // for backend score update
                        }
                    });
                });
            }
        });
    });

    return {
        scenario: scenario || "Auto-Evaluation Session",
        dimensions: dimensions,
        questions: Object.values(processedQuestions)
    };
  }, [aggregatedResults, allRunsData, products, dimensions, scenario, allQuestions]);


  // ── Page routing ──
  const PAGE_TITLE: Record<string, string> = { workbench: "评测工作台", dashboard: "评测报告", review: "人工复核", models: "模型管理", alignment: "数据批量导入" };
  const NAV_ICONS: Record<string, any> = { workbench: FlaskConical, dashboard: BarChart2, review: Users, models: Cpu, alignment: Upload };

  return (
    <div className="app-shell">
      {/* ── Sidebar ── */}
      <nav className="nav-sidebar">
        <div className="nav-logo">
          <div className="nav-logo-icon">
            <svg width="20" height="20" viewBox="0 0 32 32"><path d="M11 7 L21 7 L21 19 A5 5 0 0 1 11 19 Z" stroke="#fff" strokeWidth="2.5" fill="none" strokeLinecap="round" /><circle cx="16" cy="20" r="2" fill="#fff" /></svg>
          </div>
          <div><div className="nav-logo-text">PTSD</div><div className="nav-logo-sub">Product Test Smart Dog</div></div>
        </div>
        <div className="nav-section">WORKSPACE</div>
        <div className="nav-items">
          {(["workbench","dashboard","review","models","alignment"] as const).map(id => {
            const Icon = NAV_ICONS[id];
            return (
              <div key={id} className={clsx("nav-item", activePage === id && "active")} onClick={() => setActivePage(id)}>
                <div className="nav-item-icon"><Icon size={18} /></div>
                <span>{PAGE_TITLE[id]}</span>
                {id === "dashboard" && Object.keys(aggregatedResults).length > 0 && <span style={{marginLeft:'auto',width:6,height:6,borderRadius:'50%',background:'#34d399'}} />}
              </div>
            );
          })}
        </div>
        <div className="nav-footer">v2.0</div>
      </nav>

      {/* ── Main Area ── */}
      <div className="main-area">
        <div className="page-header">
          <div className="page-header-title">{(() => { const I = NAV_ICONS[activePage]; return <I size={20} />; })()}{PAGE_TITLE[activePage]}</div>
          <div className="page-header-actions">
            {activePage === "workbench" && <button className="btn btn-secondary btn-sm" onClick={() => setActivePage("models")}><Settings size={14} /> 模型配置</button>}
          </div>
        </div>
        <div className="page-body">

          {/* ── Global Evaluation Status Bar ── */}
          {(isEvaluating || zipProjectRunning) && (
            <div style={{
              position:'sticky', top:0, zIndex:30,
              background: zipProjectRunning && !isEvaluating ? 'linear-gradient(135deg, #f5f3ff, #ede9fe)' : 'linear-gradient(135deg, #fef3c7, #fde68a)',
              border: zipProjectRunning && !isEvaluating ? '1px solid #8b5cf6' : '1px solid #f59e0b',
              borderRadius:'var(--radius-md)',
              padding:'0.75rem 1.25rem',
              marginBottom:'1rem',
              display:'flex', alignItems:'center', justifyContent:'space-between',
              boxShadow: zipProjectRunning && !isEvaluating ? '0 2px 8px rgba(139, 92, 246, 0.2)' : '0 2px 8px rgba(245, 158, 11, 0.2)',
            }}>
              <div style={{display:'flex',alignItems:'center',gap:10}}>
                <span className="animate-spin-slow" style={{display:'inline-block',fontSize:'1.1rem'}}>⚙️</span>
                <div>
                  <div style={{fontWeight:700,fontSize:'0.875rem',color: zipProjectRunning && !isEvaluating ? '#5b21b6' : '#92400e'}}>
                    {zipProjectRunning && !isEvaluating
                      ? `📦 ZIP 批量评测中 — ${zipCurrentFile}`
                      : `评测进行中${currentRun > 0 ? ` (Run ${currentRun}/${runCount})` : '...'}`
                    }
                  </div>
                  <div style={{fontSize:'0.75rem',color: zipProjectRunning && !isEvaluating ? '#7c3aed' : '#b45309'}}>
                    {zipProjectRunning && !isEvaluating
                      ? `ZIP 项目模式${zipProgress ? ` · ${zipProgress.percent || 0}%` : ''}`
                      : <>
                          {evaluationMode === 'judge-only' ? 'Judge Only 模式' : evaluationMode === 'batch' ? 'Excel Batch 模式' : 'Single Case 模式'}
                          {evalSessionId && <span style={{marginLeft:8,opacity:0.6}}>Session: {evalSessionId}</span>}
                        </>
                    }
                  </div>
                </div>
              </div>
              <button
                className="btn btn-sm"
                onClick={zipProjectRunning && !isEvaluating ? handleAbortZipProject : handleAbortEvaluation}
                disabled={isAborting}
                style={{
                  background: isAborting ? '#fca5a5' : '#dc2626',
                  color: '#fff',
                  borderColor: isAborting ? '#fca5a5' : '#dc2626',
                  minWidth:80,
                }}
              >
                {isAborting ? (
                  <><span className="animate-spin-slow" style={{display:'inline-block'}}>⏳</span> 中断中</>
                ) : (
                  <><StopCircle size={14} /> 中断评测</>
                )}
              </button>
            </div>
          )}

          {/* ═══ WORKBENCH ═══ */}
          {activePage === "workbench" && (
            <div className="animate-fade-in" style={{maxWidth:1200,margin:'0 auto'}}>
              {/* Scenario */}
              <div className="card" style={{marginBottom:'1.5rem'}}>
                <div className="card-header"><span>📋 评测场景</span><button className="btn btn-primary btn-sm" onClick={handleGenerateDimensions} disabled={isGeneratingDims || !scenario}><Sparkles size={14} /> {isGeneratingDims ? "生成中..." : "AI 生成维度"}</button></div>
                <div className="card-body"><textarea className="form-textarea" rows={3} value={scenario} onChange={e => setScenario(e.target.value)} placeholder="描述评测场景..." style={{fontSize:'0.9rem'}} /></div>
              </div>
              {/* Dimensions */}
              <div className="card" style={{marginBottom:'1.5rem'}}>
                <div className="card-header">
                  <span>📊 评测维度 ({dimensions.length})</span>
                  <div style={{display:'flex',gap:6,alignItems:'center'}}>
                    <select className="form-select" style={{width:130,height:30,fontSize:'0.75rem'}} onChange={e => { if(e.target.value) loadPreset(e.target.value); }}><option value="">加载预设...</option>{Object.keys(dimensionPresets).map(k => <option key={k} value={k}>{k}</option>)}</select>
                    <button className="btn btn-sm btn-ghost" onClick={() => setShowPresetSave(!showPresetSave)}><Settings size={14} /></button>
                    <button className="btn btn-sm btn-primary" onClick={handleAddDimension}><Plus size={14} /> 添加</button>
                  </div>
                </div>
                {showPresetSave && (
                  <div style={{padding:'0.75rem 1.25rem',borderBottom:'1px solid var(--border)',background:'#ecfdf5',display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
                    <input className="form-input" placeholder="预设名称" value={presetName} onChange={e=>setPresetName(e.target.value)} style={{width:160,height:30,fontSize:'0.8rem'}} />
                    <button className="btn btn-sm btn-primary" onClick={savePreset}>保存</button>
                    {Object.keys(dimensionPresets).map(k => (<span key={k} className="badge badge-green">{k} <button onClick={()=>deletePreset(k)} style={{border:'none',background:'none',color:'#dc2626',cursor:'pointer',padding:0,marginLeft:4}}>×</button></span>))}
                  </div>
                )}
                <div className="card-body" style={{padding: dimensions.length > 0 ? '1rem 1.25rem' : '2rem'}}>
                  {dimensions.length > 0 ? (
                    <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.75rem'}}>
                      {dimensions.map((d, i) => (
                        <div key={i} style={{padding:'0.75rem',borderRadius:8,border:'1px solid var(--border)',background:'#fafbfc',display:'flex',gap:'0.75rem'}}>
                          <div style={{flex:1,display:'flex',flexDirection:'column',gap:4,minWidth:0}}>
                            <div style={{display:'flex',alignItems:'center',gap:6}}>
                              <span className="badge badge-green" style={{fontSize:'0.65rem'}}>#{i+1}</span>
                              <input className="form-input" style={{height:28,fontSize:'0.85rem',fontWeight:600}} value={d.name} onChange={e=>handleDimensionChange(i,'name',e.target.value)} />
                              <select className="form-select" style={{width:100,height:28,fontSize:'0.75rem'}} value={d.type||"scale"} onChange={e=>handleDimensionChange(i,'type',e.target.value)}><option value="scale">Scale (1-10)</option><option value="binary">Binary</option><option value="categorical">Categorical</option></select>
                            </div>
                            <textarea className="form-textarea" style={{fontSize:'0.8rem',minHeight:40,resize:'vertical'}} value={d.description} onChange={e=>handleDimensionChange(i,'description',e.target.value)} />
                            {d.type === "categorical" && <input className="form-input" style={{height:28,fontSize:'0.8rem'}} placeholder="选项(逗号分隔)" value={d.options?.join(", ")||""} onChange={e=>handleDimensionChange(i,'options',e.target.value.split(",").map((s: string)=>s.trim()).filter(Boolean))} />}
                          </div>
                          <div style={{display:'flex',flexDirection:'column',alignItems:'center',gap:4,minWidth:50}}>
                            <span style={{fontSize:'0.65rem',fontWeight:600,color:'var(--text-muted)'}}>权重</span>
                            <input type="number" min={1} max={10} className="form-input" style={{width:44,height:30,textAlign:'center',fontSize:'0.9rem',padding:0}} value={d.weight} onChange={e=>handleDimensionChange(i,'weight',parseInt(e.target.value)||1)} />
                            <button className="btn btn-sm btn-danger" style={{padding:'2px 6px'}} onClick={()=>handleRemoveDimension(i)}><Trash2 size={12} /></button>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (<div style={{textAlign:'center',color:'var(--text-muted)'}}>输入场景后点击 "AI 生成维度"，或手动添加</div>)}
                </div>
              </div>
              {/* Product Selection */}
              {evaluationMode !== "judge-only" && (
              <div className="card" style={{marginBottom:'1.5rem'}}>
                <div className="card-header"><span>🎯 参与评测的产品</span><button className="btn btn-sm btn-ghost" onClick={() => setActivePage("models")}><Settings size={14} /> 管理</button></div>
                <div className="card-body" style={{padding:'0.75rem 1.25rem'}}>
                  <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
                    {products.map(p => (<button key={p.id} onClick={() => toggleProductSelection(p.id)} className={clsx("btn btn-sm", selectedProductIds.includes(p.id) ? "btn-primary" : "btn-secondary")}>{selectedProductIds.includes(p.id) ? <CheckSquare size={14} /> : <Square size={14} />} {p.name}</button>))}
                    {products.length === 0 && <span style={{fontSize:'0.85rem',color:'var(--text-muted)'}}>暂无产品</span>}
                  </div>
                </div>
              </div>
              )}
              {/* Data Source */}
              <div className="card" style={{marginBottom:'1.5rem'}}>
                <div className="card-header">
                  <span>📁 数据来源</span>
                  <div className="tab-group">
                    <button className={clsx("tab-item", evaluationMode === "single" && "active")} onClick={() => setEvaluationMode("single")}>Single Case</button>
                    <button className={clsx("tab-item", evaluationMode === "batch" && "active")} onClick={() => setEvaluationMode("batch")}>Excel Batch</button>
                    <button className={clsx("tab-item", evaluationMode === "judge-only" && "active")} onClick={() => setEvaluationMode("judge-only")}>Judge Only</button>
                  </div>
                </div>
                <div className="card-body">
                  {evaluationMode === "single" && (
                    <div>
                      <div className="form-label">测试 Prompt</div>
                      <textarea className="form-textarea" rows={3} value={autoText} onChange={e => setAutoText(e.target.value)} placeholder="输入 prompt..." />
                      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'1rem',marginTop:'0.75rem'}}>
                        <div><div className="form-label">图片 URL</div><input className="form-input" value={autoImageUrls} onChange={e => setAutoImageUrls(e.target.value)} placeholder="https://..." /></div>
                        <div><div className="form-label">或上传图片</div><label className="file-drop" style={{padding:'0.5rem'}}><Upload size={16} /> {autoImageFiles.length > 0 ? `${autoImageFiles.length} 文件` : "上传"}<input type="file" multiple hidden onChange={e => setAutoImageFiles(Array.from(e.target.files || []))} /></label></div>
                      </div>
                      <div style={{display:'flex',gap:'0.75rem',alignItems:'center',marginTop:'1rem'}}>
                        <button className="btn btn-primary btn-lg" style={{flex:1}} onClick={runBatchEvaluation} disabled={isEvaluating || dimensions.length === 0}>{isEvaluating ? `评测中 ${currentRun}/${runCount}...` : <><Play size={16} /> 开始评测</>}</button>
                        {isEvaluating && (
                          <button className="btn btn-danger btn-lg" onClick={handleAbortEvaluation} disabled={isAborting} style={{minWidth:90}}>
                            {isAborting ? <><span className="animate-spin-slow" style={{display:'inline-block'}}>⏳</span> 中断中...</> : <><StopCircle size={16} /> 中断</>}
                          </button>
                        )}
                        <div style={{display:'flex',alignItems:'center',gap:6}}><span style={{fontSize:'0.8rem',color:'var(--text-muted)'}}>次数:</span><input className="form-input" type="number" min={1} max={1000} style={{width:60,height:34}} value={runCount} onChange={e => { const v = parseInt(e.target.value); if (!isNaN(v) && v > 0) setRunCount(v); }} disabled={isEvaluating} /></div>
                      </div>
                    </div>
                  )}
                  {evaluationMode === "batch" && (
                    <div>
                      <div className="form-hint" style={{marginBottom:8}}>A列 Prompt，B-E列图片</div>
                      <label className="file-drop"><Upload size={24} /><span style={{fontWeight:500}}>{excelFile ? excelFile.name : "上传 Excel"}</span><input type="file" accept=".xlsx,.xls" hidden onChange={e => setExcelFile(e.target.files?.[0] || null)} /></label>
                      <div style={{display:'flex',gap:'0.75rem',alignItems:'center',marginTop:'1rem'}}>
                        <button className="btn btn-primary btn-lg" style={{flex:1}} onClick={runBatchEvaluation} disabled={isEvaluating || dimensions.length === 0}>{isEvaluating ? `评测中 ${currentRun}/${runCount}...` : <><Play size={16} /> Batch 评测</>}</button>
                        {isEvaluating && (
                          <button className="btn btn-danger btn-lg" onClick={handleAbortEvaluation} disabled={isAborting} style={{minWidth:90}}>
                            {isAborting ? <><span className="animate-spin-slow" style={{display:'inline-block'}}>⏳</span> 中断中...</> : <><StopCircle size={16} /> 中断</>}
                          </button>
                        )}
                        <div style={{display:'flex',alignItems:'center',gap:6}}><span style={{fontSize:'0.8rem',color:'var(--text-muted)'}}>次数:</span><input className="form-input" type="number" min={1} max={1000} style={{width:60,height:34}} value={runCount} onChange={e => { const v = parseInt(e.target.value); if (!isNaN(v) && v > 0) setRunCount(v); }} disabled={isEvaluating} /></div>
                      </div>
                    </div>
                  )}
                  {evaluationMode === "judge-only" && (
                    <div>
                      <div style={{display:'flex',gap:6,marginBottom:'1rem'}}>
                        {(["excel","json","jsonl","zip"] as const).map(ft => (<button key={ft} className={clsx("btn btn-sm", judgeFileType === ft ? "btn-primary" : "btn-secondary")} onClick={() => { setJudgeFileType(ft); if(ft==="zip") setJudgeApiVersion("v2"); }} style={ft === "zip" && judgeFileType === "zip" ? {background:'#8b5cf6',borderColor:'#8b5cf6'} : {}}>{ft === "zip" ? "📦 ZIP" : ft.toUpperCase()}</button>))}
                      </div>
                      {judgeFileType === "zip" ? (
                        <div>
                          <label className="file-drop" style={{borderColor:'#c4b5fd',background:'#faf5ff'}}><Upload size={24} style={{color:'#8b5cf6'}} /><span style={{fontWeight:500,color:'#7c3aed'}}>{zipParsing ? "解析中..." : (judgeExcelFile ? judgeExcelFile.name : "上传 ZIP")}</span><input type="file" accept=".zip" hidden onChange={e => { if(e.target.files?.[0]) { setJudgeExcelFile(e.target.files[0]); handleZipUpload(e.target.files[0]); } }} disabled={zipParsing} /></label>
                          {zipManifest && (
                            <div style={{marginTop:'1rem',padding:'1rem',background:'#faf5ff',borderRadius:8,border:'1px solid #c4b5fd'}}>
                              <div style={{fontWeight:600,color:'#7c3aed',marginBottom:8}}>📋 {zipManifest.manifest?.description || "项目"}</div>
                              <div style={{fontSize:'0.8rem',color:'var(--text-muted)',marginBottom:12,display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                                <span>行数: {zipManifest.manifest?.total_rows||0} · 文件: {zipManifest.files?.length||0}</span>
                                <div style={{display:'flex',gap:4}}>
                                  <button onClick={() => setZipSelectedFiles((zipManifest.files || []).filter((f: any) => f.exists_in_zip).map((f: any) => f.file_id))} style={{fontSize:'0.7rem', padding:'0.15rem 0.4rem', border:'1px solid #c4b5fd', borderRadius:'3px', background:'white', cursor:'pointer', color:'#7c3aed'}}>全选</button>
                                  <button onClick={() => setZipSelectedFiles([])} style={{fontSize:'0.7rem', padding:'0.15rem 0.4rem', border:'1px solid #c4b5fd', borderRadius:'3px', background:'white', cursor:'pointer', color:'#7c3aed'}}>清空</button>
                                </div>
                              </div>
                              <div style={{maxHeight:160,overflowY:'auto',border:'1px solid #ddd6fe',borderRadius:6,padding:8,background:'#fff',marginBottom:12}}>
                                {(zipManifest.files||[]).map((f:any)=>(<div key={f.file_id} style={{display:'flex',alignItems:'center',gap:8,padding:'6px 4px',borderBottom:'1px solid #f3f4f6'}}><input type="checkbox" checked={zipSelectedFiles.includes(f.file_id)} disabled={!f.exists_in_zip} onChange={e=>{if(e.target.checked)setZipSelectedFiles(p=>[...p,f.file_id]);else setZipSelectedFiles(p=>p.filter(id=>id!==f.file_id));}} /><div style={{flex:1}}><span style={{fontWeight:500,fontSize:'0.85rem',color:f.exists_in_zip?'inherit':'#94a3b8'}}>{f.file_id}</span><span style={{fontSize:'0.75rem',color:'var(--text-muted)',marginLeft:8}}>{f.row_count} 行{f.origin_input_chars > 0 ? ` | 原文 ${Math.round(f.origin_input_chars/1000)}k字` : ''}{f.use_file_context === false ? <span style={{color:'#f59e0b',marginLeft:4}}>⚡滑窗{f.context_window||5}行</span> : <span style={{color:'#22c55e',marginLeft:4}}>📄全文传入</span>}</span></div>{!f.exists_in_zip && <span style={{fontSize:'0.7rem',color:'#ef4444'}}>文件缺失</span>}</div>))}
                              </div>
                              <button className="btn btn-primary btn-lg" style={{width:'100%',background:'#8b5cf6',borderColor:'#8b5cf6'}} onClick={runZipProject} disabled={zipProjectRunning||zipSelectedFiles.length===0||dimensions.length===0}>{zipProjectRunning ? `⏳ ${zipCurrentFile}` : `🚀 批量评测 (${zipSelectedFiles.length} 文件)`}</button>
                              {zipProjectRunning && (
                                <button className="btn btn-danger btn-lg" style={{width:'100%',marginTop:8}} onClick={handleAbortZipProject}>
                                  <StopCircle size={16} /> 中断评测
                                </button>
                              )}
                              {zipProjectRunning && zipProgress && (<div style={{marginTop:12}}><div className="progress-bar"><div className="progress-bar-fill" style={{width:`${zipProgress.percent||0}%`,background:'linear-gradient(90deg,#8b5cf6,#a78bfa)'}} /></div></div>)}
                              {zipProjectResults && (<div style={{marginTop:12,padding:12,background:'#f0fdf4',borderRadius:8,border:'1px solid #86efac'}}><span style={{fontWeight:600,color:'#166534'}}>✅ 完成</span> · {zipProjectResults.project_summary?.success_files}/{zipProjectResults.project_summary?.total_files} 文件{zipProjectResults.run_id && <> · <a href={`http://localhost:8000/api/eval-results/${zipProjectResults.run_id}/csv`} style={{color:'#059669'}} download>📥 CSV</a></>}</div>)}
                            </div>
                          )}
                        </div>
                      ) : (
                        <div>
                          <label className="file-drop"><Upload size={24} /><span style={{fontWeight:500}}>{detectingFields ? "检测中..." : (judgeExcelFile ? judgeExcelFile.name : "上传文件")}</span><input type="file" accept={judgeFileType==="excel"?".xlsx,.xls":judgeFileType==="json"?".json":".jsonl"} hidden onChange={e => { if(e.target.files?.[0]) handleJudgeOnlyUpload(e.target.files[0]); }} disabled={detectingFields} /></label>
                          {(judgeFileType === "json" || judgeFileType === "jsonl") && (<div style={{marginTop:'0.75rem'}}><div className="form-label">Question Prefix (可选)</div><textarea className="form-textarea" rows={2} value={questionPrefix} onChange={e => setQuestionPrefix(e.target.value)} placeholder="前缀..." /></div>)}
                          {/* V1/V2 */}
                          <div style={{marginTop:'1rem',padding:'0.75rem',background:'#fafbfc',borderRadius:8,border:'1px solid var(--border)'}}>
                            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:8}}>
                              <span className="form-label" style={{margin:0}}>评测模式</span>
                              <div className="tab-group"><button className={clsx("tab-item", judgeApiVersion==="v1" && "active")} onClick={()=>setJudgeApiVersion("v1")}>V1 逐行</button><button className={clsx("tab-item", judgeApiVersion==="v2" && "active")} onClick={()=>setJudgeApiVersion("v2")}>V2 批量</button></div>
                            </div>
                            <div className="form-hint">{judgeApiVersion==="v1"?"每行独立评分":"多行+全局上下文一起评分"}</div>
                            {judgeApiVersion === "v2" && (
                              <div style={{marginTop:'0.75rem',padding:'0.75rem',background:'#fff',borderRadius:6,border:'1px solid #c4b5fd'}}>
                                <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.75rem',marginBottom:'0.75rem'}}>
                                  <div><div className="form-label">每批行数</div><input className="form-input" type="number" min={1} max={10} value={batchSize} onChange={e=>setBatchSize(Math.max(1,Math.min(10,parseInt(e.target.value)||3)))} /></div>
                                  <div><div className="form-label">并发数</div><input className="form-input" type="number" min={1} max={10} value={batchConcurrency} onChange={e=>setBatchConcurrency(Math.max(1,Math.min(10,parseInt(e.target.value)||3)))} /></div>
                                </div>
                                <div className="form-label">全局参考上下文</div>
                                <div style={{display:'flex',gap:6,marginBottom:8}}>{(["none","text","file"] as const).map(m=>(<button key={m} className={clsx("btn btn-sm",fileContextMode===m?"btn-primary":"btn-secondary")} onClick={()=>setFileContextMode(m)}>{m==="none"?"不使用":m==="text"?"文本":"文件"}</button>))}</div>
                                {fileContextMode==="text" && <textarea className="form-textarea" rows={3} value={fileContextText} onChange={e=>setFileContextText(e.target.value)} placeholder="参考文本..." />}
                                {fileContextMode==="file" && <label className="file-drop" style={{padding:'0.5rem'}}><Upload size={14} /> {fileContextFile ? fileContextFile.name : "上传 .txt"}<input type="file" accept=".txt,.md" hidden onChange={e=>setFileContextFile(e.target.files?.[0]||null)} /></label>}
                              </div>
                            )}
                          </div>
                          {/* Mapping */}
                          {showMappingModal && (
                            <div className="card" style={{marginTop:'1rem'}}>
                              <div className="card-header">字段映射</div>
                              <div className="card-body">
                                {sampleRecord && (judgeFileType==="json"||judgeFileType==="jsonl") && (<div className="code-block" style={{marginBottom:'1rem',maxHeight:100,overflow:'auto',fontSize:'0.7rem'}}>{JSON.stringify(sampleRecord,null,2)}</div>)}
                                <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.75rem',marginBottom:'0.75rem'}}>
                                  <div><div className="form-label">Question *</div><select className="form-select" value={judgeMapping.question} onChange={e=>setJudgeMapping(p=>({...p,question:e.target.value}))}><option value="">选择...</option>{excelHeaders.map(h=><option key={h} value={h}>{h}</option>)}</select></div>
                                  <div><div className="form-label">Image</div><select className="form-select" value={judgeMapping.image} onChange={e=>setJudgeMapping(p=>({...p,image:e.target.value}))}><option value="">无</option>{excelHeaders.map(h=><option key={h} value={h}>{h}</option>)}</select></div>
                                  <div><div className="form-label">Runtime</div><select className="form-select" value={judgeMapping.runtime} onChange={e=>setJudgeMapping(p=>({...p,runtime:e.target.value}))}><option value="">无</option>{excelHeaders.map(h=><option key={h} value={h}>{h}</option>)}</select></div>
                                </div>
                                <div className="form-label">Model Answer (多选)</div>
                                <div style={{maxHeight:150,overflowY:'auto',border:'1px solid var(--border)',borderRadius:6,padding:8,marginBottom:12}}>
                                  {excelHeaders.map(h=>(<label key={h} style={{display:'flex',alignItems:'center',gap:8,padding:'3px 0',opacity:(h===judgeMapping.question||h===judgeMapping.image||h===judgeMapping.runtime||contextFieldCols.includes(h))?0.4:1}}><input type="checkbox" checked={judgeMapping.models.includes(h)} disabled={h===judgeMapping.question||h===judgeMapping.image||h===judgeMapping.runtime||contextFieldCols.includes(h)} onChange={e=>setJudgeMapping(p=>({...p,models:e.target.checked?[...p.models,h]:p.models.filter(m=>m!==h)}))} /><span style={{fontSize:'0.85rem'}}>{h}</span></label>))}
                                </div>
                                {judgeApiVersion === "v2" && (
                                  <div style={{padding:'0.75rem',background:'#faf5ff',borderRadius:6,border:'1px solid #c4b5fd',marginBottom:12}}>
                                    <div className="form-label" style={{color:'#7c3aed'}}>📎 Context Fields</div>
                                    <div style={{maxHeight:100,overflowY:'auto',border:'1px solid #ddd6fe',borderRadius:6,padding:8,background:'#fff'}}>
                                      {excelHeaders.filter(h=>h!==judgeMapping.question&&!judgeMapping.models.includes(h)&&h!==judgeMapping.image&&h!==judgeMapping.runtime).map(h=>(<label key={h} style={{display:'flex',alignItems:'center',gap:8,padding:'2px 0'}}><input type="checkbox" checked={contextFieldCols.includes(h)} onChange={e=>setContextFieldCols(p=>e.target.checked?[...p,h]:p.filter(c=>c!==h))} /><span style={{fontSize:'0.85rem'}}>{h}</span></label>))}
                                    </div>
                                  </div>
                                )}
                                <div style={{display:'flex',gap:8,alignItems:'center'}}>
                                  <button className="btn btn-primary btn-lg" style={{flex:1}} onClick={runJudgeOnly} disabled={isEvaluating||!judgeExcelFile||dimensions.length===0}>{isEvaluating ? "评测中..." : "🚀 Judge Only 评测"}</button>
                                  {isEvaluating && (
                                    <button className="btn btn-danger btn-lg" onClick={handleAbortEvaluation} disabled={isAborting} style={{minWidth:100}}>
                                      {isAborting ? "中断中..." : <><StopCircle size={16} /> 中断</>}
                                    </button>
                                  )}
                                </div>
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {/* Request Preview */}
              <div className="card" style={{marginBottom:'1.5rem'}}>
                <div className="card-header" style={{cursor:'pointer'}} onClick={() => setShowRequestPreview(!showRequestPreview)}>
                  <span><Code size={14} style={{display:'inline',marginRight:4}} /> 请求预览</span>
                  <ChevronDown size={14} style={{transform: showRequestPreview ? 'rotate(180deg)' : 'none', transition:'transform 0.2s'}} />
                </div>
                {showRequestPreview && (
                  <div className="card-body">
                    <div className="code-block" style={{maxHeight:400,overflow:'auto'}}>
                      {JSON.stringify(getRequestPreview(), null, 2)}
                    </div>
                    {evaluationMode === "judge-only" && (
                      <div style={{marginTop:12}}>
                        <button
                          className="btn btn-secondary"
                          onClick={fetchPromptPreview}
                          disabled={loadingPromptPreview || !judgeExcelFile || dimensions.length === 0 || (judgeFileType !== "zip" && (!judgeMapping.question || judgeMapping.models.length === 0))}
                          style={{display:'inline-flex',alignItems:'center',gap:6}}
                        >
                          <Eye size={14} />
                          {loadingPromptPreview ? "构建中..." : judgeFileType === "zip" ? `🔍 预览实际 Prompt（ZIP首文件·${zipManifest?.eval_config?.recommended_batch_size || 3}条/批）` : "🔍 预览实际 Prompt（前2条数据）"}
                        </button>
                        <span style={{marginLeft:8,fontSize:12,color:'var(--text-muted)'}}>
                          {judgeFileType === "zip" ? "从ZIP中读取首个文件数据，按manifest batch_size构建完整 prompt" : "读取文件数据，用评测相同逻辑构建完整 prompt"}
                        </span>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Prompt Preview Modal */}
              {showPromptPreview && promptPreviewData && (
                <div style={{position:'fixed',top:0,left:0,right:0,bottom:0,background:'rgba(0,0,0,0.6)',zIndex:9999,display:'flex',alignItems:'center',justifyContent:'center',padding:20}} onClick={() => setShowPromptPreview(false)}>
                  <div style={{background:'#ffffff',borderRadius:12,maxWidth:1000,width:'100%',maxHeight:'90vh',display:'flex',flexDirection:'column',border:'1px solid #e2e8f0',boxShadow:'0 20px 60px rgba(0,0,0,0.3)'}} onClick={e => e.stopPropagation()}>
                    {/* Modal Header */}
                    <div style={{padding:'16px 20px',borderBottom:'1px solid #e2e8f0',display:'flex',justifyContent:'space-between',alignItems:'center',background:'#f8fafc',borderRadius:'12px 12px 0 0'}}>
                      <div>
                        <h3 style={{margin:0,fontSize:16,color:'#1e293b'}}>🔍 实际 Prompt 预览</h3>
                        {promptPreviewData.status === "success" && (
                          <span style={{fontSize:12,color:'#64748b'}}>
                            {promptPreviewData.preview_file_id && <>{`📦 ${promptPreviewData.preview_file_id}`} · </>}
                            模板: {promptPreviewData.template_source} · 数据行: {promptPreviewData.data_rows_used}/{promptPreviewData.total_data_rows} · 
                            约 {promptPreviewData.prompt_estimated_tokens?.toLocaleString()} tokens · {promptPreviewData.prompt_char_count?.toLocaleString()} 字符
                            {promptPreviewData.file_context_chars > 0 && <> · 全文上下文: {Math.round(promptPreviewData.file_context_chars/1000)}k字</>}
                          </span>
                        )}
                      </div>
                      <button className="btn btn-ghost" onClick={() => setShowPromptPreview(false)} style={{padding:4}}><X size={18} /></button>
                    </div>
                    {/* Modal Body */}
                    <div style={{flex:1,overflow:'auto',padding:20,background:'#ffffff'}}>
                      {promptPreviewData.error ? (
                        <div style={{color:'#dc2626',padding:16,background:'#fef2f2',borderRadius:8,border:'1px solid #fecaca'}}>
                          <AlertTriangle size={16} style={{display:'inline',marginRight:6}} />
                          {promptPreviewData.error}
                        </div>
                      ) : (
                        <>
                          {/* Model Config */}
                          <div style={{marginBottom:16,padding:12,background:'#f1f5f9',borderRadius:8,fontSize:13,color:'#334155',border:'1px solid #e2e8f0'}}>
                            <strong style={{color:'#1e293b'}}>Judge 模型配置</strong>
                            <div style={{marginTop:6,display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(200px,1fr))',gap:'4px 16px'}}>
                              <span>Model: <code style={{background:'#e2e8f0',padding:'1px 4px',borderRadius:3,fontSize:12}}>{promptPreviewData.model_config?.model}</code></span>
                              <span>API: <code style={{background:'#e2e8f0',padding:'1px 4px',borderRadius:3,fontSize:12}}>{promptPreviewData.model_config?.api_url}</code></span>
                              <span>max_output_tokens: <code style={{background:'#e2e8f0',padding:'1px 4px',borderRadius:3,fontSize:12}}>{promptPreviewData.model_config?.max_output_tokens}</code></span>
                              <span>temperature: <code style={{background:'#e2e8f0',padding:'1px 4px',borderRadius:3,fontSize:12}}>{String(promptPreviewData.model_config?.temperature)}</code></span>
                              <span>prompt_cache: <code style={{background:'#e2e8f0',padding:'1px 4px',borderRadius:3,fontSize:12}}>{String(promptPreviewData.model_config?.prompt_cache_enabled)}</code></span>
                              <span>API Version: <code style={{background:'#e2e8f0',padding:'1px 4px',borderRadius:3,fontSize:12}}>{promptPreviewData.api_version}</code></span>
                            </div>
                          </div>

                          {/* Tab: User Prompt / Full Request Body / Cache Request Body */}
                          <div style={{marginBottom:12}}>
                            <div style={{display:'flex',gap:8,marginBottom:8}}>
                              <button className={`btn btn-sm ${!promptPreviewData._activeTab || promptPreviewData._activeTab === 'prompt' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setPromptPreviewData({...promptPreviewData, _activeTab: 'prompt'})}>User Prompt</button>
                              <button className={`btn btn-sm ${promptPreviewData._activeTab === 'request' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setPromptPreviewData({...promptPreviewData, _activeTab: 'request'})}>完整请求体</button>
                              {promptPreviewData.cache_request_body && (
                                <button className={`btn btn-sm ${promptPreviewData._activeTab === 'cache' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setPromptPreviewData({...promptPreviewData, _activeTab: 'cache'})}>Cache 请求体</button>
                              )}
                              <button className={`btn btn-sm ${promptPreviewData._activeTab === 'data' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setPromptPreviewData({...promptPreviewData, _activeTab: 'data'})}>数据样本</button>
                            </div>
                          </div>

                          {/* Content */}
                          {(!promptPreviewData._activeTab || promptPreviewData._activeTab === 'prompt') && (
                            <pre style={{whiteSpace:'pre-wrap',wordBreak:'break-word',background:'#f8fafc',border:'1px solid #e2e8f0',borderRadius:8,padding:16,fontSize:12,lineHeight:1.6,maxHeight:'50vh',overflow:'auto',fontFamily:'monospace',color:'#1e293b'}}>
                              {promptPreviewData.prompt}
                            </pre>
                          )}
                          {promptPreviewData._activeTab === 'request' && (
                            <pre style={{whiteSpace:'pre-wrap',wordBreak:'break-word',background:'#f8fafc',border:'1px solid #e2e8f0',borderRadius:8,padding:16,fontSize:12,lineHeight:1.6,maxHeight:'50vh',overflow:'auto',fontFamily:'monospace',color:'#1e293b'}}>
                              {JSON.stringify(promptPreviewData.request_body, null, 2)}
                            </pre>
                          )}
                          {promptPreviewData._activeTab === 'cache' && promptPreviewData.cache_request_body && (
                            <pre style={{whiteSpace:'pre-wrap',wordBreak:'break-word',background:'#f8fafc',border:'1px solid #e2e8f0',borderRadius:8,padding:16,fontSize:12,lineHeight:1.6,maxHeight:'50vh',overflow:'auto',fontFamily:'monospace',color:'#1e293b'}}>
                              {JSON.stringify(promptPreviewData.cache_request_body, null, 2)}
                            </pre>
                          )}
                          {promptPreviewData._activeTab === 'data' && (
                            <pre style={{whiteSpace:'pre-wrap',wordBreak:'break-word',background:'#f8fafc',border:'1px solid #e2e8f0',borderRadius:8,padding:16,fontSize:12,lineHeight:1.6,maxHeight:'50vh',overflow:'auto',fontFamily:'monospace',color:'#1e293b'}}>
                              {JSON.stringify(promptPreviewData.sample_data, null, 2)}
                            </pre>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {/* Manual Test (collapsible) */}
              <div className="card">
                <div className="card-header" style={{cursor:'pointer'}} onClick={() => setActiveTab(activeTab === "manual" ? "" : "manual")}>
                  <span><Terminal size={14} style={{display:'inline',marginRight:4}} /> 手动测试</span>
                  <ChevronDown size={14} style={{transform: activeTab === "manual" ? 'rotate(180deg)' : 'none', transition:'transform 0.2s'}} />
                </div>
                {activeTab === "manual" && (
                  <div className="card-body">
                    <div style={{marginBottom:'1rem'}}>
                      <div className="form-label">测试输入</div>
                      <textarea className="form-textarea" rows={2} value={text} onChange={e => setText(e.target.value)} placeholder="输入测试文本..." />
                      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.75rem',marginTop:8}}>
                        <div><div className="form-label">图片 URL</div><input className="form-input" value={imageUrls} onChange={e => setImageUrls(e.target.value)} placeholder="https://..." /></div>
                        <div><div className="form-label">上传图片</div><label className="file-drop" style={{padding:'0.375rem'}}><Upload size={14} /> {imageFiles.length > 0 ? `${imageFiles.length} 文件` : "上传"}<input type="file" multiple hidden onChange={e => setImageFiles(Array.from(e.target.files || []))} /></label></div>
                      </div>
                    </div>
                    <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill, minmax(300px, 1fr))',gap:'1rem'}}>
                      {products.map(p => (
                        <div key={p.id} className="card" style={{boxShadow:'none'}}>
                          <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'0.625rem 0.875rem',borderBottom:'1px solid var(--border)'}}>
                            <span style={{fontWeight:600,fontSize:'0.85rem'}}>{p.name}</span>
                            <button className="btn btn-sm btn-primary" onClick={() => handleManualTest(p)} disabled={loading[p.id]}>{loading[p.id] ? "..." : <Play size={12} />}</button>
                          </div>
                          <div style={{padding:'0.75rem',maxHeight:200,overflowY:'auto'}}>
                            {results[p.id] ? (<div className="code-block" style={{fontSize:'0.75rem'}}>{results[p.id].answer ? results[p.id].answer : JSON.stringify(results[p.id], null, 2)}</div>) : (<span style={{color:'var(--text-muted)',fontSize:'0.8rem'}}>等待测试...</span>)}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ═══ DASHBOARD ═══ */}
          {activePage === "dashboard" && (
            <div className="animate-fade-in" style={{maxWidth:1400,margin:'0 auto'}}>
              {/* History Run Info Bar */}
              {currentRunId && Object.keys(aggregatedResults).length > 0 && (
                <div style={{
                  display:'flex',alignItems:'center',justifyContent:'space-between',
                  padding:'0.625rem 1rem',marginBottom:'1rem',
                  background:'linear-gradient(135deg, #eff6ff, #dbeafe)',
                  border:'1px solid #93c5fd',borderRadius:'var(--radius-md)',
                }}>
                  <div style={{display:'flex',alignItems:'center',gap:8}}>
                    <span>📂</span>
                    <div>
                      <div style={{fontWeight:700,fontSize:'0.85rem',color:'#1e40af'}}>历史评测: {currentRunId}</div>
                      <div style={{fontSize:'0.7rem',color:'#3b82f6'}}>人工复核的修改将自动保存到原始评测文件</div>
                    </div>
                  </div>
                  <div style={{display:'flex',gap:6}}>
                    <button className="btn btn-sm" style={{background:'#fff',borderColor:'#93c5fd'}} onClick={() => { setCurrentRunId(null); setAggregatedResults({}); setAllRunsData([]); setBatchRunHistory([]); }}>
                      返回列表
                    </button>
                    <button className="btn btn-sm btn-primary" onClick={() => loadHistoryRun(currentRunId)}>
                      重新加载
                    </button>
                  </div>
                </div>
              )}
              {Object.keys(aggregatedResults).length > 0 ? (
                <>
                  {/* Charts */}
                  <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'1.5rem',marginBottom:'1.5rem'}}>
                    {/* Radar */}
                    <div className="card"><div className="card-header">维度对比</div><div className="card-body"><div style={{height:320}}><ResponsiveContainer width="100%" height="100%"><RadarChart cx="50%" cy="50%" outerRadius="80%" data={getRadarData()}><PolarGrid /><PolarAngleAxis dataKey="subject" /><PolarRadiusAxis angle={30} domain={[0, 10]} />{Object.keys(aggregatedResults).map((pid, i) => { let p = products.find(prod => prod.id === pid); const name = p ? p.name : pid; return <Radar key={pid} name={name} dataKey={name} stroke={COLORS[i % COLORS.length]} fill={COLORS[i % COLORS.length]} fillOpacity={0.3} />; })}<Legend /><RechartsTooltip /></RadarChart></ResponsiveContainer></div></div></div>
                    {/* Leaderboard */}
                    <div className="card"><div className="card-header"><span>排行榜</span><select className="form-select" style={{width:130,height:30,fontSize:'0.75rem'}} value={scoringMethod} onChange={e => setScoringMethod(e.target.value as any)}><option value="weighted">加权平均</option><option value="entropy">熵权法</option><option value="pca">PCA 综合</option></select></div><div className="card-body"><div style={{display:'flex',flexDirection:'column',gap:8}}>{getLeaderboard().map((p: any, i: number) => (<div key={p.id} style={{display:'flex',alignItems:'center',gap:12,padding:'0.75rem 1rem',background:i===0?'rgba(5,150,105,0.08)':'#fafbfc',border:i===0?'1px solid #a7f3d0':'1px solid var(--border)',borderRadius:8}}><div style={{fontSize:'1.25rem',fontWeight:800,width:30,color:i===0?'#059669':'var(--text-muted)'}}>#{i+1}</div><div style={{flex:1}}><div style={{fontWeight:600,fontSize:'0.9rem'}}>{p.name}</div><div style={{fontSize:'0.75rem',color:'var(--text-muted)'}}>Runs: {p.runs}</div></div><div style={{fontSize:'1.5rem',fontWeight:800,color:'#059669'}}>{p.score}</div></div>))}</div></div></div>
                  </div>
                  {/* Trend */}
                  {batchRunHistory.length > 0 && (
                    <div className="card" style={{marginBottom:'1.5rem'}}><div className="card-header"><TrendingUp size={16} /> 稳定性趋势</div><div className="card-body"><div style={{height:280}}><ResponsiveContainer width="100%" height="100%"><LineChart data={getTrendData()}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="run" /><YAxis domain={[0, 10]} /><RechartsTooltip /><Legend />{Object.keys(aggregatedResults).map((pid, i) => { let p = products.find(prod => prod.id === pid); const name = p ? p.name : pid; return <Line key={pid} type="monotone" dataKey={name} stroke={COLORS[i % COLORS.length]} strokeWidth={2} dot={false} />; })}</LineChart></ResponsiveContainer></div></div></div>
                  )}
                  {/* Detailed Table */}
                  <div className="card"><div className="card-header"><span>详细分析</span><div style={{display:'flex',alignItems:'center',gap:8}}>{allRunsData.length > 1 && <select className="form-select" style={{width:120,height:30,fontSize:'0.75rem'}} value={viewRunIndex} onChange={e => setViewRunIndex(parseInt(e.target.value))}>{allRunsData.map((_, i) => <option key={i} value={i}>Run #{i + 1}</option>)}</select>}<button className="btn btn-primary btn-sm" onClick={handleExport}><Download size={14} /> 导出</button></div></div>
                  <div className="card-body" style={{padding:0}}>
                    <div style={{overflowX:'auto',maxHeight:600,overflowY:'auto'}}>
                      <table style={{width:'100%',borderCollapse:'collapse'}}>
                        <thead style={{position:'sticky',top:0,background:'#fff',zIndex:1}}><tr style={{borderBottom:'2px solid var(--border)'}}><th style={{textAlign:'left',padding:'0.75rem 1rem',width:50,fontSize:'0.8rem'}}>#</th><th style={{textAlign:'left',padding:'0.75rem 1rem',fontSize:'0.8rem'}}>Question</th><th style={{textAlign:'center',padding:'0.75rem 1rem',width:100,fontSize:'0.8rem'}}>Models</th><th style={{textAlign:'right',padding:'0.75rem 1rem',width:80,fontSize:'0.8rem'}}>操作</th></tr></thead>
                        <tbody>
                          {allQuestions.length > 0 ? allQuestions.map((q, idx) => {
                            const isExpanded = expandedRows.includes(idx);
                            return (<React.Fragment key={idx}>
                              <tr onClick={() => toggleRow(idx)} style={{cursor:'pointer',background:isExpanded?'#f8fafc':'white',borderBottom:isExpanded?'none':'1px solid var(--border)'}}>
                                <td style={{padding:'0.75rem 1rem',fontWeight:600,fontSize:'0.85rem'}}>{idx+1}</td>
                                <td style={{padding:'0.75rem 1rem',fontSize:'0.85rem',maxWidth:500}}><div style={{display:'flex',alignItems:'center',gap:6}}>{isExpanded?<ChevronDown size={14} />:<ChevronRight size={14} />}<div style={{whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}} title={q}>{q}</div></div></td>
                                <td style={{padding:'0.75rem 1rem',textAlign:'center',color:'var(--text-muted)',fontSize:'0.8rem'}}>{Object.keys(aggregatedResults).length}</td>
                                <td style={{padding:'0.75rem 1rem',textAlign:'right'}}><button className="btn btn-sm btn-ghost">{isExpanded?"收起":"展开"}</button></td>
                              </tr>
                              {isExpanded && (
                                <tr style={{background:'#f8fafc',borderBottom:'1px solid var(--border)'}}>
                                  <td colSpan={4} style={{padding:'0 1rem 1rem'}}>
                                    <div style={{display:'grid',gridTemplateColumns:`repeat(${Math.min(Object.keys(aggregatedResults).length,3)},1fr)`,gap:'1rem'}}>
                                      {Object.keys(aggregatedResults).map(pid => {
                                        let p = products.find(prod => prod.id === pid); if(!p) p = {id:pid,name:pid,type:'judge-only',url:'',key:''};
                                        const rd = allRunsData[viewRunIndex]?allRunsData[viewRunIndex][pid]:aggregatedResults[pid]; const fd = rd||aggregatedResults[pid];
                                        let ans = "N/A"; if(fd?.allAnswers?.[idx]) ans=fd.allAnswers[idx]; else if(fd?.runs?.[idx]?.answer) ans=fd.runs[idx].answer; else if(fd?.sampleAnswer) ans=fd.sampleAnswer;
                                        let lat = 0; if(fd?.allLatencies?.[idx]) lat=fd.allLatencies[idx];
                                        const run = fd?.runs?.[idx]; const scores = run?.scores||fd?.avgScores; const reasoning = run?.reasoning||fd?.sampleReasoning;
                                        return (
                                          <div key={pid} className="card" style={{boxShadow:'none'}}>
                                            <div style={{padding:'0.625rem 0.875rem',borderBottom:'1px solid var(--border)',background:'#fafbfc',display:'flex',justifyContent:'space-between',alignItems:'center'}}><span style={{fontWeight:700,fontSize:'0.85rem'}}>{p.name}</span>{lat>0&&<span className="badge badge-blue" style={{fontSize:'0.65rem'}}>{lat}ms</span>}</div>
                                            {scores && (<div style={{padding:'0.5rem 0.875rem',borderBottom:'1px solid var(--border)',display:'flex',gap:4,flexWrap:'wrap'}}>{Object.entries(scores).map(([k,v])=>{const n=typeof v==='number'?v:0;return <span key={k} className={`score-badge ${n>=8?'score-high':n>=5?'score-mid':'score-low'}`}>{k.substring(0,5)}: {v as number}</span>;})}</div>)}
                                            <div style={{padding:'0.75rem 0.875rem',maxHeight:300,overflowY:'auto'}}><div className="markdown-content" style={{fontSize:'0.825rem',lineHeight:1.65}}><ReactMarkdown>{ans}</ReactMarkdown></div></div>
                                            {reasoning && (<div style={{padding:'0.625rem 0.875rem',borderTop:'1px solid var(--border)',background:'#f8fafc'}}><div style={{fontSize:'0.65rem',fontWeight:700,textTransform:'uppercase',color:'var(--text-muted)',marginBottom:3}}>Judge 评语</div><div style={{fontSize:'0.775rem',color:'var(--text-muted)',fontStyle:'italic',lineHeight:1.5}}>{reasoning}</div></div>)}
                                          </div>
                                        );
                                      })}
                                    </div>
                                  </td>
                                </tr>
                              )}
                            </React.Fragment>);
                          }) : (<tr><td colSpan={4} style={{padding:'2rem',textAlign:'center',color:'var(--text-muted)'}}>暂无数据</td></tr>)}
                        </tbody>
                      </table>
                    </div>
                  </div></div>
                </>
              ) : (
                <div style={{maxWidth:800,margin:'0 auto'}}>
                  <div className="empty-state" style={{marginBottom:'2rem'}}>
                    <BarChart2 size={48} className="empty-state-icon" />
                    <div className="empty-state-title">暂无评测结果</div>
                    <div className="empty-state-desc">运行评测后结果将在此展示，或从下方加载历史评测</div>
                    <button className="btn btn-primary" style={{marginTop:'1.5rem'}} onClick={() => setActivePage("workbench")}>前往工作台</button>
                  </div>
                  
                  {/* ── History Browser ── */}
                  <div className="card">
                    <div className="card-header">
                      <span>📂 历史评测结果</span>
                      <button className="btn btn-sm btn-primary" onClick={fetchEvalHistory} disabled={loadingHistory}>
                        {loadingHistory ? "加载中..." : "刷新列表"}
                      </button>
                    </div>
                    <div className="card-body" style={{padding:0}}>
                      {evalHistory.length === 0 ? (
                        <div style={{padding:'2rem',textAlign:'center',color:'var(--text-muted)'}}>
                          {loadingHistory ? "正在加载..." : "点击「刷新列表」加载历史评测记录"}
                        </div>
                      ) : (
                        <div style={{maxHeight:400,overflowY:'auto'}}>
                          {evalHistory.map((run: any) => (
                            <div key={run.run_id} style={{
                              display:'flex', alignItems:'center', justifyContent:'space-between',
                              padding:'0.75rem 1rem', borderBottom:'1px solid var(--border)',
                              cursor:'pointer', transition:'background 0.15s',
                            }}
                            onMouseEnter={e => (e.currentTarget.style.background = '#f0fdf4')}
                            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                            onClick={() => loadHistoryRun(run.run_id)}
                            >
                              <div style={{flex:1}}>
                                <div style={{fontWeight:600,fontSize:'0.875rem'}}>{run.run_id}</div>
                                <div style={{fontSize:'0.75rem',color:'var(--text-muted)',marginTop:2}}>
                                  {run.timestamp && new Date(run.timestamp).toLocaleString('zh-CN')}
                                  {run.eval_type && <span style={{marginLeft:8}} className="badge badge-blue">{run.eval_type}</span>}
                                </div>
                                {run.scenario && <div style={{fontSize:'0.7rem',color:'var(--text-muted)',marginTop:2,maxWidth:500,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{run.scenario}</div>}
                              </div>
                              <div style={{textAlign:'right',flexShrink:0}}>
                                <div style={{fontSize:'0.8rem',fontWeight:600,color:'#059669'}}>
                                  {run.summary?.total_rows_evaluated || run.summary?.total_files || 0} {run.summary?.total_rows_evaluated ? '行' : '文件'}
                                </div>
                                {run.dimensions && run.dimensions.length > 0 && (
                                  <div style={{fontSize:'0.65rem',color:'var(--text-muted)',marginTop:2}}>
                                    {run.dimensions.slice(0, 4).join(', ')}{run.dimensions.length > 4 ? `... +${run.dimensions.length - 4}` : ''}
                                  </div>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ═══ HUMAN REVIEW ═══ */}
          {activePage === "review" && (
            <HumanReviewPage initialData={humanReviewData} onSync={handleHumanReviewSync} />
          )}

          {/* ═══ MODEL CONFIG ═══ */}
          {activePage === "models" && (
            <ModelConfigPage />
          )}

          {activePage === "alignment" && (
            <AlignmentPage onImportZip={(file) => {
              setActivePage("workbench");
              // 延迟一帧确保页面已切换
              setTimeout(() => handleZipUpload(file), 100);
            }} />
          )}

        </div>
      </div>
    </div>
  );

}
