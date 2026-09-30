import { useCallback, useEffect, useMemo, useState } from "react";

const API = "";

function authHeaders(apiKey) {
  return {
    "X-API-Key": apiKey,
  };
}

async function api(path, { apiKey, method = "GET", body, formData } = {}) {
  const headers = { ...authHeaders(apiKey) };
  let payload = body;
  if (body && !formData) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(`${API}${path}`, {
    method,
    headers: formData ? authHeaders(apiKey) : headers,
    body: formData || payload,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}

export default function App() {
  const [apiKey, setApiKey] = useState(
    () => localStorage.getItem("pie_api_key") || "pie_dev_key_change_me"
  );
  const [cases, setCases] = useState([]);
  const [selectedCase, setSelectedCase] = useState(null);
  const [caseDetail, setCaseDetail] = useState(null);
  const [selectedDoc, setSelectedDoc] = useState(null);
  const [pages, setPages] = useState([]);
  const [pageNum, setPageNum] = useState(1);
  const [evidence, setEvidence] = useState([]);
  const [facts, setFacts] = useState([]);
  const [whyFact, setWhyFact] = useState(null);
  const [job, setJob] = useState(null);
  const [intelligence, setIntelligence] = useState(null);
  const [title, setTitle] = useState("Sample title diligence");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [imageUrl, setImageUrl] = useState(null);
  const [chatInput, setChatInput] = useState("Who is the current owner?");
  const [chatMessages, setChatMessages] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [chatBusy, setChatBusy] = useState(false);
  const [reviewTasks, setReviewTasks] = useState([]);
  const [reviewSeverity, setReviewSeverity] = useState("");
  const [reviewStatus, setReviewStatus] = useState("OPEN");
  const [reviewType, setReviewType] = useState("");
  const [auditEvents, setAuditEvents] = useState([]);
  const [decideBusy, setDecideBusy] = useState(null);
  const [report, setReport] = useState(null);
  const [reportBusy, setReportBusy] = useState(false);
  const [opsSummary, setOpsSummary] = useState(null);

  const saveKey = () => {
    localStorage.setItem("pie_api_key", apiKey);
    setStatus("API key saved");
  };

  const refreshCases = useCallback(async () => {
    setError("");
    const data = await api("/v1/cases", { apiKey });
    setCases(data);
  }, [apiKey]);

  const createCase = async () => {
    setError("");
    const c = await api("/v1/cases", {
      apiKey,
      method: "POST",
      body: { title, description: "Created from PIE web UI" },
    });
    await refreshCases();
    setSelectedCase(c.id);
    setStatus(`Created case ${c.id}`);
  };

  const loadFacts = useCallback(
    async (caseId, docId) => {
      const q = docId ? `?document_id=${docId}` : "";
      const data = await api(`/v1/cases/${caseId}/facts${q}`, { apiKey });
      setFacts(data);
    },
    [apiKey]
  );

  const loadIntelligence = useCallback(
    async (caseId) => {
      try {
        const data = await api(`/v1/cases/${caseId}/intelligence`, { apiKey });
        setIntelligence(data);
      } catch {
        setIntelligence(null);
      }
    },
    [apiKey]
  );

  const loadReviewTasks = useCallback(
    async (caseId, { status, severity, taskType } = {}) => {
      if (!caseId) return;
      const params = new URLSearchParams();
      const st = status ?? reviewStatus;
      const sev = severity ?? reviewSeverity;
      const tt = taskType ?? reviewType;
      if (st) params.set("status", st);
      if (sev) params.set("severity", sev);
      if (tt) params.set("task_type", tt);
      const q = params.toString() ? `?${params}` : "";
      try {
        const data = await api(`/v1/cases/${caseId}/review-tasks${q}`, { apiKey });
        setReviewTasks(data);
      } catch {
        setReviewTasks([]);
      }
    },
    [apiKey, reviewStatus, reviewSeverity, reviewType]
  );

  const loadAudit = useCallback(
    async (caseId) => {
      if (!caseId) return;
      try {
        const data = await api(`/v1/cases/${caseId}/audit-events?limit=40`, { apiKey });
        setAuditEvents(data);
      } catch {
        setAuditEvents([]);
      }
    },
    [apiKey]
  );

  const loadReport = useCallback(
    async (caseId, { regenerate = false } = {}) => {
      if (!caseId) return;
      setReportBusy(true);
      try {
        const q = regenerate ? "?regenerate=true" : "";
        const data = await api(`/v1/cases/${caseId}/report${q}`, { apiKey });
        setReport(data);
      } catch {
        setReport(null);
      } finally {
        setReportBusy(false);
      }
    },
    [apiKey]
  );

  const loadOps = useCallback(async () => {
    try {
      const data = await api("/v1/ops/summary", { apiKey });
      setOpsSummary(data);
    } catch {
      setOpsSummary(null);
    }
  }, [apiKey]);

  useEffect(() => {
    refreshCases().catch((e) => setError(String(e.message || e)));
    loadOps().catch(() => {});
  }, [refreshCases, loadOps]);

  const runReprocess = async (mode = "delta") => {
    if (!selectedCase) return;
    setError("");
    setStatus(`Reprocess (${mode})…`);
    try {
      const res = await api(`/v1/cases/${selectedCase}/reprocess`, {
        apiKey,
        method: "POST",
        body: { mode, force: mode === "prompt_bump" },
      });
      setStatus(
        `Reprocess queued · ${res.job_ids?.length || 0} jobs · invalidated ${res.invalidated_stage_runs || 0}`
      );
      await loadOps();
    } catch (e) {
      setError(e.message || String(e));
    }
  };

  const runAnalyze = async () => {
    if (!selectedCase) return;
    setError("");
    setStatus("Analyzing…");
    try {
      const res = await api(`/v1/cases/${selectedCase}/analyze`, {
        apiKey,
        method: "POST",
        body: { mode: "incremental", sync: true, generate_report: true },
      });
      setStatus(
        `Analyze ${res.status}${res.report_id ? ` · report ${res.report_id}` : ""}`
      );
      await loadIntelligence(selectedCase);
      await loadReport(selectedCase);
      await loadAudit(selectedCase);
    } catch (e) {
      setError(e.message || String(e));
    }
  };

  const loadCase = useCallback(
    async (caseId) => {
      setError("");
      const detail = await api(`/v1/cases/${caseId}`, { apiKey });
      setCaseDetail(detail);
      setSelectedCase(caseId);
      setWhyFact(null);
      setChatMessages([]);
      setConversationId(null);
      await loadIntelligence(caseId);
      await loadReviewTasks(caseId);
      await loadAudit(caseId);
      await loadReport(caseId);
      if (detail.documents?.length) {
        setSelectedDoc(detail.documents[0].id);
      } else {
        setSelectedDoc(null);
        setPages([]);
        setEvidence([]);
        setFacts([]);
        setIntelligence(null);
        setReviewTasks([]);
        setAuditEvents([]);
        setReport(null);
      }
    },
    [apiKey, loadIntelligence, loadReviewTasks, loadAudit, loadReport]
  );

  useEffect(() => {
    if (selectedCase) {
      loadCase(selectedCase).catch((e) => setError(String(e.message || e)));
    }
  }, [selectedCase, loadCase]);

  const uploadFile = async (file) => {
    if (!selectedCase || !file) return;
    setError("");
    setStatus("Uploading…");
    const fd = new FormData();
    fd.append("file", file);
    fd.append("role", "primary");
    const result = await api(`/v1/cases/${selectedCase}/documents/complete`, {
      apiKey,
      method: "POST",
      formData: fd,
    });
    setStatus(`Uploaded. Job ${result.job_id}${result.deduped ? " (deduped)" : ""}`);
    setSelectedDoc(result.document.id);
    pollJob(result.job_id);
    await loadCase(selectedCase);
  };

  const pollJob = async (jobId) => {
    for (let i = 0; i < 60; i++) {
      const j = await api(`/v1/jobs/${jobId}`, { apiKey });
      setJob(j);
      if (j.status === "succeeded" || j.status === "failed") {
        setStatus(`Job ${j.status}`);
        const docId = selectedDoc || j.document_id;
        if (selectedCase && docId) {
          setSelectedDoc(docId);
          await loadEvidence(selectedCase, docId);
          await loadPages(selectedCase, docId);
          await loadFacts(selectedCase, null);
        }
        if (selectedCase && j.status === "succeeded") {
          for (let k = 0; k < 10; k++) {
            await new Promise((r) => setTimeout(r, 700));
            const intel = await api(`/v1/cases/${selectedCase}/intelligence`, { apiKey });
            setIntelligence(intel);
            if (intel?.scorecard) break;
          }
          await loadReviewTasks(selectedCase);
          await loadAudit(selectedCase);
          await loadReport(selectedCase);
        }
        return;
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
    setStatus("Job still running…");
  };

  const loadPages = async (caseId, docId) => {
    const p = await api(`/v1/cases/${caseId}/documents/${docId}/pages`, { apiKey });
    setPages(p);
    if (p.length) setPageNum(p[0].page_number);
  };

  const loadEvidence = async (caseId, docId) => {
    const ev = await api(`/v1/cases/${caseId}/documents/${docId}/evidence`, { apiKey });
    setEvidence(ev);
  };

  const openWhy = async (fact) => {
    if (!selectedCase) return;
    setError("");
    const detail = await api(`/v1/cases/${selectedCase}/facts/${fact.fact_id}`, { apiKey });
    setWhyFact(detail);
    if (detail.evidence?.length) {
      setPageNum(detail.evidence[0].page_number);
    }
  };

  const confirmFact = async (fact) => {
    if (!selectedCase) return;
    setError("");
    await api(`/v1/cases/${selectedCase}/facts/${fact.fact_id}/confirm`, {
      apiKey,
      method: "POST",
      body: {},
    });
    setStatus(`Confirmed ${fact.fact_id} → VERIFIED`);
    await loadFacts(selectedCase, null);
  };

  const rejectFact = async (fact) => {
    if (!selectedCase) return;
    setError("");
    await api(`/v1/cases/${selectedCase}/facts/${fact.fact_id}/reject`, {
      apiKey,
      method: "POST",
      body: {},
    });
    setStatus(`Rejected ${fact.fact_id}`);
    await loadFacts(selectedCase, null);
  };

  const decideReview = async (task, action, extra = {}) => {
    if (!selectedCase) return;
    setError("");
    setDecideBusy(`${task.task_id}:${action}`);
    try {
      let body = { action, ...extra };
      if (action === "approve" && (task.related_fact_ids || []).length > 1 && !extra.selected_fact_id) {
        const pick = window.prompt(
          `Approve which fact_id?\n${(task.related_fact_ids || []).join("\n")}`,
          task.related_fact_ids[0]
        );
        if (!pick) return;
        body.selected_fact_id = pick;
      }
      if (action === "merge" && !extra.preferred_name && !extra.selected_fact_id) {
        const pick = window.prompt("Preferred name / spelling for merge:", "");
        if (!pick) return;
        body.preferred_name = pick;
      }
      if (action === "annotate") {
        const note = window.prompt("Annotation note:", "");
        if (!note) return;
        body.note = note;
      }
      if (action === "request_docs") {
        const label = window.prompt("Document to request:", task.title || "");
        if (label) body.requested_doc_label = label;
      }
      const res = await api(`/v1/cases/${selectedCase}/review-tasks/${task.task_id}/decide`, {
        apiKey,
        method: "POST",
        body,
      });
      setStatus(`Review ${action}: ${res.decision.decision_id}`);
      await loadReviewTasks(selectedCase);
      await loadFacts(selectedCase, null);
      await loadIntelligence(selectedCase);
      await loadAudit(selectedCase);
      await loadReport(selectedCase);
    } finally {
      setDecideBusy(null);
    }
  };

  const flagLastAnswer = async () => {
    if (!selectedCase) return;
    const last = [...chatMessages].reverse().find((m) => m.role === "assistant");
    if (!last) {
      setError("No assistant answer to flag");
      return;
    }
    setError("");
    await api(`/v1/cases/${selectedCase}/review-tasks/flag`, {
      apiKey,
      method: "POST",
      body: {
        summary: `User flagged chat answer (${last.status || "n/a"}): ${last.content.slice(0, 400)}`,
        conversation_message_id: last.message_id || null,
        severity: "MEDIUM",
      },
    });
    setStatus("Flagged answer for review");
    await loadReviewTasks(selectedCase);
    await loadAudit(selectedCase);
  };

  const sendChat = async () => {
    if (!selectedCase || !chatInput.trim()) return;
    setError("");
    setChatBusy(true);
    const userMsg = chatInput.trim();
    setChatMessages((m) => [...m, { role: "user", content: userMsg }]);
    setChatInput("");
    try {
      const body = { message: userMsg };
      if (conversationId) body.conversation_id = conversationId;
      const res = await api(`/v1/cases/${selectedCase}/chat`, {
        apiKey,
        method: "POST",
        body,
      });
      setConversationId(res.conversation_id);
      setChatMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: res.answer,
          status: res.status,
          evidence: res.evidence || [],
          conflicts: res.conflicts || [],
          missing_evidence: res.missing_evidence || [],
          paths: res.retrieval_paths || [],
          message_id: res.message_id,
        },
      ]);
      const firstEv = (res.evidence || []).find((e) => e.page);
      if (firstEv?.page) setPageNum(firstEv.page);
      if (firstEv?.document_id) setSelectedDoc(firstEv.document_id);
    } finally {
      setChatBusy(false);
    }
  };

  useEffect(() => {
    if (!selectedCase || !selectedDoc) return;
    loadPages(selectedCase, selectedDoc).catch(() => {});
    loadEvidence(selectedCase, selectedDoc).catch(() => {});
    loadFacts(selectedCase, null).catch(() => {});
  }, [selectedCase, selectedDoc, apiKey, loadFacts]);

  useEffect(() => {
    let objectUrl;
    async function loadImage() {
      if (!selectedCase || !selectedDoc || !pageNum) {
        setImageUrl(null);
        return;
      }
      const res = await fetch(
        `${API}/v1/cases/${selectedCase}/documents/${selectedDoc}/pages/${pageNum}/image`,
        { headers: authHeaders(apiKey) }
      );
      if (!res.ok) {
        setImageUrl(null);
        return;
      }
      const blob = await res.blob();
      objectUrl = URL.createObjectURL(blob);
      setImageUrl(objectUrl);
    }
    loadImage();
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [selectedCase, selectedDoc, pageNum, apiKey]);

  const pageEvidence = useMemo(() => {
    if (whyFact?.evidence?.length) {
      return whyFact.evidence.filter((e) => e.page_number === pageNum);
    }
    return evidence.filter((e) => e.page_number === pageNum);
  }, [evidence, pageNum, whyFact]);

  const currentPage = pages.find((p) => p.page_number === pageNum);

  return (
    <div className="app">
      <div className="brand">
        <h1>PIE</h1>
        <span>Property Intelligence Engine — review · memory · chat</span>
      </div>
      <p className="lede">
        Upload title documents to a case. PIE compounds facts, conflicts, and memory — then
        answers case-scoped questions with an evidence contract.
      </p>

      <div className="panel">
        <div className="row">
          <label>
            API key
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </label>
          <button type="button" className="secondary" onClick={saveKey}>
            Save key
          </button>
          <button type="button" className="secondary" onClick={() => refreshCases()}>
            Refresh cases
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="row">
          <label>
            New case title
            <input value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <button type="button" onClick={() => createCase().catch((e) => setError(e.message))}>
            Create case
          </button>
        </div>
      </div>

      <div className="grid">
        <div className="panel">
          <h3>Cases</h3>
          <ul className="list">
            {cases.map((c) => (
              <li
                key={c.id}
                className={c.id === selectedCase ? "active" : ""}
                onClick={() => setSelectedCase(c.id)}
              >
                <strong>{c.title}</strong>
                <div className="status">{c.id.slice(0, 8)}…</div>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <div className="panel">
            <h3>Documents</h3>
            {!selectedCase && <p className="status">Select or create a case.</p>}
            {selectedCase && (
              <>
                <div className="row">
                  <input
                    type="file"
                    accept="application/pdf,image/*"
                    onChange={(e) =>
                      uploadFile(e.target.files?.[0]).catch((err) => setError(err.message))
                    }
                  />
                </div>
                <ul className="list">
                  {(caseDetail?.documents || []).map((d) => (
                    <li
                      key={d.id}
                      className={d.id === selectedDoc ? "active" : ""}
                      onClick={() => {
                        setSelectedDoc(d.id);
                        setWhyFact(null);
                      }}
                    >
                      {d.source_filename || d.id}
                      <div className="status">
                        {d.upload_status} · {d.page_count ?? "?"} pages ·{" "}
                        <span className="pill">{d.content_hash.slice(0, 10)}</span>
                      </div>
                    </li>
                  ))}
                </ul>
                {job && (
                  <p className="status">
                    Job {job.status}
                    {job.stages?.length
                      ? ` · ${job.stages.map((s) => `${s.stage_name}:${s.status}${s.skipped ? "(skip)" : ""}`).join(", ")}`
                      : ""}
                  </p>
                )}
              </>
            )}
          </div>

          <div className="panel">
            <div className="row">
              <h3 style={{ margin: 0, flex: 1 }}>Case intelligence</h3>
              {selectedCase && (
                <button
                  type="button"
                  className="secondary"
                  onClick={() =>
                    api(`/v1/cases/${selectedCase}/reconcile?sync=true`, {
                      apiKey,
                      method: "POST",
                    })
                      .then(() => loadIntelligence(selectedCase))
                      .then(() => loadFacts(selectedCase, null))
                      .then(() => loadReviewTasks(selectedCase))
                      .then(() => loadAudit(selectedCase))
                      .catch((e) => setError(e.message))
                  }
                >
                  Reconcile now
                </button>
              )}
            </div>
            {!intelligence?.scorecard && (
              <p className="status">
                No scorecard yet. Upload documents and wait for reconciliation.
              </p>
            )}
            {intelligence?.scorecard && (
              <>
                <div className="score-strip">
                  <span className="pill warn">
                    {intelligence.open_conflicts_count} open conflicts
                  </span>
                  <span className="pill">
                    {intelligence.missing_evidence_count} missing evidence
                  </span>
                </div>
                <div className="scorecard">
                  {Object.entries(intelligence.scorecard.dimensions || {}).map(([k, v]) => (
                    <div key={k} className="score-cell">
                      <span className="score-key">{k.replace(/_/g, " ")}</span>
                      <span className={`pill score-${String(v).toLowerCase()}`}>{v}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
            {intelligence?.risk && (
              <div className="intel-block">
                <h4>Risk</h4>
                <div className="score-strip">
                  <span
                    className={`pill ${
                      ["HIGH", "CRITICAL"].includes(intelligence.risk.risk_level)
                        ? "danger"
                        : intelligence.risk.risk_level === "MEDIUM"
                          ? "warn"
                          : ""
                    }`}
                  >
                    {intelligence.risk.risk_level}
                  </span>
                  <span className="pill">score {intelligence.risk.score}</span>
                  <span className="status">{intelligence.risk.weights_version}</span>
                </div>
                {!!intelligence.risk.drivers?.length && (
                  <ul className="list risk-drivers">
                    {intelligence.risk.drivers.map((d) => (
                      <li key={`${d.code}-${d.source_id || d.label}`}>
                        <span className="fact-value">
                          {d.label}{" "}
                          <span className="pill warn">+{d.weight}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="status risk-disclaimer">{intelligence.risk.disclaimer}</p>
              </div>
            )}
            {!!intelligence?.ownership_timeline?.length && (
              <div className="intel-block">
                <h4>Ownership chain</h4>
                <ol className="timeline">
                  {intelligence.ownership_timeline.map((e) => (
                    <li key={e.event_id} className={`timeline-item status-${(e.chain_status || "OK").toLowerCase()}`}>
                      <div className="timeline-head">
                        <span className="pill">{e.event_type}</span>{" "}
                        <span className="pill">{e.chain_status || "OK"}</span>{" "}
                        <span className="status">
                          {e.event_date
                            ? new Date(e.event_date).toLocaleDateString()
                            : e.event_date_raw || "undated"}
                        </span>
                      </div>
                      <div className="fact-value">
                        {(e.parties || [])
                          .map((p) => `${p.role}: ${p.display_name || p.person_id?.slice?.(0, 8) || "?"}`)
                          .join(" · ") || "No parties"}
                      </div>
                      {!!e.notes?.length && (
                        <div className="status">{e.notes.join(" ")}</div>
                      )}
                    </li>
                  ))}
                </ol>
              </div>
            )}
            {!!intelligence?.legal_findings?.length && (
              <div className="intel-block">
                <h4>Legal findings</h4>
                <ul className="list">
                  {intelligence.legal_findings.map((f) => (
                    <li key={f.finding_id}>
                      <span className={`pill ${f.status === "UNRESOLVED" ? "danger" : "warn"}`}>
                        {f.status}
                      </span>{" "}
                      <span className="pill">{f.category}</span>
                      <div className="fact-value">{f.statement}</div>
                      {!!f.missing_evidence?.length && (
                        <div className="status">
                          Missing: {f.missing_evidence.join(", ")}
                        </div>
                      )}
                      {f.recommended_action && (
                        <div className="status">{f.recommended_action}</div>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {!!intelligence?.conflicts?.length && (
              <div className="intel-block">
                <h4>Open conflicts</h4>
                <ul className="list">
                  {intelligence.conflicts.map((c) => (
                    <li key={c.conflict_id}>
                      <span className="pill danger">{c.conflict_type}</span>{" "}
                      <span className="pill">{c.status}</span>
                      <div className="fact-value">{c.summary}</div>
                      <div className="status">
                        {(c.facts || [])
                          .map((f) => `${f.fact_type}=${f.value_text || f.value_normalized}`)
                          .join(" · ")}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {!!intelligence?.missing_evidence?.length && (
              <div className="intel-block">
                <h4>Missing evidence</h4>
                <ul className="list">
                  {intelligence.missing_evidence.map((g) => (
                    <li key={g.gap_id}>
                      <span className="pill">{g.gap_type}</span>{" "}
                      <span className="pill">{g.status}</span>
                      <div className="fact-value">{g.referenced_label}</div>
                      <div className="status">{g.summary}</div>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {!!intelligence?.document_graph?.nodes?.length && (
              <div className="intel-block">
                <h4>Document graph</h4>
                <ul className="list graph-list">
                  {intelligence.document_graph.nodes.map((n) => (
                    <li key={n.node_id}>
                      <span className="pill">{n.status || n.node_type}</span> {n.label}
                    </li>
                  ))}
                </ul>
                {!!intelligence.document_graph.edges?.length && (
                  <div className="status" style={{ marginTop: "0.5rem" }}>
                    {intelligence.document_graph.edges.map((e) => (
                      <div key={e.edge_id}>
                        {e.from_node_id} —{e.edge_type}→ {e.to_node_id}{" "}
                        <span className="pill">{e.status}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="panel">
            <div className="row">
              <h3 style={{ margin: 0, flex: 1 }}>Ops</h3>
              <button
                type="button"
                className="secondary"
                onClick={() => loadOps().catch(() => {})}
              >
                Refresh
              </button>
              {selectedCase && (
                <>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => runReprocess("delta").catch((e) => setError(e.message))}
                  >
                    Reprocess delta
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() =>
                      runReprocess("prompt_bump").catch((e) => setError(e.message))
                    }
                  >
                    Prompt bump
                  </button>
                </>
              )}
            </div>
            {opsSummary ? (
              <div className="status" style={{ marginTop: "0.5rem" }}>
                <div>
                  Budget:{" "}
                  <span className="pill">{opsSummary.budget?.status}</span>{" "}
                  ${Number(opsSummary.budget?.spent_usd || 0).toFixed(4)} / $
                  {Number(opsSummary.budget?.budget_usd || 0).toFixed(2)}
                </div>
                <div>
                  Review backlog: {opsSummary.review_backlog?.open_count ?? "—"} · Verified
                  fact ratio:{" "}
                  {opsSummary.verified_fact_ratio?.ratio != null
                    ? `${(opsSummary.verified_fact_ratio.ratio * 100).toFixed(0)}%`
                    : "—"}
                </div>
                <div>
                  Cases with cost: {opsSummary.cost_per_case?.length ?? 0} · OCR samples:{" "}
                  {opsSummary.ocr_confidence?.count ?? 0}
                </div>
              </div>
            ) : (
              <p className="status">Ops summary unavailable.</p>
            )}
          </div>

          <div className="panel">
            <div className="row">
              <h3 style={{ margin: 0, flex: 1 }}>Diligence report</h3>
              {selectedCase && (
                <>
                  <button
                    type="button"
                    className="secondary"
                    disabled={reportBusy}
                    onClick={() => runAnalyze().catch((e) => setError(e.message))}
                  >
                    Analyze + report
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={reportBusy}
                    onClick={() =>
                      loadReport(selectedCase, { regenerate: true }).catch((e) =>
                        setError(e.message)
                      )
                    }
                  >
                    Regenerate
                  </button>
                  <a
                    className="secondary"
                    style={{ textDecoration: "none", padding: "0.35rem 0.7rem" }}
                    href={`/v1/cases/${selectedCase}/report?format=pdf`}
                    onClick={(e) => {
                      e.preventDefault();
                      fetch(`/v1/cases/${selectedCase}/report?format=pdf`, {
                        headers: { "X-API-Key": apiKey },
                      })
                        .then(async (r) => {
                          if (!r.ok) throw new Error(await r.text());
                          return r.blob();
                        })
                        .then((blob) => {
                          const url = URL.createObjectURL(blob);
                          const a = document.createElement("a");
                          a.href = url;
                          a.download = `${report?.report_id || "pie-report"}.pdf`;
                          a.click();
                          URL.revokeObjectURL(url);
                        })
                        .catch((err) => setError(err.message));
                    }}
                  >
                    PDF
                  </a>
                </>
              )}
            </div>
            {!selectedCase && <p className="status">Select a case to generate a report.</p>}
            {selectedCase && !report && (
              <p className="status">
                No report yet. Run Analyze + report after documents are processed.
              </p>
            )}
            {report && (
              <>
                <div className="score-strip">
                  <span className="pill">{report.status}</span>
                  <span className="pill">v{report.version}</span>
                  <span className="status">{report.report_id}</span>
                </div>
                <p className="status">
                  Open conflicts: {report.body?.summary?.open_conflicts ?? "—"} · Missing:{" "}
                  {report.body?.summary?.missing_evidence ?? "—"} · Risk:{" "}
                  {report.body?.summary?.risk_level ?? "—"}
                </p>
                {!!report.reused_sections?.length && (
                  <p className="status">
                    Reused sections after regen: {report.reused_sections.join(", ")}
                  </p>
                )}
                {report.rebuild_reason && (
                  <p className="status">Rebuild reason: {report.rebuild_reason}</p>
                )}
                <ul className="list">
                  {(report.body?.section_order || Object.keys(report.body?.sections || {})).map(
                    (key) => {
                      const sec = report.body?.sections?.[key];
                      if (!sec) return null;
                      return (
                        <li key={key}>
                          <div className="fact-value">{sec.heading || key}</div>
                          <div className="status">{sec.conclusion || ""}</div>
                        </li>
                      );
                    }
                  )}
                </ul>
              </>
            )}
          </div>

          <div className="panel">
            <div className="row">
              <h3 style={{ margin: 0, flex: 1 }}>Review queue</h3>
              {selectedCase && (
                <button
                  type="button"
                  className="secondary"
                  onClick={() =>
                    loadReviewTasks(selectedCase)
                      .then(() => loadAudit(selectedCase))
                      .catch((e) => setError(e.message))
                  }
                >
                  Refresh
                </button>
              )}
            </div>
            {!selectedCase && <p className="status">Select a case to review.</p>}
            {selectedCase && (
              <>
                <div className="row review-filters">
                  <label>
                    Status
                    <select
                      value={reviewStatus}
                      onChange={(e) => {
                        setReviewStatus(e.target.value);
                        loadReviewTasks(selectedCase, { status: e.target.value }).catch((err) =>
                          setError(err.message)
                        );
                      }}
                    >
                      <option value="">All</option>
                      <option value="OPEN">OPEN</option>
                      <option value="RESOLVED">RESOLVED</option>
                      <option value="CANCELLED">CANCELLED</option>
                    </select>
                  </label>
                  <label>
                    Severity
                    <select
                      value={reviewSeverity}
                      onChange={(e) => {
                        setReviewSeverity(e.target.value);
                        loadReviewTasks(selectedCase, { severity: e.target.value }).catch((err) =>
                          setError(err.message)
                        );
                      }}
                    >
                      <option value="">All</option>
                      <option value="CRITICAL">CRITICAL</option>
                      <option value="HIGH">HIGH</option>
                      <option value="MEDIUM">MEDIUM</option>
                      <option value="LOW">LOW</option>
                    </select>
                  </label>
                  <label>
                    Type
                    <select
                      value={reviewType}
                      onChange={(e) => {
                        setReviewType(e.target.value);
                        loadReviewTasks(selectedCase, { taskType: e.target.value }).catch((err) =>
                          setError(err.message)
                        );
                      }}
                    >
                      <option value="">All</option>
                      <option value="OWNER_IDENTITY_AMBIGUITY">Owner ambiguity</option>
                      <option value="CRITICAL_ID_CONFLICT">Critical ID</option>
                      <option value="CRITICAL_ID_OCR_CONFLICT">OCR ID</option>
                      <option value="SHARE_MATH_INCONSISTENCY">Share math</option>
                      <option value="ENCUMBRANCE_UNRESOLVED">Encumbrance</option>
                      <option value="USER_FLAG">User flag</option>
                      <option value="REQUIRES_REVIEW">Requires review</option>
                    </select>
                  </label>
                </div>
                {!reviewTasks.length && (
                  <p className="status">No review tasks for these filters.</p>
                )}
                <ul className="list">
                  {reviewTasks.map((t) => (
                    <li key={t.task_id}>
                      <div className="fact-row">
                        <div>
                          <span
                            className={`pill ${
                              t.severity === "CRITICAL" || t.severity === "HIGH"
                                ? "danger"
                                : "warn"
                            }`}
                          >
                            {t.severity}
                          </span>{" "}
                          <span className="pill">{t.task_type}</span>{" "}
                          <span className="pill">{t.status}</span>
                          <div className="fact-value">{t.title}</div>
                          <div className="status">{t.summary}</div>
                          {!!t.related_fact_ids?.length && (
                            <div className="status">
                              Facts: {t.related_fact_ids.join(", ")}
                            </div>
                          )}
                        </div>
                        {t.status === "OPEN" && (
                          <div className="fact-actions">
                            <button
                              type="button"
                              className="secondary"
                              disabled={!!decideBusy}
                              onClick={() =>
                                decideReview(t, "approve").catch((e) => setError(e.message))
                              }
                            >
                              Approve
                            </button>
                            <button
                              type="button"
                              className="secondary"
                              disabled={!!decideBusy}
                              onClick={() =>
                                decideReview(t, "reject").catch((e) => setError(e.message))
                              }
                            >
                              Reject
                            </button>
                            <button
                              type="button"
                              className="secondary"
                              disabled={!!decideBusy}
                              onClick={() =>
                                decideReview(t, "merge").catch((e) => setError(e.message))
                              }
                            >
                              Merge
                            </button>
                            <button
                              type="button"
                              className="secondary"
                              disabled={!!decideBusy}
                              onClick={() =>
                                decideReview(t, "request_docs").catch((e) =>
                                  setError(e.message)
                                )
                              }
                            >
                              Request docs
                            </button>
                            <button
                              type="button"
                              className="secondary"
                              disabled={!!decideBusy}
                              onClick={() =>
                                decideReview(t, "annotate").catch((e) => setError(e.message))
                              }
                            >
                              Annotate
                            </button>
                          </div>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
                {!!auditEvents.length && (
                  <div className="intel-block">
                    <h4>Audit trail</h4>
                    <ul className="list audit-list">
                      {auditEvents.slice(0, 12).map((ev) => (
                        <li key={ev.id}>
                          <span className="pill">{ev.action}</span>{" "}
                          <span className="status">
                            {ev.resource_type}
                            {ev.resource_id ? `:${ev.resource_id}` : ""} · {ev.actor}
                          </span>
                          {ev.details?.reason || ev.details?.note ? (
                            <div className="status">
                              {ev.details.reason || ev.details.note}
                            </div>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            )}
          </div>

          <div className="panel">
            <div className="row">
              <h3 style={{ margin: 0, flex: 1 }}>Case chat</h3>
              {conversationId && (
                <span className="pill">{conversationId.slice(0, 8)}…</span>
              )}
              {selectedCase && !!chatMessages.some((m) => m.role === "assistant") && (
                <button
                  type="button"
                  className="secondary"
                  onClick={() => flagLastAnswer().catch((e) => setError(e.message))}
                >
                  Flag answer
                </button>
              )}
            </div>
            {!selectedCase && <p className="status">Select a case to chat.</p>}
            {selectedCase && (
              <>
                <div className="chat-log">
                  {!chatMessages.length && (
                    <p className="status">
                      Try: “Who is the current owner?” · “What about survey 183/2?” · “What
                      should I upload next?”
                    </p>
                  )}
                  {chatMessages.map((m, i) => (
                    <div key={i} className={`chat-bubble ${m.role}`}>
                      <div className="chat-role">{m.role}</div>
                      <div className="chat-body">{m.content}</div>
                      {m.status && (
                        <div className="status" style={{ marginTop: "0.35rem" }}>
                          <span className="pill">{m.status}</span>{" "}
                          {(m.paths || []).map((p) => (
                            <span key={p} className="pill">
                              {p}
                            </span>
                          ))}
                        </div>
                      )}
                      {!!m.evidence?.length && (
                        <div className="chat-cites">
                          {m.evidence.slice(0, 4).map((ev, j) => (
                            <button
                              key={j}
                              type="button"
                              className="secondary"
                              onClick={() => {
                                if (ev.document_id) setSelectedDoc(ev.document_id);
                                if (ev.page) setPageNum(ev.page);
                              }}
                            >
                              {ev.evidence_id || ev.fact_id || "cite"}
                              {ev.page ? ` · p.${ev.page}` : ""}
                            </button>
                          ))}
                        </div>
                      )}
                      {!!m.conflicts?.length && (
                        <div className="status" style={{ marginTop: "0.35rem" }}>
                          {m.conflicts.length} open conflict(s) surfaced
                        </div>
                      )}
                    </div>
                  ))}
                </div>
                <div className="row" style={{ marginTop: "0.65rem" }}>
                  <label style={{ flex: 1 }}>
                    Ask the case
                    <input
                      value={chatInput}
                      onChange={(e) => setChatInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !chatBusy) {
                          sendChat().catch((err) => setError(err.message));
                        }
                      }}
                      disabled={chatBusy}
                    />
                  </label>
                  <button
                    type="button"
                    disabled={chatBusy}
                    onClick={() => sendChat().catch((e) => setError(e.message))}
                  >
                    {chatBusy ? "…" : "Send"}
                  </button>
                </div>
              </>
            )}
          </div>

          <div className="panel">
            <div className="row">
              <h3 style={{ margin: 0, flex: 1 }}>Extracted facts</h3>
              {whyFact && (
                <button type="button" className="secondary" onClick={() => setWhyFact(null)}>
                  Clear why
                </button>
              )}
            </div>
            {!facts.length && (
              <p className="status">No facts yet. Upload a sale deed and wait for the job.</p>
            )}
            <ul className="list facts">
              {facts.map((f) => (
                <li
                  key={f.fact_id}
                  className={whyFact?.fact_id === f.fact_id ? "active" : ""}
                >
                  <div className="fact-row">
                    <div>
                      <span className="pill">{f.fact_type}</span>{" "}
                      <span className="pill">{f.verification_state}</span>
                      <div className="fact-value">
                        <strong>{f.predicate}</strong>: {f.value_text || "—"}
                        {f.value_normalized && f.value_normalized !== f.value_text ? (
                          <span className="status"> → {f.value_normalized}</span>
                        ) : null}
                      </div>
                      <div className="status">
                        conf {(f.confidence ?? 0).toFixed(2)} · {f.evidence_count} evidence
                      </div>
                    </div>
                    <div className="fact-actions">
                      <button
                        type="button"
                        className="secondary"
                        onClick={() => openWhy(f).catch((e) => setError(e.message))}
                      >
                        Why?
                      </button>
                      <button
                        type="button"
                        className="secondary"
                        onClick={() => confirmFact(f).catch((e) => setError(e.message))}
                      >
                        Confirm
                      </button>
                      <button
                        type="button"
                        className="secondary"
                        onClick={() => rejectFact(f).catch((e) => setError(e.message))}
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
            {whyFact && (
              <div className="why-box">
                <strong>Why: {whyFact.fact_id}</strong>
                <p className="status">
                  {whyFact.verification_state === "NOT_FOUND"
                    ? "Not found in supplied documents (no invented value)."
                    : `Cited ${whyFact.evidence?.length || 0} evidence item(s). Page viewer highlights the citation.`}
                </p>
                {(whyFact.evidence || []).map((ev) => (
                  <div key={ev.evidence_id} className="why-ev">
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => setPageNum(ev.page_number)}
                    >
                      Page {ev.page_number}
                    </button>{" "}
                    <span className="pill">{ev.evidence_id}</span>
                    <div>{ev.text.slice(0, 280)}{ev.text.length > 280 ? "…" : ""}</div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="panel">
            <div className="row">
              <h3 style={{ margin: 0, flex: 1 }}>Page viewer</h3>
              <select
                value={pageNum}
                onChange={(e) => setPageNum(Number(e.target.value))}
                disabled={!pages.length}
              >
                {pages.map((p) => (
                  <option key={p.page_number} value={p.page_number}>
                    Page {p.page_number} ({p.quality_label || "n/a"})
                  </option>
                ))}
              </select>
            </div>
            <div className="viewer">
              {imageUrl ? (
                <>
                  <img src={imageUrl} alt={`Page ${pageNum}`} />
                  {pageEvidence.map((ev) => {
                    const bbox = ev.bbox;
                    if (!bbox || bbox.length < 4 || !currentPage?.width) return null;
                    const [x0, y0, x1, y1] = bbox;
                    const style = {
                      left: `${(x0 / currentPage.width) * 100}%`,
                      top: `${(y0 / currentPage.height) * 100}%`,
                      width: `${((x1 - x0) / currentPage.width) * 100}%`,
                      height: `${((y1 - y0) / currentPage.height) * 100}%`,
                    };
                    return <div key={ev.evidence_id} className="bbox" style={style} />;
                  })}
                </>
              ) : (
                <p className="status" style={{ padding: "1rem" }}>
                  No page image yet. Upload a PDF and wait for the job to succeed.
                </p>
              )}
            </div>
            <div className="evidence">
              {pageEvidence.length === 0 && (
                <span>No evidence for this page yet.</span>
              )}
              {pageEvidence.map((ev) => (
                <div key={ev.evidence_id} style={{ marginBottom: "0.75rem" }}>
                  <div>
                    <span className="pill">{ev.evidence_id}</span>{" "}
                    <span className="pill">{ev.source_type}</span>{" "}
                    <span className="pill">{ev.ocr_provider}</span>
                  </div>
                  {ev.text}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {status && <p className="status ok">{status}</p>}
      {error && <p className="status err">{error}</p>}
    </div>
  );
}
