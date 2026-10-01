import { useCallback, useEffect, useRef, useState } from "react";

const API = "";

const STAGE_LABELS = {
  integrity: "Checking file",
  page_split: "Reading pages",
  page_quality: "Checking quality",
  classify: "Classifying document",
  ocr: "Extracting text",
  evidence_persist: "Saving evidence",
  structured_extract: "Extracting facts",
  index_embeddings: "Indexing",
  queued: "Queued",
};

const SUMMARY_PROMPT =
  "Summarize this title diligence case for a lawyer. Cover: document type, parties (buyer/seller/owner), property/parcels, key dates and consideration if present, open conflicts or risks, and what still needs human review. Be concrete and cite evidence where you can.";

function authHeaders(apiKey) {
  return { "X-API-Key": apiKey };
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

function stageLabel(name) {
  return STAGE_LABELS[name] || name || "Working";
}

export default function App() {
  const [apiKey, setApiKey] = useState(
    () => localStorage.getItem("pie_api_key") || "pie_dev_key_change_me"
  );
  const [cases, setCases] = useState([]);
  const [selectedCase, setSelectedCase] = useState(null);
  const [caseDetail, setCaseDetail] = useState(null);
  const [title, setTitle] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("idle"); // idle | uploading | processing | summarizing | ready
  const [progress, setProgress] = useState("");
  const [intelligence, setIntelligence] = useState(null);
  const [facts, setFacts] = useState([]);
  const [reviewTasks, setReviewTasks] = useState([]);
  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [chatBusy, setChatBusy] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const fileRef = useRef(null);
  const chatEndRef = useRef(null);

  const saveKey = () => {
    localStorage.setItem("pie_api_key", apiKey);
    setStatus("API key saved");
  };

  const refreshCases = useCallback(async () => {
    const data = await api("/v1/cases", { apiKey });
    setCases(data);
    return data;
  }, [apiKey]);

  useEffect(() => {
    refreshCases().catch((e) => setError(String(e.message || e)));
  }, [refreshCases]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages, chatBusy]);

  const loadCase = useCallback(
    async (caseId, { resetChat = false } = {}) => {
      const detail = await api(`/v1/cases/${caseId}`, { apiKey });
      setCaseDetail(detail);
      setSelectedCase(caseId);
      if (resetChat) {
        setChatMessages([]);
        setConversationId(null);
      }
      setPhase((prev) =>
        prev === "uploading" || prev === "processing" || prev === "summarizing"
          ? prev
          : detail.documents?.length
            ? "ready"
            : "idle"
      );

      let intel = null;
      try {
        intel = await api(`/v1/cases/${caseId}/intelligence`, { apiKey });
        setIntelligence(intel);
      } catch {
        setIntelligence(null);
      }

      try {
        const f = await api(`/v1/cases/${caseId}/facts`, { apiKey });
        setFacts(f);
      } catch {
        setFacts([]);
      }

      try {
        const tasks = await api(`/v1/cases/${caseId}/review-tasks?status=OPEN`, { apiKey });
        setReviewTasks(tasks);
      } catch {
        setReviewTasks([]);
      }

      return { detail, intel };
    },
    [apiKey]
  );

  useEffect(() => {
    if (!selectedCase) return;
    // Don't wipe chat when upload pipeline refreshes the same case
    loadCase(selectedCase).catch((e) => setError(String(e.message || e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when case id changes
  }, [selectedCase]);

  const ensureCase = async () => {
    if (selectedCase) return selectedCase;
    const name = title.trim() || `Diligence ${new Date().toLocaleDateString()}`;
    const c = await api("/v1/cases", {
      apiKey,
      method: "POST",
      body: { title: name, description: "Created from PIE web UI" },
    });
    await refreshCases();
    setSelectedCase(c.id);
    setTitle("");
    return c.id;
  };

  const askSummary = async (caseId) => {
    setPhase("summarizing");
    setProgress("Writing analysis…");
    setChatBusy(true);
    try {
      // Reconcile may already have finished via the worker — don't fail the UX on races.
      try {
        await api(`/v1/cases/${caseId}/analyze`, {
          apiKey,
          method: "POST",
          body: { mode: "incremental", sync: true, generate_report: true },
        });
      } catch (e) {
        console.warn("analyze skipped/failed; continuing with chat summary", e);
      }

      const res = await api(`/v1/cases/${caseId}/chat`, {
        apiKey,
        method: "POST",
        body: { message: SUMMARY_PROMPT },
      });
      setConversationId(res.conversation_id);
      setChatMessages([
        {
          role: "assistant",
          content: res.answer,
          status: res.status,
          evidence: res.evidence || [],
          conflicts: res.conflicts || [],
          missing_evidence: res.missing_evidence || [],
          message_id: res.message_id,
          isSummary: true,
        },
      ]);
      setPhase("ready");
      setProgress("");
      setStatus("Analysis ready — ask a follow-up below");
    } finally {
      setChatBusy(false);
    }
  };

  const pollJob = async (caseId, jobId) => {
    setPhase("processing");
    for (let i = 0; i < 300; i++) {
      const j = await api(`/v1/jobs/${jobId}`, { apiKey });
      const running =
        (j.stages || []).find((s) => s.status === "running")?.stage_name ||
        (j.stages || []).filter((s) => s.status === "succeeded").at(-1)?.stage_name ||
        "queued";
      setProgress(stageLabel(running));

      if (j.status === "succeeded" || j.status === "failed" || j.status === "partial") {
        if (j.status === "failed") {
          setPhase("idle");
          setProgress("");
          throw new Error(j.error_message || "Processing failed");
        }

        // Wait for reconcile / intelligence
        setProgress("Building case intelligence…");
        for (let k = 0; k < 45; k++) {
          await new Promise((r) => setTimeout(r, 1000));
          try {
            const intel = await api(`/v1/cases/${caseId}/intelligence`, { apiKey });
            setIntelligence(intel);
            if (intel?.scorecard || intel?.risk) break;
          } catch {
            /* keep waiting */
          }
        }

        await loadCase(caseId);
        await askSummary(caseId);
        return;
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    setPhase("idle");
    throw new Error("Processing timed out — check the worker");
  };

  const uploadFile = async (file) => {
    if (!file) return;
    setError("");
    setBusy(true);
    setPhase("uploading");
    setProgress("Uploading…");
    setChatMessages([]);
    setConversationId(null);
    try {
      const caseId = await ensureCase();
      const fd = new FormData();
      fd.append("file", file);
      fd.append("role", "primary");
      const result = await api(`/v1/cases/${caseId}/documents/complete`, {
        apiKey,
        method: "POST",
        formData: fd,
      });
      setStatus(
        result.deduped
          ? `Linked existing document · analyzing for this case`
          : `Uploaded ${file.name}`
      );
      await pollJob(caseId, result.job_id);
    } catch (e) {
      setError(e.message || String(e));
      setPhase("idle");
      setProgress("");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const sendChat = async (text) => {
    const userMsg = (text ?? chatInput).trim();
    if (!selectedCase || !userMsg || chatBusy) return;
    setError("");
    setChatBusy(true);
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
          message_id: res.message_id,
        },
      ]);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setChatBusy(false);
    }
  };

  const risk = intelligence?.risk;
  const docs = caseDetail?.documents || [];
  const keyFacts = facts
    .filter((f) =>
      ["party.owner", "party.buyer", "party.seller", "property.address", "transaction.date", "transaction.consideration"].includes(
        f.fact_type
      )
    )
    .slice(0, 8);

  return (
    <div className="app shell">
      <header className="topbar">
        <div className="brand">
          <h1>PIE</h1>
          <span>Title diligence</span>
        </div>
        <button
          type="button"
          className="secondary ghost"
          onClick={() => setShowSettings((s) => !s)}
        >
          Settings
        </button>
      </header>

      {showSettings && (
        <div className="panel settings">
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
              Save
            </button>
          </div>
        </div>
      )}

      <section className="hero-panel panel">
        <h2>Upload a title document</h2>
        <p className="lede tight">
          PIE extracts parties, parcels, and risks, then writes an analysis you can question.
        </p>

        <div className="row start-row">
          <label className="grow">
            Case
            <select
              value={selectedCase || ""}
              disabled={busy}
              onChange={(e) => {
                const id = e.target.value || null;
                setChatMessages([]);
                setConversationId(null);
                setSelectedCase(id);
                if (!id) {
                  setCaseDetail(null);
                  setIntelligence(null);
                  setFacts([]);
                  setReviewTasks([]);
                  setPhase("idle");
                }
              }}
            >
              <option value="">New case (auto-create on upload)</option>
              {cases.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </select>
          </label>
          {!selectedCase && (
            <label className="grow">
              Optional title
              <input
                type="text"
                placeholder="e.g. Survey No. 42 / Bandra"
                value={title}
                disabled={busy}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
          )}
        </div>

        <label className={`dropzone ${busy ? "disabled" : ""}`}>
          <input
            ref={fileRef}
            type="file"
            accept="application/pdf,image/*"
            disabled={busy}
            onChange={(e) => uploadFile(e.target.files?.[0])}
          />
          <strong>{busy ? progress || "Working…" : "Drop a PDF here or click to upload"}</strong>
          <span>Sale deed, mutation, title search, encumbrance certificate…</span>
        </label>

        {(phase === "uploading" || phase === "processing" || phase === "summarizing") && (
          <div className="progress-bar">
            <div className="progress-pulse" />
            <span>{progress || "Working…"}</span>
          </div>
        )}

        {docs.length > 0 && (
          <div className="doc-chips">
            {docs.map((d) => (
              <span key={d.id} className="pill">
                {d.source_filename || "Document"} · {d.upload_status}
              </span>
            ))}
          </div>
        )}
      </section>

      {error && <p className="status err banner">{error}</p>}
      {status && !error && <p className="status ok banner">{status}</p>}

      {(risk || reviewTasks.length > 0 || keyFacts.length > 0) && phase === "ready" && (
        <section className="panel snapshot">
          <div className="snapshot-head">
            <h3>At a glance</h3>
            {risk && (
              <span
                className={`pill ${
                  ["HIGH", "CRITICAL"].includes(risk.risk_level)
                    ? "danger"
                    : risk.risk_level === "MEDIUM"
                      ? "warn"
                      : ""
                }`}
              >
                Risk {risk.risk_level}
              </span>
            )}
            {reviewTasks.length > 0 && (
              <span className="pill warn">{reviewTasks.length} open review items</span>
            )}
            {intelligence?.open_conflicts_count > 0 && (
              <span className="pill danger">{intelligence.open_conflicts_count} conflicts</span>
            )}
          </div>
          {keyFacts.length > 0 && (
            <ul className="fact-chips">
              {keyFacts.map((f) => (
                <li key={f.fact_id}>
                  <span className="muted">{f.fact_type.replace(/^party\.|^property\.|^transaction\./, "")}</span>
                  <strong>{f.value_text || f.value_normalized || "—"}</strong>
                </li>
              ))}
            </ul>
          )}
          <button
            type="button"
            className="secondary ghost"
            onClick={() => setShowDetails((s) => !s)}
          >
            {showDetails ? "Hide details" : "Show risks & review items"}
          </button>
          {showDetails && (
            <div className="details">
              {!!risk?.drivers?.length && (
                <div>
                  <h4>Risk drivers</h4>
                  <ul className="list quiet">
                    {risk.drivers.map((d) => (
                      <li key={`${d.code}-${d.label}`}>
                        {d.label} <span className="pill warn">+{d.weight}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {!!reviewTasks.length && (
                <div>
                  <h4>Needs review</h4>
                  <ul className="list quiet">
                    {reviewTasks.slice(0, 6).map((t) => (
                      <li key={t.task_id}>
                        <span className={`pill ${t.severity === "HIGH" || t.severity === "CRITICAL" ? "danger" : "warn"}`}>
                          {t.severity}
                        </span>{" "}
                        {t.title || t.summary}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {!!intelligence?.legal_findings?.length && (
                <div>
                  <h4>Findings</h4>
                  <ul className="list quiet">
                    {intelligence.legal_findings.slice(0, 5).map((f) => (
                      <li key={f.finding_id}>{f.statement}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </section>
      )}

      <section className="panel chat-panel">
        <div className="chat-head">
          <h3>Analysis & questions</h3>
          {selectedCase && phase === "ready" && chatMessages.length === 0 && !chatBusy && (
            <button
              type="button"
              className="secondary"
              onClick={() => askSummary(selectedCase).catch((e) => setError(e.message))}
            >
              Generate analysis
            </button>
          )}
        </div>

        {!selectedCase && (
          <p className="status empty">Upload a document to get started.</p>
        )}

        {selectedCase && chatMessages.length === 0 && !chatBusy && phase !== "summarizing" && (
          <p className="status empty">
            {docs.length
              ? "No analysis yet — upload again or generate analysis."
              : "Waiting for a document."}
          </p>
        )}

        <div className="chat-log tall">
          {chatMessages.map((m, i) => (
            <div
              key={i}
              className={`chat-bubble ${m.role}${m.isSummary ? " summary" : ""}`}
            >
              <div className="chat-role">
                {m.isSummary ? "Analysis" : m.role === "user" ? "You" : "PIE"}
                {m.status ? ` · ${m.status}` : ""}
              </div>
              <div className="chat-body">{m.content}</div>
              {!!m.evidence?.length && (
                <div className="chat-cites">
                  {m.evidence.slice(0, 6).map((e, idx) => (
                    <span key={idx} className="pill">
                      p.{e.page ?? "?"} {e.snippet ? `· ${String(e.snippet).slice(0, 40)}…` : ""}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
          {chatBusy && (
            <div className="chat-bubble assistant">
              <div className="chat-role">PIE</div>
              <div className="chat-body muted">Thinking…</div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        <div className="chat-compose">
          <input
            type="text"
            placeholder={
              phase === "ready"
                ? "Ask a follow-up — e.g. Who is the seller?"
                : "Available after analysis"
            }
            value={chatInput}
            disabled={!selectedCase || chatBusy || phase !== "ready"}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") sendChat();
            }}
          />
          <button
            type="button"
            disabled={!selectedCase || chatBusy || phase !== "ready" || !chatInput.trim()}
            onClick={() => sendChat()}
          >
            Ask
          </button>
        </div>

        {phase === "ready" && (
          <div className="suggest">
            {[
              "Who is the current owner?",
              "What are the open risks?",
              "List all parcels mentioned",
            ].map((q) => (
              <button
                key={q}
                type="button"
                className="secondary chip"
                disabled={chatBusy}
                onClick={() => sendChat(q)}
              >
                {q}
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
