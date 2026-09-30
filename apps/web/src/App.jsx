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
  const [job, setJob] = useState(null);
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

  const loadCase = useCallback(
    async (caseId) => {
      setError("");
      const detail = await api(`/v1/cases/${caseId}`, { apiKey });
      setCaseDetail(detail);
      setSelectedCase(caseId);
      if (detail.documents?.length) {
        setSelectedDoc(detail.documents[0].id);
      } else {
        setSelectedDoc(null);
        setPages([]);
        setEvidence([]);
      }
    },
    [apiKey]
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
        if (selectedCase && selectedDoc) {
          await loadEvidence(selectedCase, selectedDoc);
          await loadPages(selectedCase, selectedDoc);
        } else if (selectedCase && j.document_id) {
          setSelectedDoc(j.document_id);
          await loadEvidence(selectedCase, j.document_id);
          await loadPages(selectedCase, j.document_id);
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

  useEffect(() => {
    if (!selectedCase || !selectedDoc) return;
    loadPages(selectedCase, selectedDoc).catch(() => {});
    loadEvidence(selectedCase, selectedDoc).catch(() => {});
  }, [selectedCase, selectedDoc, apiKey]);

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

  const pageEvidence = useMemo(
    () => evidence.filter((e) => e.page_number === pageNum),
    [evidence, pageNum]
  );

  const currentPage = pages.find((p) => p.page_number === pageNum);

  return (
    <div className="app">
      <div className="brand">
        <h1>PIE</h1>
        <span>Property Intelligence Engine — evidence viewer</span>
      </div>
      <p className="lede">
        Upload title documents to a case, watch the evidence spine build, and inspect
        page-level citations.
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
                      onClick={() => setSelectedDoc(d.id)}
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
