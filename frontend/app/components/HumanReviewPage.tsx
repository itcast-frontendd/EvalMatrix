"use client";

import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { CheckCircle, Circle, ChevronRight, Save, Download, Eye, EyeOff, MessageSquare, AlertCircle, Check, GripVertical } from "lucide-react";
import ReactMarkdown from "react-markdown";
import clsx from "clsx";
import * as XLSX from "xlsx";

interface HumanReviewPageProps {
  initialData?: any;
  onSync?: (updatedScores: { [key: string]: any }) => void;
}

interface ReviewDimension {
  name: string;
  description: string;
  weight: number;
  type: string;
  options?: string[];
}

const MOCK_DIMENSIONS: ReviewDimension[] = [
  { name: "Accuracy", description: "Is the technical advice correct?", weight: 10, type: "scale" },
  { name: "Empathy", description: "Does the bot sound supportive?", weight: 8, type: "scale" },
  { name: "Clarity", description: "Are the instructions easy to follow?", weight: 9, type: "scale" },
];

const MOCK_QUESTIONS = [
  { id: "q1", text: "My internet is down, the globe icon is red.", answers: [
    { modelId: "m1", content: "Please try restarting your router by unplugging it for 30 seconds.", scores: { Accuracy: 9, Empathy: 7, Clarity: 10 }, reasoning: "Standard procedure, clear instructions.", reviewed: false },
    { modelId: "m2", content: "Red globe means no internet. Check if the cable is plugged in.", scores: { Accuracy: 8, Empathy: 4, Clarity: 9 }, reasoning: "Correct but a bit blunt.", reviewed: false },
  ]}
];

