import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

type Row = Record<string, any>;
const SEV = ["critical", "high", "medium", "low", "info"];

async function api(path: string, options: any = {}) {
  const fromUrl = new URLSearchParams(location.search).get("token");
  if (fromUrl) localStorage.setItem("hackbots_token", fromUrl);
  const saved = localStorage.getItem("hackbots_token");
  const joiner = path.includes("?") ? "&" : "?";
  const full = saved ? `${path}${joiner}token=${encodeURIComponent(saved)}` : path;
  const res = await fetch(full, {
    ...options,
    headers: { "content-type": "application/json", ...(options.headers ?? {}) },
  });
  return res.json();
}

function App() {
  const [data, setData] = useState<any>({ targets: [], findings: [], runs: [], settings: {} });
  const [tab, setTab] = useState("dashboard");
  const [selected, setSelected] = useState<Row | null>(null);
  const [filters, setFilters] = useState({ search: "", severity: "", status: "", kind: "", target_id: "" });
  const [form, setForm] = useState<Row>({ name: "", platform: "web", base_url: "", in_scope: "", out_of_scope: "", auth_notes: "", notes: "" });
  const [roomsData, setRoomsData] = useState<Row>({ rooms: [], agents: [] });
  const [agentView, setAgentView] = useState("hackbot-lead");
  const [roomId, setRoomId] = useState("");
  const [messages, setMessages] = useState<Row[]>([]);

  const refresh = async () => setData(await api("/api/bootstrap"));
  const refreshRooms = async () => setRoomsData(await api("/api/rooms"));
  const openRoom = async (id: string, agent = agentView) => {
    setRoomId(id);
    const r = await api(`/api/rooms/messages?room_id=${encodeURIComponent(id)}&agent_id=${encodeURIComponent(agent)}`);
    setMessages(r.messages ?? []);
  };
  useEffect(() => {
    refresh();
    refreshRooms().then(() => {
      const params = new URLSearchParams(location.search);
      const view = params.get("view");
      const agent = params.get("agent");
      const room = params.get("room");
      if (view) setTab(view);
      if (agent && ["hackbot-lead", "recon-agent", "hunt-agent", "validate-agent", "report-agent"].includes(agent)) {
        setAgentView(agent);
      }
      if (room && view === "conversations") openRoom(room, agent ?? agentView);
    });
  }, []);

  const counts = Object.fromEntries(SEV.map(s => [s, data.findings.filter((f: Row) => f.severity === s).length]));

  const filtered = data.findings.filter((f: Row) =>
    (!filters.search || (f.title + f.detail).toLowerCase().includes(filters.search.toLowerCase())) &&
    (!filters.severity || f.severity === filters.severity) &&
    (!filters.status || f.status === filters.status) &&
    (!filters.kind || f.kind === filters.kind) &&
    (!filters.target_id || String(f.target_id) === filters.target_id));

  const patchFinding = async (id: number, patch: Row) => {
    await api(`/api/findings/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
    await refresh();
  };

  return (
    <>
      <header>
        <h1>⚡ HACKBOTS HARNESS</h1>
        {["dashboard", "targets", "findings", "conversations", "settings"].map(t => (
          <button key={t} className={tab === t ? "" : "secondary"} onClick={() => setTab(t)}>{t}</button>
        ))}
        <button className="secondary" onClick={refresh}>↻ refresh</button>
        <span className="muted">listening on 127.0.0.1:13337</span>
      </header>
      <main>
        {tab === "dashboard" && <>
          <div className="cards">
            {SEV.map(s => <div className="card" key={s}><span className={`sev-${s}`}>{s}</span><b className={`sev-${s}`}>{counts[s]}</b></div>)}
            <div className="card"><span>targets</span><b>{data.targets.length}</b></div>
            <div className="card"><span>active runs</span><b>{data.runs.filter((r: Row) => r.status !== "done").length}</b></div>
          </div>
          <h3>Recent findings</h3>
          <table><thead><tr><th>ID</th><th>Severity</th><th>Title</th><th>Kind</th><th>Status</th><th>Source</th></tr></thead>
            <tbody>{data.findings.slice(0, 20).map((f: Row) => <tr key={f.id} onClick={() => setSelected(f)} style={{cursor:"pointer"}}>
              <td>{f.id}</td><td className={`sev-${f.severity}`}>{f.severity}</td><td>{f.title}</td>
              <td>{f.kind}</td><td>{f.status}</td><td className="muted">{f.source}</td>
            </tr>)}</tbody></table>
          <h3>Active runs</h3>
          <table><thead><tr><th>Run</th><th>Target</th><th>Model</th><th>Budget</th><th>Status</th></tr></thead>
            <tbody>{data.runs.map((r: Row) => <tr key={r.id}><td>{r.id}</td><td>{r.target}</td><td>{r.model}</td><td>{r.budget}m</td><td>{r.status}</td></tr>)}</tbody></table>
        </>}

        {tab === "targets" && <>
          <h3>Add target</h3>
          <div className="grid">
            {["name","platform","base_url","in_scope","out_of_scope","auth_notes","notes"].map(k =>
              <div key={k}><label>{k}</label><input style={{width:"100%"}} value={form[k] ?? ""} onChange={e => setForm({...form,[k]:e.target.value})} /></div>)}
          </div>
          <button onClick={async () => { await api("/api/targets",{method:"POST",body:JSON.stringify(form)}); setForm({name:"",platform:"web",base_url:"",in_scope:"",out_of_scope:"",auth_notes:"",notes:""}); refresh(); }}>＋ Add target</button>
          <h3>Targets</h3>
          {data.targets.map((t: Row) => <div className="card" key={t.id} style={{marginBottom:10}}>
            <b>{t.name}</b> <span className="tag">{t.platform}</span> <span className="tag">{t.base_url}</span>
            <div className="muted">in: {t.in_scope || "—"} · out: {t.out_of_scope || "—"}</div>
            <div className="muted">auth: {t.auth_notes || "—"}</div>
            <div>{t.notes}</div>
            <button className="danger" style={{marginTop:8}} onClick={async () => { if(confirm(`Delete ${t.name} and its findings?`)){ await api(`/api/targets/${t.id}`,{method:"DELETE"}); refresh(); } }}>delete</button>
          </div>)}
        </>}

        {tab === "findings" && <>
          <div className="row">
            {Object.keys(filters).map(k => <input key={k} placeholder={k} value={(filters as any)[k]} onChange={e => setFilters({...filters,[k]:e.target.value})} />)}
          </div>
          <table><thead><tr><th>ID</th><th>Severity</th><th>Title</th><th>Kind</th><th>Target</th><th>Status</th></tr></thead>
            <tbody>{filtered.map((f: Row) => <tr key={f.id} onClick={() => setSelected(f)} style={{cursor:"pointer"}}>
              <td>{f.id}</td><td className={`sev-${f.severity}`}>{f.severity}</td><td>{f.title}</td><td>{f.kind}</td><td>{f.target_id ?? "—"}</td><td>{f.status}</td>
            </tr>)}</tbody></table>
        </>}

        {tab === "conversations" && <>
          <h3>Canonical shared domain conversations</h3>
          <p className="muted">One room per target. All five agent interfaces are views of the same room and message history.</p>
          <div className="row">
            <b>Agent workspace view:</b>
            {(roomsData.agents ?? []).map((a: string) => (
              <button key={a} className={agentView === a ? "" : "secondary"}
                onClick={() => { setAgentView(a); if (roomId) openRoom(roomId, a); }}>{a}</button>
            ))}
          </div>
          <table><thead><tr><th>Domain</th><th>Room ID</th><th>Views</th><th>Messages</th><th>Activity</th></tr></thead>
            <tbody>{(roomsData.rooms ?? []).map((r: Row) => <tr key={r.room_id} onClick={() => openRoom(r.room_id)} style={{cursor:"pointer"}}>
              <td><b>{r.canonical_target}</b></td><td className="muted">{r.room_id}</td>
              <td>{(r.agent_views ?? []).length}/5</td><td>{r.message_count}</td><td className="muted">{r.last_activity_at}</td>
            </tr>)}</tbody></table>
          {roomId && <>
            <h3>{agentView} — {roomId}</h3>
            <p className="muted">Same room ID, same message IDs, same order from every agent view.</p>
            <table><thead><tr><th>Message ID</th><th>Sender</th><th>Type</th><th>Time</th><th>Content</th></tr></thead>
              <tbody>{messages.map(m => <tr key={m.message_id}>
                <td className="muted">{m.message_id}</td><td>{m.sender_agent_id}</td>
                <td>{m.message_type}</td><td className="muted">{m.timestamp}</td>
                <td><pre>{m.message_content}</pre></td>
              </tr>)}</tbody></table>
          </>}
          <button className="secondary" onClick={refreshRooms}>↻ refresh rooms</button>
        </>}

        {tab === "settings" && <Settings settings={data.settings} refresh={refresh} />}
      </main>

      {selected && <div className="drawer">
        <div className="row"><h2>Finding #{selected.id}</h2><button className="secondary" onClick={() => setSelected(null)}>close</button></div>
        <label>Severity</label>
        <select value={selected.severity} onChange={e => { const v = e.target.value; setSelected({...selected,severity:v}); patchFinding(selected.id,{severity:v}); }}>
          {SEV.map(s => <option key={s}>{s}</option>)}
        </select>
        <label>Status</label>
        <select value={selected.status} onChange={e => { const v = e.target.value; setSelected({...selected,status:v}); patchFinding(selected.id,{status:v}); }}>
          {["open","triaged","resolved","false-positive"].map(s => <option key={s}>{s}</option>)}
        </select>
        <label>Title</label><input style={{width:"100%"}} value={selected.title} onChange={e => setSelected({...selected,title:e.target.value})} />
        <label>Detail</label><textarea style={{width:"100%",minHeight:140}} value={selected.detail} onChange={e => setSelected({...selected,detail:e.target.value})} />
        <button className="secondary" onClick={() => patchFinding(selected.id,{title:selected.title,detail:selected.detail})}>save</button>
        <button style={{marginLeft:8}} onClick={async () => { const r = await api(`/api/findings/${selected.id}/resolve`,{method:"POST"}); alert(r.webhookSent ? "Resolved + Discord notified" : "Resolved (no webhook configured / send failed)"); setSelected(null); refresh(); }}>✓ Resolve to Discord</button>
        <h3>Source</h3><pre>{selected.source || "—"}</pre>
      </div>}
    </>
  );
}

function Settings({ settings, refresh }: { settings: Row; refresh: () => void }) {
  const [form, setForm] = useState<Row>(settings);
  useEffect(() => setForm(settings), [settings]);
  const save = async () => { await api("/api/settings", { method: "PATCH", body: JSON.stringify(form) }); refresh(); };
  const curl = `curl -X POST http://127.0.0.1:13337/api/ingest \\\n  -H 'Authorization: Bearer ${settings.ingest_token ?? ""}' \\\n  -H 'Content-Type: application/json' \\\n  -d '{"title":"Sample finding","severity":"high","kind":"endpoint","detail":"..."}'`;
  return <>
    <h3>Discord / ingest settings</h3>
    <div className="grid">
      <div><label>webhook_url</label><input style={{width:"100%"}} value={form.webhook_url ?? ""} onChange={e=>setForm({...form,webhook_url:e.target.value})} /></div>
      <div><label>severity_threshold</label><select value={form.severity_threshold ?? "high"} onChange={e=>setForm({...form,severity_threshold:e.target.value})}>{SEV.map(s=><option key={s}>{s}</option>)}</select></div>
      <div><label>ingest_token</label><input style={{width:"100%"}} value={form.ingest_token ?? ""} onChange={e=>setForm({...form,ingest_token:e.target.value})} /></div>
      <div><label>eval_endpoint</label><input style={{width:"100%"}} value={form.eval_endpoint ?? ""} onChange={e=>setForm({...form,eval_endpoint:e.target.value})} /></div>
    </div>
    <button onClick={save}>save settings</button>
    <h3>Ingest example (copy-paste)</h3><pre>{curl}</pre>
    <p className="muted">Findings at or above the threshold auto-fire the webhook on arrival.</p>
  </>;
}

createRoot(document.getElementById("root")!).render(<App />);
