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

  const saveKey = () => {
    localStorage.setItem("pie_api_key", apiKey);
    setStatus("API key saved");
  };

  const refreshCases = useCallback(async () => {
    setError("");
    const data = await api("/v1/cases", { apiKey });
    setCases(data);
  }, [apiKey]);

  useEffect(() => {
    refreshCases().catch((e) => setError(String(e.message || e)));
  }, [refreshCases]);

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

  const loadCase = useCallback(
    async (caseId) => {
      setError("");
      const detail = await api(`/v1/cases/${caseId}`, { apiKey });
      setCaseDetail(detail);
      setSelectedCase(caseId);
      setWhyFact(null);
      await loadIntelligence(caseId);
      if (detail.documents?.length) {
        setSelectedDoc(detail.documents[0].id);
      } else {
        setSelectedDoc(null);
        setPages([]);
        setEvidence([]);
        setFacts([]);
        setIntelligence(null);
      }
    },
    [apiKey, loadIntelligence]
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
        <span>Property Intelligence Engine — reconcile · conflicts · graphs</span>
      </div>
      <p className="lede">
        Upload title documents to a case. PIE extracts facts with evidence, reconciles across
        documents, and surfaces conflicts, missing instruments, and a completeness scorecard.
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
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => openWhy(f).catch((e) => setError(e.message))}
                    >
                      Why this fact?
                    </button>
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