export default function HumanReviewPage({ initialData, onSync }: HumanReviewPageProps) {
  const [questions, setQuestions] = useState<any[]>([]);
  const [selectedQId, setSelectedQId] = useState("");
  const [humanScores, setHumanScores] = useState<{ [key: string]: any }>({});
  const [showRealNames, setShowRealNames] = useState(false);
  const [dimensions, setDimensions] = useState<ReviewDimension[]>(MOCK_DIMENSIONS);
  const [scenario, setScenario] = useState("Mock Scenario");
  const [isSyncing, setIsSyncing] = useState(false);
  const [answerPanelHeight, setAnswerPanelHeight] = useState<number | null>(null);
  const [isResizing, setIsResizing] = useState(false);
  const resizeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (initialData?.questions?.length > 0) {
      setQuestions(initialData.questions);
      setSelectedQId(initialData.questions[0].id);
      setDimensions(initialData.dimensions || []);
      setScenario(initialData.scenario || "");
      const initialScores: any = {};
      initialData.questions.forEach((q: any) => {
        initialScores[q.id] = {};
        q.answers.forEach((a: any) => { initialScores[q.id][a.modelId] = { ...a.scores, _metadata: a.sourceMetadata }; });
      });
      setHumanScores(initialScores);
    } else {
      setQuestions(MOCK_QUESTIONS);
      setSelectedQId(MOCK_QUESTIONS[0].id);
      const initialScores: any = {};
      MOCK_QUESTIONS.forEach(q => {
        initialScores[q.id] = {};
        q.answers.forEach(a => { initialScores[q.id][a.modelId] = { ...a.scores }; });
      });
      setHumanScores(initialScores);
    }
  }, [initialData]);

  // ── Resize logic ──
  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
    const startY = e.clientY;
    const startH = answerPanelHeight || (resizeRef.current?.offsetHeight || 400);
    const handleMove = (ev: MouseEvent) => {
      const delta = ev.clientY - startY;
      setAnswerPanelHeight(Math.max(150, startH + delta));
    };
    const handleUp = () => { setIsResizing(false); document.removeEventListener("mousemove", handleMove); document.removeEventListener("mouseup", handleUp); };
    document.addEventListener("mousemove", handleMove);
    document.addEventListener("mouseup", handleUp);
  }, [answerPanelHeight]);

  const handleSync = () => {
    if (!onSync) return;
    setIsSyncing(true);
    onSync(humanScores);
    setTimeout(() => { setIsSyncing(false); alert("评分已同步到 Dashboard!"); }, 500);
  };

  const [isClient, setIsClient] = useState(false);
  useEffect(() => { setIsClient(true); }, []);

  const shuffledAnswersMap = useMemo(() => {
    if (!isClient) return {};
    const map: { [key: string]: any[] } = {};
    questions.forEach(q => {
      const shuffled = [...q.answers].sort(() => Math.random() - 0.5);
      map[q.id] = shuffled.map((a, idx) => ({ ...a, alias: `Model ${String.fromCharCode(65 + idx)}` }));
    });
    return map;
  }, [isClient, questions]);

  const currentQuestion = questions.find(q => q.id === selectedQId);
  const currentAnswers = (isClient && currentQuestion) ? shuffledAnswersMap[currentQuestion.id] : [];

  const handleScoreChange = (qId: string, modelId: string, dimName: string, value: any) => {
    setHumanScores(prev => ({ ...prev, [qId]: { ...prev[qId], [modelId]: { ...prev[qId][modelId], [dimName]: value } } }));
  };

  const toggleModelReviewed = (qId: string, modelId: string) => {
    setQuestions(prev => prev.map(q => q.id === qId ? { ...q, answers: q.answers.map((a: any) => a.modelId === modelId ? { ...a, reviewed: !a.reviewed } : a) } : q));
  };

  const isModelReviewed = (qId: string, modelId: string) => {
    const q = questions.find(qu => qu.id === qId);
    return q?.answers.find((a: any) => a.modelId === modelId)?.reviewed || false;
  };

  const handleExport = () => {
    const rows: any[] = [];
    questions.forEach(q => {
      q.answers.forEach((a: any) => {
        const scores = humanScores[q.id]?.[a.modelId] || a.scores;
        let totalScore = 0; let totalWeight = 0;
        dimensions.forEach(dim => {
          if (dim.type === "scale") { totalScore += (typeof scores[dim.name] === "number" ? scores[dim.name] : 0) * (dim.weight || 1); totalWeight += (dim.weight || 1); }
        });
        const row: any = { "Question": q.text, "Model": showRealNames ? a.modelId : a.alias || a.modelId, "Weighted Score": totalWeight > 0 ? (totalScore / totalWeight).toFixed(2) : "0", "Answer": a.content || "", "Reasoning": a.reasoning || "", "Reviewed": a.reviewed ? "Yes" : "No" };
        dimensions.forEach(dim => { row[`Score: ${dim.name}`] = scores[dim.name] ?? "-"; });
        rows.push(row);
      });
    });
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(rows), "Review Results");
    XLSX.writeFile(wb, `human_review_${new Date().toISOString().split("T")[0]}.xlsx`);
  };

  const totalAnswers = questions.reduce((acc, q) => acc + q.answers.length, 0);
  const reviewedAnswers = questions.reduce((acc, q) => acc + q.answers.filter((a: any) => a.reviewed).length, 0);
  const progressPct = totalAnswers > 0 ? (reviewedAnswers / totalAnswers) * 100 : 0;

  return (
    <div className="animate-fade-in" style={{ display: "flex", height: "calc(100vh - 56px)", overflow: "hidden" }}>
      {/* ── Left: Question List ── */}
      <div style={{ width: 280, minWidth: 280, background: "#fff", borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "1rem", borderBottom: "1px solid var(--border)" }}>
          <div style={{ fontWeight: 700, fontSize: "0.9rem", marginBottom: 4 }}>审核队列</div>
          <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>{reviewedAnswers}/{totalAnswers} 已审核</div>
          <div className="progress-bar" style={{ marginTop: 8 }}><div className="progress-bar-fill" style={{ width: `${progressPct}%` }} /></div>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "0.5rem" }}>
          {questions.map((q, idx) => {
            const isFullyReviewed = q.answers.every((a: any) => a.reviewed);
            const revCount = q.answers.filter((a: any) => a.reviewed).length;
            return (
              <button key={q.id} onClick={() => setSelectedQId(q.id)} style={{ width: "100%", textAlign: "left", padding: "0.625rem 0.75rem", borderRadius: 8, fontSize: "0.8rem", display: "flex", alignItems: "flex-start", gap: 8, border: selectedQId === q.id ? "1px solid #a7f3d0" : "1px solid transparent", background: selectedQId === q.id ? "#ecfdf5" : "transparent", cursor: "pointer", marginBottom: 2, transition: "all 0.15s" }}>
                <div style={{ marginTop: 2, color: isFullyReviewed ? "#059669" : "#cbd5e1", flexShrink: 0 }}>
                  {isFullyReviewed ? <CheckCircle size={14} /> : <Circle size={14} />}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: selectedQId === q.id ? 600 : 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: selectedQId === q.id ? "#047857" : "var(--text-main)" }}>
                    Q{idx + 1}: {q.text}
                  </div>
                  <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: 2 }}>
                    {revCount}/{q.answers.length} reviewed
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* ── Right: Review Content ── */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>
        {/* Header bar */}
        <div style={{ padding: "0.75rem 1.25rem", borderBottom: "1px solid var(--border)", background: "#fff", display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", fontWeight: 600 }}>维度:</span>
            {dimensions.map(dim => (
              <span key={dim.name} className="badge badge-green" style={{ fontSize: "0.7rem" }}>{dim.name} ({dim.weight})</span>
            ))}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn-sm btn-secondary" onClick={() => setShowRealNames(!showRealNames)}>
              {showRealNames ? <Eye size={12} /> : <EyeOff size={12} />} {showRealNames ? "显示名称" : "盲审模式"}
            </button>
            <button className="btn btn-sm btn-primary" onClick={handleSync} disabled={isSyncing || !onSync}>
              <Save size={12} /> {isSyncing ? "同步中..." : "同步到Dashboard"}
            </button>
            <button className="btn btn-sm btn-secondary" onClick={handleExport}>
              <Download size={12} /> 导出
            </button>
          </div>
        </div>

        {/* Content */}
        <div style={{ flex: 1, overflowY: "auto", padding: "1.25rem" }}>
          {currentQuestion && (
            <div style={{ maxWidth: 1200, margin: "0 auto" }}>
              {/* Question */}
              <div className="card" style={{ marginBottom: "1.25rem" }}>
                <div className="card-body" style={{ padding: "1rem 1.25rem" }}>
                  <div style={{ fontSize: "0.75rem", fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 6 }}>Question</div>
                  <div style={{ fontSize: "0.9rem", lineHeight: 1.6, whiteSpace: "pre-wrap", maxHeight: 200, overflowY: "auto" }}>
                    {currentQuestion.text}
                  </div>
                </div>
              </div>

              {/* Answer Cards — side by side, with scores visible at top */}
              <div style={{ display: "grid", gridTemplateColumns: `repeat(${Math.min(currentAnswers?.length || 1, 3)}, 1fr)`, gap: "1rem" }}>
                {currentAnswers?.map((ans: any) => {
                  const isReviewed = isModelReviewed(currentQuestion.id, ans.modelId);
                  const scores = humanScores[selectedQId]?.[ans.modelId] || ans.scores;
                  let totalScore = 0; let totalWeight = 0;
                  dimensions.forEach(dim => { if (dim.type === "scale") { totalScore += (typeof scores[dim.name] === "number" ? scores[dim.name] : 0) * (dim.weight || 1); totalWeight += (dim.weight || 1); } });
                  const weightedScore = totalWeight > 0 ? (totalScore / totalWeight).toFixed(1) : "0.0";

                  return (
                    <div key={ans.modelId} className="card" style={{ border: isReviewed ? "1px solid #a7f3d0" : "1px solid var(--border)", display: "flex", flexDirection: "column" }}>
                      {/* Card header with model name + total score */}
                      <div style={{ padding: "0.75rem 1rem", borderBottom: "1px solid var(--border)", background: isReviewed ? "#ecfdf5" : "#fafbfc", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <div style={{ width: 24, height: 24, borderRadius: "50%", background: "#059669", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "0.7rem", fontWeight: 700 }}>
                            {ans.alias?.split(" ")[1] || "?"}
                          </div>
                          <span style={{ fontWeight: 600, fontSize: "0.85rem" }}>{showRealNames ? ans.modelId : ans.alias}</span>
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span style={{ fontSize: "1.1rem", fontWeight: 800, color: "#059669" }}>{weightedScore}</span>
                          <button onClick={() => toggleModelReviewed(currentQuestion.id, ans.modelId)} style={{ background: "none", border: "none", cursor: "pointer", color: isReviewed ? "#059669" : "#cbd5e1" }}>
                            <CheckCircle size={18} />
                          </button>
                        </div>
                      </div>

                      {/* Scores bar — compact, always visible */}
                      <div style={{ padding: "0.5rem 1rem", borderBottom: "1px solid var(--border)", background: "#fafbfc" }}>
                        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                          {dimensions.map(dim => {
                            const currentScore = humanScores[selectedQId]?.[ans.modelId]?.[dim.name] ?? ans.scores[dim.name];
                            const dimType = dim.type || "scale";
                            
                            // Infer type from value if type is "scale" but value is clearly not numeric
                            let effectiveType = dimType;
                            if (effectiveType === "scale") {
                              const sv = String(currentScore).toLowerCase().trim();
                              if (sv === "true" || sv === "false" || typeof currentScore === "boolean") {
                                effectiveType = "binary";
                              } else if (currentScore !== undefined && currentScore !== null && isNaN(Number(currentScore))) {
                                effectiveType = "categorical";
                              }
                            }
                            
                            // Collect unique options for categorical from all answers if dim.options missing
                            let catOptions = dim.options || [];
                            if (effectiveType === "categorical" && catOptions.length === 0) {
                              const optSet = new Set<string>();
                              questions.forEach((q: any) => {
                                q.answers.forEach((a: any) => {
                                  const val = a.scores?.[dim.name];
                                  if (val !== undefined && val !== null) optSet.add(String(val));
                                  const hVal = humanScores[q.id]?.[a.modelId]?.[dim.name];
                                  if (hVal !== undefined && hVal !== null) optSet.add(String(hVal));
                                });
                              });
                              catOptions = Array.from(optSet).sort();
                            }
                            
                            return (
                              <div key={dim.name} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                <span style={{ fontSize: "0.7rem", fontWeight: 600, color: "var(--text-muted)", width: 60, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flexShrink: 0 }} title={`${dim.description || dim.name} (${effectiveType})`}>{dim.name}</span>
                                {effectiveType === "scale" && (
                                  <div style={{ flex: 1, display: "flex", alignItems: "center", gap: 6 }}>
                                    <input type="range" min={1} max={10} value={typeof currentScore === "number" ? currentScore : 5} onChange={e => handleScoreChange(selectedQId, ans.modelId, dim.name, Number(e.target.value))} style={{ flex: 1, height: 4, accentColor: "#059669" }} />
                                    <span style={{ fontSize: "0.75rem", fontWeight: 700, width: 16, textAlign: "right" }}>{typeof currentScore === "number" ? currentScore : "-"}</span>
                                  </div>
                                )}
                                {effectiveType === "binary" && (
                                  <button onClick={() => {
                                    const boolVal = String(currentScore).toLowerCase();
                                    const isTrue = boolVal === "true" || boolVal === "1" || boolVal === "yes" || currentScore === true;
                                    handleScoreChange(selectedQId, ans.modelId, dim.name, !isTrue);
                                  }} className={`btn btn-sm ${(String(currentScore).toLowerCase() === "true" || currentScore === true || String(currentScore) === "1") ? "btn-primary" : "btn-danger"}`} style={{ fontSize: "0.7rem", padding: "1px 8px" }}>
                                    {(String(currentScore).toLowerCase() === "true" || currentScore === true || String(currentScore) === "1") ? "Yes" : "No"}
                                  </button>
                                )}
                                {effectiveType === "categorical" && (
                                  <select className="form-select" style={{ height: 26, fontSize: "0.75rem", flex: 1 }} value={String(currentScore ?? "")} onChange={e => handleScoreChange(selectedQId, ans.modelId, dim.name, e.target.value)}>
                                    {catOptions.length > 0 
                                      ? catOptions.map(opt => <option key={opt} value={opt}>{opt}</option>)
                                      : <option value={String(currentScore ?? "")}>{String(currentScore ?? "(none)")}</option>
                                    }
                                  </select>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>

                      {/* Answer content — resizable */}
                      <div ref={resizeRef} className="resizable-panel" style={{ flex: answerPanelHeight ? "none" : 1, height: answerPanelHeight || undefined, minHeight: 150, overflowY: "auto", padding: "1rem" }}>
                        <div className="markdown-content" style={{ fontSize: "0.85rem", color: "var(--text-main)", lineHeight: 1.7 }}>
                          <ReactMarkdown components={{
                            p: ({ node, ...props }) => <p style={{ marginBottom: "0.5rem" }} {...props} />,
                            strong: ({ node, ...props }) => <strong style={{ fontWeight: 700 }} {...props} />,
                            code: ({ node, ...props }) => <code style={{ background: "#f1f5f9", padding: "1px 3px", borderRadius: 3, fontSize: "0.85em" }} {...props} />,
                          }}>
                            {ans.content}
                          </ReactMarkdown>
                        </div>
                        {/* Resize handle */}
                        <div className="resize-handle" onMouseDown={handleResizeStart} title="拖动调整答案区域高度" />
                      </div>

                      {/* Reasoning */}
                      {ans.reasoning && (
                        <div style={{ padding: "0.75rem 1rem", borderTop: "1px solid var(--border)", background: "#f8fafc" }}>
                          <div style={{ fontSize: "0.65rem", fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 4 }}>Judge 评语</div>
                          <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", fontStyle: "italic", lineHeight: 1.5 }}>
                            {ans.reasoning}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
