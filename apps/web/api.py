"""Minimal Query/Command API (T1.17, §13).

M1 slice:
  - GET  /api/v1/status     — node state, config head, active session, counts;
  - GET  /api/v1/timeline   — audit events for a session (sequence order);
  - POST /api/v1/messages   — inbox message (created/queued);
  - GET  /api/v1/questions  — the FIFO question queue view (T7.59);
  - POST /api/v1/questions  — operator question intake, admin-token guarded (T7.59);
  - POST /api/v1/commands   — closed operator commands with idempotency key;
  - GET  /api/v1/glossary   — human labels for every enum value the web shows (T7.64).

M1 command semantics: pause/resume (node state), wake_now (run one session
through the attached orchestrator), stop_gracefully / abort_session (set the
active session's request flags; the orchestrator enforces the boundary
between steps). set_budget / set_access_profile / restore_checkpoint are
rejected until their milestones (M2/M7).

Visibility: M1 is single-operator local; the timeline returns all events.
Auth and visibility filtering land with the full web in M7.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from apps.orchestrator.node_guard import NodeSessionGuard, NodeSessionGuardError
from apps.orchestrator.orchestrator import Orchestrator, SessionOutcome
from apps.orchestrator.scheduler import (
    REASON_SESSION_IN_PROGRESS,
    WakeScheduler,
    data_root_from_env,
    node_owner_from_env,
)
from apps.web import diagnostics as diagnostics_queries
from apps.web import knowledge as knowledge_queries
from apps.web import labels as ui_labels
from apps.web import metrics as metrics_queries
from apps.web.bind import resolve_standalone_workspace
from apps.web.host_status import HostStatus, HostStatusAdapter
from packages.domain.db.engine import DatabaseSettings
from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.config import ORMConfigSnapshot, ORMSystemConstant
from packages.domain.models.enums import (
    OperatorCommandState,
    OperatorCommandType,
    SessionState,
)
from packages.domain.models.events import ORMAuditEvent
from packages.domain.models.inbox import ORMMessage, ORMOperatorCommand
from packages.domain.models.questions import ORMQuestion
from packages.domain.models.sessions import ORMSession
from packages.domain.repositories.inbox import MessageRepository, OperatorCommandRepository
from packages.domain.repositories.sessions import SessionRepository
from packages.domain.services.config import ConfigError, ConfigService
from packages.domain.services.question_intake import (
    MAX_QUESTION_TEXT_CHARS,
    PRIORITY_MAX,
    PRIORITY_MIN,
    QuestionIntakeError,
    put_operator_question,
    question_queue,
    queue_position,
)

NODE_STATE_KEY = "node_state"
NODE_STATES = ("idle", "paused", "session_running")


def annotate_reasons(result: object) -> JsonDict:
    """Аддитивные подписи причины отказа команды (T7.64, ADR-0026).

    Чистая функция presentation-слоя: только уже переданные поля, ничего не
    решает и не пересчитывает. Известная строка отказа получает label/hint/action
    из единого словаря подписей; неизвестная остаётся как есть (label = сам
    текст) — выдумывать причину нельзя. Существующие ключи ответа не меняются.
    """
    out: JsonDict = dict(result) if isinstance(result, dict) else {}
    reason = out.get("reason")
    if isinstance(reason, str) and reason:
        entry = ui_labels.describe_refusal(reason)
        if not entry["hint"]:
            # отказ мог быть кодом допуска (scheduler REASON_*), а не фразой
            entry = ui_labels.describe_reason(reason)
        out["reason_label"] = entry["label"]
        out["reason_hint"] = entry["hint"]
        out["reason_action"] = entry["action"]
    for key, category in (("wake_reason", "wake_reason"), ("paused_reason", "paused_reason")):
        value = out.get(key)
        if isinstance(value, str) and value:
            entry = ui_labels.describe(category, value)
            out[f"{key}_label"] = entry["label"]
            out[f"{key}_hint"] = entry["hint"]
            out[f"{key}_action"] = entry["action"]
    return out


def annotate_host_status(host: object) -> JsonDict:
    """Подпись состояния восстановления в баннере узла (аддитивно)."""
    out: JsonDict = dict(host) if isinstance(host, dict) else {}
    state = out.get("recovery_state")
    if isinstance(state, str) and state:
        entry = ui_labels.describe("recovery_state", state)
        out["recovery_state_label"] = entry["label"]
        out["recovery_state_hint"] = entry["hint"]
        out["recovery_state_action"] = entry["action"]
    return out


def annotate_question(row: object) -> JsonDict:
    """Подписи состояния и происхождения вопроса (аддитивно, строка очереди)."""
    out: JsonDict = dict(row) if isinstance(row, dict) else {}
    for key, category in (("state", "question_state"), ("origin", "question_origin")):
        value = out.get(key)
        if isinstance(value, str) and value:
            entry = ui_labels.describe(category, value)
            out[f"{key}_label"] = entry["label"]
            out[f"{key}_hint"] = entry["hint"]
    session = out.get("session")
    if isinstance(session, dict):
        inner = dict(session)
        state = inner.get("state")
        if isinstance(state, str) and state:
            inner["state_label"] = ui_labels.describe("session_state", state)["label"]
        out["session"] = inner
    return out


# T3.20/T3.21: minimal HTML shell. The page is a thin viewer over the JSON
# API; all invariants live server-side, the HTML only renders them.
_MAIN_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOEZEMA — узел</title>
<style>
 body{font-family:system-ui,sans-serif;margin:2rem;background:#0e1116;color:#e6e6e6}
 h1{font-size:1.3rem} .card{background:#171c26;border:1px solid #2a3142;border-radius:8px;padding:1rem;margin:0.7rem 0}
 .ok{color:#7bd88f}.bad{color:#e06c75}.warn{color:#e5c07b}
 #banner{padding:.6rem 1rem;border-radius:6px;font-weight:600}
 .none{background:#1c2a22}.retry{background:#2a2a1c}.degraded{background:#2a2416}.blocked{background:#2a1616}
 table{border-collapse:collapse;width:100%;font-size:.92rem;margin-top:.5rem}
 th,td{border-bottom:1px solid #2a3142;padding:.3rem .45rem;text-align:left;vertical-align:top}
 td.wrap{overflow-wrap:anywhere;max-width:40rem}
 textarea, input[type=number], input[type=password]{background:#0b0e13;border:1px solid #2a3142;
   border-radius:5px;padding:.35rem;color:#e6e6e6}
 button{background:#2a3142;color:#e6e6e6;border:1px solid #3d4658;border-radius:5px;padding:.35rem .7rem;cursor:pointer}
 code{background:#0b0e13;padding:.1rem .3rem;border-radius:4px}
 ul{margin:.3rem 0 .3rem 1.2rem}
</style></head><body>
<h1>NOEZEMA — узел</h1>
<p><a href="/knowledge">Знание (граф)</a> · <a href="/diagnostics">Диагностика</a></p>
<p><a href="/metrics">Метрики (§16)</a> · <a href="/evaluation">Evaluation (§22.2)</a></p>
<div id="banner" class="none">загрузка…</div>
<div class="card"><b>Узел:</b> <span id="node">—</span> · <b>Фазa:</b> <span id="phase">—</span></div>
<div class="card"><b>Конфиг:</b> <code id="cfg">—</code></div>
<div class="card"><b>Сессия:</b> <span id="sess">—</span></div>
<div class="card"><b>Ресурсы:</b> <span id="counts">—</span></div>
<div class="card"><b>Предупреждения:</b> <ul id="warns"></ul></div>
<div class="card"><b>Задать вопрос</b> (operator intake, T7.59)
 <form id="ask-form">
  <p><textarea id="ask-text" rows="3" cols="64" maxlength="2000"
    placeholder="Вопрос NOEZEMA: origin message, priority поднимает его в начало очереди"></textarea></p>
  <p><label>priority <input id="ask-priority" type="number" min="-100" max="100" step="1" value="0"></label>
     <label>admin token <input id="ask-token" type="password" size="18" placeholder="X-Admin-Token"></label>
     <button type="submit">Задать вопрос</button></p>
 </form>
 <p id="ask-result" class="warn"></p>
</div>
<div class="card"><b>Очередь вопросов</b> (FIFO: priority ↓, затем возраст)
 <table id="queue"><thead><tr><th>#</th><th>id</th><th>текст</th><th>state</th><th>pr</th>
  <th>origin</th><th>сессия</th><th>created_at</th></tr></thead><tbody></tbody></table>
 <p id="queue-error" class="warn"></p>
</div>
<div class="card"><b>Узел</b> · <button id="wake-now" type="button">wake now</button>
 <span>§5.2.1: обходит тайминг расписания, шлюзы admission остаются</span>
 <p id="wake-result" class="warn"></p>
</div>
<script>
async function tick(){
  try{
    const r = await fetch('/api/v1/status'); const s = await r.json();
    const b = document.getElementById('banner');
    const h = s.host||{}; const rs = h.recovery_state||'none';
    b.textContent = {none:'здоров',retry_wait:'retry_wait — ожидание',
      resume_degraded:'degraded — деградация',resume_blocked:'blocked — требуется оператор'}[rs]||rs;
    b.className = rs==='none'?'none':(rs==='retry_wait'?'retry':(rs==='resume_degraded'?'degraded':'blocked'));
    document.getElementById('node').textContent = s.node_state||'—';
    document.getElementById('node').className = s.node_state==='session_running'?'ok':'warn';
    document.getElementById('phase').textContent = (s.session&&s.session.state)||'нет активной';
    document.getElementById('cfg').textContent = (s.config&&s.config.sha256||'—').slice(0,16);
    const sess = s.session;
    document.getElementById('sess').innerHTML = sess
      ? `<a href="/session/${sess.id}">${sess.state}</a>` : 'нет активной';
    document.getElementById('counts').textContent =
      `вопросы ${s.counts.questions} · сессии ${s.counts.sessions} · сообщения ${s.counts.messages}`;
    const w = document.getElementById('warns'); w.innerHTML='';
    for(const x of (h.warnings||[])){ const li=document.createElement('li');
      li.textContent=x; li.className='warn'; w.appendChild(li); }
  }catch(e){ document.getElementById('banner').textContent='ошибка чтения статуса'; }
}
const TOKEN_KEY='noezema.admin.token';
function esc(v){return String(v===null||v===undefined?'':v);}
function adminToken(){
  const el=document.getElementById('ask-token');
  const t=(el.value||'').trim() || (sessionStorage.getItem(TOKEN_KEY)||'');
  if(t) sessionStorage.setItem(TOKEN_KEY,t);
  return t;
}
(function(){ const t=sessionStorage.getItem(TOKEN_KEY); if(t) document.getElementById('ask-token').value=t; })();
async function loadQueue(){
  try{
    const r = await fetch('/api/v1/questions?limit=25'); const d = await r.json();
    const tb = document.querySelector('#queue tbody'); tb.innerHTML='';
    for(const q of d.questions){
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${q.position===null||q.position===undefined?'':q.position}</td>`+
        `<td>${esc(q.id).slice(0,8)}</td><td class="wrap">${esc(q.text)}</td>`+
        `<td>${esc(q.state)}</td><td>${q.priority}</td><td>${esc(q.origin)}</td>`+
        `<td>${q.session?`<a href="/session/${esc(q.session.id)}">${esc(q.session.state)}</a>`:'—'}</td>`+
        `<td>${new Date(q.created_at).toLocaleString()}</td>`;
      tb.appendChild(tr);
    }
    document.getElementById('queue-error').textContent =
      d.questions.length ? '' : 'очередь пуста: задай вопрос или дождись новых кандидатов';
  }catch(err){ document.getElementById('queue-error').textContent='очередь недоступна'; }
}
document.getElementById('ask-form').addEventListener('submit', async (ev)=>{
  ev.preventDefault();
  const out = document.getElementById('ask-result');
  const body = {text:document.getElementById('ask-text').value,
                priority:Number(document.getElementById('ask-priority').value||0)};
  let r;
  try{
    r = await fetch('/api/v1/questions', {method:'POST',
      headers:{'Content-Type':'application/json','X-Admin-Token':adminToken()},
      body:JSON.stringify(body)});
  }catch(err){ out.textContent='ошибка отправки'; return; }
  const d = await r.json().catch(()=>({}));
  if(r.status===201) out.textContent=`вопрос принят: id ${d.id}`+
    ` · позиция в очереди ${d.position===null?'—':d.position}`;
  else if(r.status===200) out.textContent=`такой текст уже в очереди: id ${d.id} · state ${d.state}`;
  else if(r.status===401) out.textContent='нужен admin token (заголовок X-Admin-Token)';
  else if(r.status===423) out.textContent='хост деградирован: mutating endpoints закрыты';
  else out.textContent=`отклонено (${r.status}): ${d.detail||d.error||''}`;
  loadQueue();
});
 function idemKey(){
   // crypto.randomUUID exists only in a secure context; on a LAN stand over http it does not.
   const c = window.crypto && typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : null;
   return c || ('web-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10));
 }
 document.getElementById('wake-now').addEventListener('click', async ()=>{
   const out = document.getElementById('wake-result'); out.textContent='запуск…';
   let r;
   try{
     r = await fetch('/api/v1/commands', {method:'POST',
       headers:{'Content-Type':'application/json','X-Admin-Token':adminToken()},
       body:JSON.stringify({type:'wake_now', idempotency_key:idemKey(), reason='web: wake now'})});
   }catch(err){ out.textContent='ошибка отправки'; return; }
   const d = await r.json().catch(()=>({}));
   if(r.status===202) out.textContent = `${d.state||''}: `+
     (d.result&&d.result.reason ? d.result.reason : (d.result&&d.result.node_state)||'');
   else if(r.status===401) out.textContent='нужен admin token (заголовок X-Admin-Token)';
   else if(r.status===423) out.textContent='хост деградирован: команды закрыты';
   else out.textContent=`отклонено (${r.status}): ${d.detail||d.error||''}`;
   tick(); loadQueue();
 });
tick(); setInterval(tick, 3000);
loadQueue(); setInterval(loadQueue, 5000);
</script></body></html>
"""

_SESSION_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOEZEMA — сессия</title>
<style>
 body{font-family:system-ui,sans-serif;margin:2rem;background:#0e1116;color:#e6e6e6}
 .ev{background:#171c26;border:1px solid #2a3142;border-radius:6px;padding:.5rem .8rem;margin:.4rem 0}
 .t{color:#7bd88f} .s{color:#9aa0a6;font-size:.9rem}
</style></head><body>
<h1>NOEZEMA — сессия <code>__SESSION_ID__</code></h1>
<div id="detail">загрузка…</div>
<ul id="events"></ul>
<script>
const sid = "__SESSION_ID__";
async function tick(){
  try{
    const r = await fetch('/api/v1/sessions/'+sid);
    if(!r.ok){document.getElementById('detail').textContent='не найдена';return;}
    const s = await r.json();
    document.getElementById('detail').innerHTML =
      `<b>Фазa:</b> ${s.state} · <b>Вопрос:</b> ${s.question_id||'—'}`;
    const ul = document.getElementById('events'); ul.innerHTML='';
    for(const e of s.events){ const li=document.createElement('div'); li.className='ev';
      li.innerHTML = `<span class="t">${e.sequence} ${e.type}</span> `+
        `<span class="s">${e.public_summary||''} · ${new Date(e.occurred_at).toLocaleTimeString()}</span>`;
      ul.appendChild(li); }
  }catch(err){ document.getElementById('detail').textContent='ошибка чтения'; }
}
tick(); setInterval(tick, 2500);
</script></body></html>
"""


# T7.1: knowledge graph + diagnostics pages (thin viewers over the JSON
# API; the invariant lives server-side, the HTML only renders it).
_KNOWLEDGE_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOEZEMA — знание</title>
<style>
 body{font-family:system-ui,sans-serif;margin:2rem;background:#0e1116;color:#e6e6e6}
 table{border-collapse:collapse;width:100%}
 td,th{border:1px solid #2a3142;padding:.4rem .6rem;text-align:left;vertical-align:top}
 th{background:#171c26}
 .cur{color:#7bd88f}.pen{color:#e5c07b}.inv{color:#e06c75}.non{color:#9aa0a6}
 code{background:#0b0e13;padding:.1rem .3rem;border-radius:4px}
 a{color:#61afef;text-decoration:none}
</style></head><body>
<h1>NOEZEMA — знание</h1>
<p><a href="/">← узел</a></p>
<div id="box">загрузка…</div>
<script>
async function tick(){
  try{
    const r = await fetch('/api/v1/knowledge/claims?limit=200');
    const d = await r.json();
    let h = `<table><tr><th>утверждение</th><th>тип</th><th>head</th>`+
      `<th>статус</th><th>grade</th><th>свежесть</th></tr>`;
    for(const c of d.claims){
      const cls = {current:'cur',pending:'pen',invalid:'inv',none:'non'}[c.head_state]||'non';
      h += `<tr><td><a href="/claim/${c.id}">${c.statement.slice(0,120)}</a></td>`+
        `<td>${c.claim_type}</td><td class="${cls}">${c.head_state}</td>`+
        `<td>${c.epistemic_status||'—'}</td><td>${c.effective_grade||'—'}</td>`+
        `<td>${c.freshness_status}</td></tr>`;
    }
    h += '</table>';
    document.getElementById('box').innerHTML = h;
  }catch(e){ document.getElementById('box').textContent='ошибка чтения'; }
}
tick(); setInterval(tick, 5000);
</script></body></html>
"""

_CLAIM_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOEZEMA — утверждение</title>
<style>
 body{font-family:system-ui,sans-serif;margin:2rem;background:#0e1116;color:#e6e6e6}
 .card{background:#171c26;border:1px solid #2a3142;border-radius:8px;padding:1rem;margin:.7rem 0}
 code{background:#0b0e13;padding:.1rem .3rem;border-radius:4px}
 .ok{color:#7bd88f}.warn{color:#e5c07b}.bad{color:#e06c75}
 a{color:#61afef;text-decoration:none}
 ul{margin:.3rem 0 .3rem 1.2rem}
</style></head><body>
<h1>NOEZEMA — утверждение</h1>
<p><a href="/knowledge">← знание</a></p>
<div id="box">загрузка…</div>
<script>
const cid = "__CLAIM_ID__";
const esc = s => (s||'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function tick(){
  try{
    const [d, p] = await Promise.all([
      fetch('/api/v1/knowledge/claims/'+cid).then(r => r.ok ? r.json() : null),
      fetch('/api/v1/knowledge/claims/'+cid+'/provenance').then(r => r.ok ? r.json() : null),
    ]);
    if(!d){ document.getElementById('box').textContent='не найдено'; return; }
    const cur = d.heads.find(h => h.assessment_state === 'current') || d.heads[0];
    let h = '<div class="card"><b>Утверждение:</b> ' + esc(d.statement) +
      '<br><b>Тип:</b> ' + d.claim_type + ' · <b>свежесть:</b> ' + d.freshness_status +
      (d.as_of ? ' · <b>as_of:</b> ' + d.as_of : '') + '</div>';
    h += '<div class="card"><b>Оценки (по снапшотам):</b><ul>';
    for(const hd of d.heads){
      const cls = hd.assessment_state==='current' ? 'ok' : (hd.assessment_state==='pending' ? 'warn' : 'bad');
      h += `<li class="${cls}">${hd.assessment_state} · ${hd.epistemic_status||'—'} · `+
        `${hd.effective_grade||'—'} · conf ${hd.confidence ?? '—'} `+
        `(<code>${(hd.config_snapshot_id||'').slice(0,8)}</code>, `+
        `${hd.activation_state||hd.activation_mode||'?'})</li>`;
    }
    h += '</ul></div>';
    h += '<div class="card"><b>Зависимости:</b><ul>';
    for(const x of d.depends_on)
      h += `<li>зависит от: <a href="/claim/${x.claim_id}">${esc(x.statement.slice(0,80))}</a> (${x.kind})</li>`;
    for(const x of d.depended_by)
      h += `<li>используется: <a href="/claim/${x.claim_id}">${esc(x.statement.slice(0,80))}</a> (${x.kind})</li>`;
    if(!d.depends_on.length && !d.depended_by.length) h += '<li>нет</li>';
    h += '</ul></div>';
    if(p){
      h += '<div class="card"><b>Происхождение (evidence → source/artifact):</b><ul>';
      for(const e of p.evidence){
        let src = '—';
        if(e.source) src = `${e.source.source_type} <code>${esc((e.source.canonical_uri||'').slice(0,60))}</code>`+
          (e.source.parent ? ' ← ' + esc((e.source.parent.canonical_uri||'').slice(0,60)) : '');
        let art = '—';
        if(e.artifact) art = `<code>${e.artifact.sha256.slice(0,16)}</code> (${e.artifact.trust_class})`;
        h += `<li>${e.relation}/${e.evidence_kind}: source: ${src}; artifact: ${art}`;
        for(const r of p.assessment_evidence_roles.filter(r => r.evidence_id === e.id))
          h += ` · role: ${r.role}`;
        h += '</li>';
      }
      if(p.source_groups.length){
        h += '<br><b>Группы независимости источников:</b> ';
        h += p.source_groups.map(g => `<code>${g.group_id}</code> (${esc((g.uri||'').slice(0,50))})`).join(', ');
      }
      if(p.environment_groups.length){
        h += '<br><b>Группы окружений:</b> ' + p.environment_groups.map(g => `<code>${g.group_id}</code>`).join(', ');
      }
      h += '</ul></div>';
    }
    document.getElementById('box').innerHTML = h;
  }catch(e){ document.getElementById('box').textContent='ошибка чтения'; }
}
tick(); setInterval(tick, 5000);
</script></body></html>
"""

_DIAGNOSTICS_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOEZEMA — диагностика</title>
<style>
 body{font-family:system-ui,sans-serif;margin:2rem;background:#0e1116;color:#e6e6e6}
 .card{background:#171c26;border:1px solid #2a3142;border-radius:8px;padding:1rem;margin:.7rem 0}
 code{background:#0b0e13;padding:.1rem .3rem;border-radius:4px}
 .ok{color:#7bd88f}.warn{color:#e5c07b}.bad{color:#e06c75}
 ul{margin:.3rem 0 .3rem 1.2rem}
 a{color:#61afef;text-decoration:none}
</style></head><body>
<h1>NOEZEMA — диагностика</h1>
<p><a href="/">← узел</a></p>
<div id="box">загрузка…</div>
<script>
const esc = s => (s||'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function tick(){
  try{
    const [s, rec, bar, jobs] = await Promise.all([
      fetch('/api/v1/diagnostics').then(r => r.json()),
      fetch('/api/v1/diagnostics/reconciliation').then(r => r.json()),
      fetch('/api/v1/diagnostics/barriers').then(r => r.json()),
      fetch('/api/v1/diagnostics/jobs?limit=20').then(r => r.json()),
    ]);
    let h = '';
    const act = s.activation || {};
    h += `<div class="card"><b>Активация конфига:</b> `+
      `active <code>${(act.active_snapshot_id||'').slice(0,8)}</code>` +
      (act.activating_snapshot_id
        ? ` · activating <code>${act.activating_snapshot_id.slice(0,8)}</code> `+
          `(fence ${act.fence}, owner ${esc(act.lease_owner)})`
        : '') +
      ` · gate: ${s.writer_gate && s.writer_gate.owner_id ? esc(s.writer_gate.owner_id) : 'свободен'}` +
      ` · ревизии: ${Object.entries(s.revisions||{}).map(([k,v]) => k+'='+v).join(' · ')}</div>`;
    const bk = s.backups || {total: 0};
    const lastDrill = bk.last_verified_at
      ? ` · последний restore drill: ${bk.last_verified_at.slice(0,19)}`
      : ' · restore drill ещё не был';
    h += `<div class="card"><b>Backup/PITR:</b> `+
      `всего ${bk.total}, в retention ${bk.in_retention ?? 0}` + lastDrill + `</div>`;
    const un = rec.unresolved;
    h += `<div class="card"><b class="${un?'bad':'ok'}">Рекомендация/коммит:</b> `+
      (un ? 'есть нерешённые commit_attempts — блокируют wake и GC' : 'нерешённых попыток нет');
    for(const e of rec.entries){
      h += `<ul><li>сессия <code>${(e.session_id||'').slice(0,8)}</code> (${e.session_state})`+
        (e.attempt
          ? ` · attempt ${e.attempt.status} (base k=${e.attempt.base_knowledge_revision}, `+
            `dg=${e.attempt.base_dependency_graph_revision})`
          : '') +
        (e.checkpoint ? ` · checkpoint k=${e.checkpoint.knowledge_revision}` : '') + '</li></ul>';
    }
    h += '</div>';
    const ob = Object.entries(s.open_barriers||{});
    const obN = ob.reduce((a, [,v]) => a + (v.n||0), 0);
    h += `<div class="card"><b class="${obN?'warn':'ok'}">Барьеры инвалидации:</b> `+
      (obN ? `${obN} открытых` : 'нет открытых');
    for(const b of bar.barriers){
      const cls = b.status==='blocked' ? 'bad' : 'warn';
      h += `<ul><li class="${cls}">${b.status} gen ${b.generation}: ${esc((b.root_statement||'').slice(0,80))}`+
        ` · прогресс ${b.closure_progress}` +
        (b.last_error ? ` · <code>${esc(b.last_error.slice(0,60))}</code>` : '') + '</li></ul>';
    }
    h += '</div>';
    const jb = (s.reassessment_jobs||{}).blocked || 0;
    h += `<div class="card"><b class="${jb?'bad':'ok'}">Джобы переоценки:</b> `+
      Object.entries(s.reassessment_jobs||{}).map(([k,v]) => k+'='+v).join(' · ') || 'нет';
    for(const j of jobs.jobs.slice(0,10)){
      const cls = j.status==='blocked' ? 'bad' : (j.status==='retry' ? 'warn' : 'ok');
      h += `<ul><li class="${cls}">${j.status} · ${j.attempts}/${j.max_attempts} попыток`+
        (j.error_class ? ` · ${j.error_class}` : '') +
        (j.next_attempt_at ? ` · next ${j.next_attempt_at}` : '') +
        (j.claim_statement ? ` · ${esc(j.claim_statement.slice(0,60))}` : '') + '</li></ul>';
    }
    h += '</div>';
    document.getElementById('box').innerHTML = h;
  }catch(e){ document.getElementById('box').textContent='ошибка чтения'; }
}
tick(); setInterval(tick, 5000);
</script></body></html>
"""

_METRICS_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOEZEMA — метрики (§16)</title>
<style>
 body{font-family:system-ui,sans-serif;margin:2rem;background:#0e1116;color:#e6e6e6}
 .card{background:#171c26;border:1px solid #2a3142;border-radius:8px;padding:1rem;margin:.7rem 0}
 code{background:#0b0e13;padding:.1rem .3rem;border-radius:4px}
 h2{margin:.3rem 0 .5rem;font-size:1.05rem;color:#61afef}
 ul{margin:.3rem 0 .3rem 1.2rem}
 a{color:#61afef;text-decoration:none}
 .bad{color:#e06c75}
</style></head><body>
<h1>NOEZEMA — метрики (§16)</h1>
<p><a href="/">← узел</a> · <a href="/diagnostics">Диагностика</a></p>
<div id="box">загрузка…</div>
<script>
const esc = s => (s==null?'—':String(s)).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function obj(o){
  return Object.entries(o||{}).map(([k,v]) =>
    `<li><code>${esc(k)}</code>: ${esc(v)}</li>`).join('') || '<li>—</li>';
}
function barriers(o){
  return Object.entries(o||{}).map(([k,v]) =>
    `<li><code>${esc(k)}</code>: ${esc(v.n)} (members ${esc(v.members)})</li>`).join('');
}
function kv(label, o, fields){
  return fields.map(f => [f, o[f]]).filter(([,v]) => v!=null)
    .map(([k,v]) => esc(k)+' '+esc(v)).join(', ');
}
async function tick(){
  try{
    const m = await fetch('/api/v1/metrics').then(r => r.json());
    const t = m.technical || {}, c = m.cognitive || {}, s = m.security || {};
    let h = '';
    h += '<div class="card"><h2>Технические (§16.1)</h2>';
    h += '<p>commit_attempts</p><ul>' + obj(t.commit_attempts) + '</ul>';
    h += '<p>oldest_unresolved_attempt: '
       + esc(t.oldest_unresolved_attempt_at) + '</p>';
    h += '<p>open_barriers</p><ul>' + barriers(t.open_barriers) + '</ul>';
    h += '<p>reassessment_jobs</p><ul>' + obj(t.reassessment_jobs) + '</ul>';
    if (t.backups)
      h += '<p>backups: ' + kv('', t.backups,
        ['total','in_retention','oldest_backup_at','last_verified_at']) + '</p>';
    if (t.gc)
      h += '<p>gc: ' + kv('', t.gc,
        ['applied_sweeps','artifacts_deleted','last_sweep_at']) + '</p>';
    if (t.session_latency)
      h += '<p>session_latency: ' + kv('', t.session_latency,
        ['n','avg_seconds','max_seconds']) + 's</p>';
    h += '</div>';
    h += '<div class="card"><h2>Познавательные (§16.2)</h2>';
    h += '<p>claims_by_epistemic_status</p><ul>'
       + obj(c.claims_by_epistemic_status) + '</ul>';
    h += '<p>assessments_by_grade</p><ul>'
       + obj(c.assessments_by_grade) + '</ul>';
    h += '<p>reassessment_jobs</p><ul>' + obj(c.reassessment_jobs) + '</ul>';
    if (c.counterevidence)
      h += '<p>counterevidence: ' + kv('', c.counterevidence,
        ['found','resolved']) + '</p>';
    h += '</div>';
    h += '<div class="card"><h2>Безопасность и взаимодействие (§16.3)</h2>';
    h += '<p>policy (deny / require_operator)</p><ul>' + obj(s.policy) + '</ul>';
    h += '<p>egress_rejections</p><ul>' + obj(s.egress_rejections) + '</ul>';
    const badCls = s.idempotency_mismatches > 0 ? 'bad' : '';
    h += '<p>idempotency_mismatches: <span class="' + badCls + '">'
       + esc(s.idempotency_mismatches) + '</span></p>';
    h += '<p>source_graph_corrections: ' + esc(s.source_graph_corrections) + '</p>';
    h += '<p>stop/abort_commands</p><ul>' + obj(s.stop_abort_commands) + '</ul>';
    h += '<p>command_like_messages (без исполнения): '
       + esc(s.command_like_messages) + '</p>';
    h += '<p>egress_rate_limited: ' + esc(s.egress_rate_limited) + '</p>';
    h += '</div>';
    document.getElementById('box').innerHTML = h;
  }catch(e){ document.getElementById('box').textContent='ошибка чтения'; }
}
tick(); setInterval(tick, 5000);
</script></body></html>
"""

_EVALUATION_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOEZEMA — evaluation (§22.2)</title>
<style>
 body{font-family:system-ui,sans-serif;margin:2rem;background:#0e1116;color:#e6e6e6}
 .card{background:#171c26;border:1px solid #2a3142;border-radius:8px;padding:1rem;margin:.7rem 0}
 code{background:#0b0e13;padding:.1rem .3rem;border-radius:4px}
 table{border-collapse:collapse;width:100%}
 th,td{border:1px solid #2a3142;padding:.4rem .6rem;text-align:left}
 th{background:#171c26}
 a{color:#61afef;text-decoration:none}
 .ok{color:#7bd88f}.warn{color:#e5c07b}.bad{color:#e06c75}
</style></head><body>
<h1>NOEZEMA — evaluation (§22.2)</h1>
<p><a href="/">← узел</a> · <a href="/metrics">Метрики</a></p>
<div id="box">загрузка…</div>
<script>
const esc = s => (s==null?'—':String(s)).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function outcomeCls(o){
  return o==='passed'?'ok':(o==='failed'?'bad':'warn');
}
async function tick(){
  try{
    const m = await fetch('/api/v1/evaluation').then(r => r.json());
    const runs = m.runs || [];
    let h = '<table><tr><th>label</th><th>outcome</th><th>started</th>'
      + '<th>eligible</th><th>completed</th><th>blind size</th></tr>';
    for (const r of runs){
      const cls = outcomeCls(r.outcome);
      h += '<tr><td><a href="/evaluation/' + esc(r.id) + '">' + esc(r.label) + '</a></td>'
        + '<td class="' + cls + '">' + esc(r.outcome) + '</td>'
        + '<td>' + esc((r.started_at||'').slice(0,19)) + '</td>'
        + '<td>' + esc(r.eligible_sessions) + '</td>'
        + '<td>' + esc(r.completed_sessions) + '</td>'
        + '<td>' + esc(r.blind_sample_size) + '</td></tr>';
    }
    h += '</table>';
    if (runs.length===0) h += '<p class="warn">нет evaluation runs</p>';
    document.getElementById('box').innerHTML = h;
  }catch(e){ document.getElementById('box').textContent='ошибка чтения'; }
}
tick(); setInterval(tick, 5000);
</script></body></html>
"""


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=2000)
    priority: int = Field(default=0, ge=-100, le=100)
    sender: str = Field(default="owner", min_length=1, max_length=100)


class QuestionIn(BaseModel):
    """T7.59: operator question intake. The bounds are the intake service's
    (one source for CLI + API); the service re-validates them itself, so a
    hand-built request that skips pydantic still cannot bypass them."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_QUESTION_TEXT_CHARS)
    priority: int = Field(default=0, ge=PRIORITY_MIN, le=PRIORITY_MAX)


class CommandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: OperatorCommandType
    idempotency_key: str = Field(min_length=1, max_length=200)
    arguments: JsonDict = Field(default_factory=dict)
    actor_id: str = Field(default="operator", min_length=1, max_length=100)
    reason: str | None = Field(default=None, max_length=500)


class _Node:
    """What the web process owns IN MEMORY (T7.59(в)).

    The node STATE is deliberately not here. `system_constants.node_state` in the DB is the single
    source of truth (AGENTS §3 "effective config/Host-контур": the state is a host fact, and an
    external `hostctl wake-tick` — a different process — writes it). Reading it once in the
    lifespan made the copy stale forever: a dev stand whose web started while an external tick held
    a session remembered `session_running`, answered every later «wake now» with "a session is
    already running" and showed the wrong node_state in /api/v1/status even though the DB said
    `idle`. Only what no other process can know stays in memory: the session task THIS process
    started, and its last error.
    """

    def __init__(self) -> None:
        self.session_task: asyncio.Task[SessionOutcome] | None = None
        self.last_error: str | None = None
        # T7.61(а): this process's hold on the node's session lane (see apps/orchestrator/node_guard.py).
        # It lives here, not in the DB, because it IS this process: released when the session task ends
        # and — as a safety net — by the lifespan shutdown if the task was cancelled before starting.
        self.session_guard: NodeSessionGuard | None = None
        self._resets: set[asyncio.Task[None]] = set()


# T7.63: how long a shutdown may wait for the accounting work this process started itself. A ceiling
# against a hang — no test and no production path is allowed to depend on this duration.
_SHUTDOWN_DRAIN_CEILING_SECONDS = 20.0

logger = logging.getLogger(__name__)


async def _await_with_ceiling(*tasks: asyncio.Task[Any], ceiling: float = _SHUTDOWN_DRAIN_CEILING_SECONDS) -> None:
    """Wait for tasks this process owns, but never longer than `ceiling` seconds (T7.63).

    A task that does not finish is cancelled and NAMED in the log: shutdown must not hang on a wedged
    database, and an abandoned accounting write has to be visible rather than silent.
    """
    pending = [task for task in tasks if not task.done()]
    if not pending:
        return
    _, still_running = await asyncio.wait(pending, timeout=ceiling)
    for task in still_running:
        logger.warning(
            "shutdown drain: %s did not finish within %.1f s — cancelled, its accounting is incomplete",
            task.get_name(),
            ceiling,
        )
        task.cancel()


def _now() -> datetime:
    return datetime.now(UTC)


async def _load_node_state(db: AsyncSession) -> str:
    """The DB truth about the node state (also what /api/v1/status reports)."""
    stmt = select(ORMSystemConstant).where(ORMSystemConstant.key == NODE_STATE_KEY)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return "idle"
    return row.value if row.value in NODE_STATES else "idle"


def _owns_live_session(node: _Node) -> bool:
    """True while this web process runs a session it started itself (wake_now)."""
    return node.session_task is not None and not node.session_task.done()


def effective_node_state(
    raw: str, *, web_owns_session: bool, db_has_nonterminal_session: bool
) -> str:
    """The state a command is decided on (§5.2.1, T7.59(в)).

    `session_running` is a marker written by whoever started the session — the web or an external
    tick — and it only means something while a session really runs. When the marker is
    `session_running`, no session row is nonterminal and this process owns no session task, the
    marker is left over from an external tick that has since ended (a killed/timed-out unit, a
    crash before the session row existed): honouring it would wedge the node — every «wake now»
    refused forever. Everything else (`idle`, `paused`) is taken from the DB as is: an operator
    pause is sticky until the operator resume.

    Invariant kept: while an external tick really holds a session (the DB says `session_running`
    AND a nonterminal session exists), wake_now is refused; after that session terminates it is
    accepted again.
    """
    if raw == "session_running" and not web_owns_session and not db_has_nonterminal_session:
        return "idle"
    return raw


async def _effective_state(db: AsyncSession, node: _Node) -> str:
    """Read the DB truth for this request and resolve a leftover external-tick marker."""
    active = await SessionRepository.list_nonterminal(db)
    return effective_node_state(
        await _load_node_state(db),
        web_owns_session=_owns_live_session(node),
        db_has_nonterminal_session=bool(active),
    )


async def _save_node_state(db: AsyncSession, state: str) -> None:
    stmt = select(ORMSystemConstant).where(ORMSystemConstant.key == NODE_STATE_KEY)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        db.add(ORMSystemConstant(key=NODE_STATE_KEY, value=state))
    else:
        row.value = state
    await db.flush()


def create_app(
    db_url: str | None = None,
    orchestrator: Orchestrator | None = None,
    engine: AsyncEngine | None = None,
    factory: async_sessionmaker[AsyncSession] | None = None,
    host_adapter: HostStatusAdapter | None = None,
    admin_token: str | None = None,
) -> FastAPI:
    owns_engine = engine is None
    if engine is None:
        settings = DatabaseSettings()
        engine = create_async_engine(db_url or settings.database_url)
    if factory is None:
        factory = async_sessionmaker(engine, expire_on_commit=False)
    node = _Node()
    # T3.18: the local admin token (commands require it; queries are open)
    _admin_token = admin_token if admin_token is not None else os.environ.get("NOEZEMA_ADMIN_TOKEN", "")
    # T3.24: host status adapter (fail-closed command gate + status view)
    if host_adapter is None:
        host_adapter = HostStatusAdapter(
            host_lib_base=Path(os.environ.get("NOEZEMA_HOST_LIB", "/var/lib/noezema")),
            unit_state_path=Path(os.environ.get("NOEZEMA_UNIT_STATE", "/run/noezema/unit-state.json")),
        )
    # T3.29: wake scheduler identity (wake admission + backoff, §5.2.1)
    _node_owner = node_owner_from_env()
    _data_root = data_root_from_env()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # T7.59(в): no state is cached here — the node state is read from the DB on every command
        # and every status query, so an external tick (or another writer) cannot desynchronise us.
        yield
        if node.session_task is not None and not node.session_task.done():
            node.session_task.cancel()
            # T7.63: awaiting it is what lets its done-callback run — and that callback CREATES the
            # outcome task (§5.2.1 «учёт исхода»). Draining without this would drain an empty set and
            # abandon the accounting write all the same.
            await _await_with_ceiling(node.session_task)
        # T7.63 (§5.2.1 «…сессия → учёт исхода → снятие»): the outcome task writes through THIS engine.
        # Abandoning it loses the wake bookkeeping mid-write and leaves its session attached to the DB as
        # a live idle transaction until the process dies — visible as `database ... is being accessed by
        # other users` for anyone who has to drop or recreate that DB. Bounded, so shutdown still cannot
        # hang on a wedged database: an unfinished task is cancelled and named in the log.
        await _await_with_ceiling(*list(node._resets))
        # T7.61(а): safety net for the session lane. The task's own finally releases it; this covers the
        # edge where the process shuts down with a held guard (cancelled before starting, or a release that
        # could not run). An unheld guard releases to a no-op — no lock is dropped that is not ours.
        if node.session_guard is not None:
            await node.session_guard.release()
        if owns_engine:
            await engine.dispose()

    app = FastAPI(title="NOEZEMA", version="0.2.0-m3", lifespan=lifespan)
    app.state.engine = engine
    app.state.factory = factory
    app.state.orchestrator = orchestrator
    app.state.node = node
    app.state.owns_engine = owns_engine
    app.state.host_adapter = host_adapter
    app.state.admin_token = _admin_token

    # ── T3.18: command auth (local admin token) ──────────────────────────

    def _check_admin(request: Request) -> None:
        provided = request.headers.get("X-Admin-Token")
        if provided is None:
            auth = request.headers.get("Authorization")
            if auth is not None:
                provided = auth.removeprefix("Bearer ").strip()
        if _admin_token and provided != _admin_token:
            raise HTTPException(status_code=401, detail="invalid admin token")
        # no token configured: local single-operator, allow (MVP)

    def _command_orchestrator() -> Orchestrator | None:
        """The orchestrator that answers a command is the one ATTACHED AT COMMAND TIME.

        T7.59(в) defect of the real stand: `build_standalone_app` — the entry every stand and every
        production web unit uses — cannot build the orchestrator before the app owns its session
        factory (the orchestrator shares that factory), so it attaches the orchestrator to
        `app.state.orchestrator` AFTER create_app returned. The command handler closed over the
        create_app argument instead, which is None in that path, so every «wake now» answered
        `rejected: orchestrator not attached`. Tests never caught it because they passed the
        orchestrator as a create_app argument.

        `create_app(orchestrator=…)` behaviour is unchanged — it seeds app.state.orchestrator;
        reading app.state at command time simply also honours what was attached later.
        """
        attached: Orchestrator | None = app.state.orchestrator
        return attached

    # ── queries ───────────────────────────────────────────────────────────

    @app.get("/api/v1/status")
    async def status() -> JsonDict:
        async with factory() as db:
            try:
                snapshot: ORMConfigSnapshot = await ConfigService.get_effective(db)
            except ConfigError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            active = await SessionRepository.list_nonterminal(db)
            session = active[0] if active else None
            node_state = await _load_node_state(db)
            counts = {
                "questions": (await db.execute(select(func.count(ORMQuestion.id)))).scalar_one(),
                "sessions": (await db.execute(select(func.count(ORMSession.id)))).scalar_one(),
                "messages": (await db.execute(select(func.count(ORMMessage.id)))).scalar_one(),
            }
            out = {
                # T7.59(в): the node state in the view is the DB value, not a copy made at startup.
                # `node_state_stale_marker` names the one case where the marker no longer describes
                # a running session (session_running without any nonterminal session and without a
                # session this web started) — commands are decided on the resolved state instead.
                "node_state": node_state,
                "node_state_stale_marker": node_state == "session_running"
                and not active
                and not _owns_live_session(node),
                "last_error": node.last_error,
                "config": {
                    "snapshot_id": str(snapshot.id),
                    "sha256": snapshot.sha256,
                    "payload_sha256": snapshot.payload_sha256,
                },
                "session": (
                    {
                        "id": str(session.id),
                        "state": session.state,
                        "question_id": str(session.question_id) if session.question_id else None,
                    }
                    if session is not None
                    else None
                ),
                "counts": counts,
                # T3.29: wake bookkeeping (backoff/pause observability)
                "wake": await WakeScheduler(db, node_owner=_node_owner, data_root=_data_root).status(),
            }
        # T3.20/T3.24: host status (recovery banner + warnings) is a host
        # view, not a DB query — it must survive a DB outage (fail-closed)
        try:
            host = host_adapter.read()
        except Exception as exc:  # the adapter is best-effort; a disk error
            # degrades the view, never crashes the status endpoint
            host = HostStatus(
                healthy=False,
                recovery_state="resume_blocked",
                warnings=[f"host_status_unreadable:{type(exc).__name__}"],
            )
        out["host"] = annotate_host_status(host.to_dict())
        # T7.64 (ADR-0026): человеческая подпись состояния узла; сырое значение
        # остаётся на месте — старые ключи и значения не тронуты.
        out["node_state_label"] = ui_labels.describe("node_state", node_state)["label"]
        out["node_state_hint"] = ui_labels.describe("node_state", node_state)["hint"]
        return out

    @app.get("/api/v1/timeline")
    async def timeline(
        session_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> JsonDict:
        limit = max(1, min(limit, 500))
        async with factory() as db:
            if session_id is None:
                row = (
                    await db.execute(select(ORMSession).order_by(ORMSession.created_at.desc()).limit(1))
                ).scalar_one_or_none()
                if row is None:
                    return {"session_id": None, "events": []}
                target: uuid.UUID | None = row.id
            else:
                target = session_id
            stmt = (
                select(ORMAuditEvent)
                .where(ORMAuditEvent.session_id == target)
                .order_by(ORMAuditEvent.sequence.asc())
                .limit(limit)
            )
            events = (await db.execute(stmt)).scalars().all()
            return {
                "session_id": str(target),
                "events": [
                    {
                        "sequence": e.sequence,
                        "type": e.type,
                        "type_label": ui_labels.describe("audit_event_type", e.type)["label"],
                        "occurred_at": e.occurred_at.isoformat(),
                        "actor": e.actor,
                        "public_summary": e.public_summary,
                        "payload": e.payload,
                        "visibility": e.visibility,
                    }
                    for e in events
                ],
            }

    # ── commands ──────────────────────────────────────────────────────────

    @app.post("/api/v1/messages", status_code=201)
    async def post_message(body: MessageIn) -> JsonDict:
        async with factory() as db, transaction(db):
            message = ORMMessage(
                sender=body.sender, body=body.body, priority=body.priority
            )
            await MessageRepository.create(db, message)
            return {"id": str(message.id), "state": message.state, "priority": message.priority}

    @app.post("/api/v1/commands", status_code=202, response_model=None)
    async def post_command(body: CommandIn, request: Request) -> JsonDict | JSONResponse:
        # T3.18: command auth (local admin token)
        _check_admin(request)
        # T3.24: fail-closed — refuse any command while the host is not
        # fully healthy (unresolved transition / active policy change /
        # stale unit-state / DB outage). The web drops to read-only.
        host = host_adapter.read()
        if not host.healthy:
            return JSONResponse(
                status_code=423,
                # T7.64 (ADR-0026): причина отказа и состояние восстановления
                # подписаны поверх существующих ключей; fail-closed поведение прежнее.
                content=annotate_reasons(
                    annotate_host_status(
                        {
                            "rejected": True,
                            "reason": "host_not_healthy",
                            "recovery_state": host.recovery_state,
                            "warnings": host.warnings,
                        }
                    )
                ),
            )
        async with factory() as db, transaction(db):
            command = ORMOperatorCommand(
                actor_id=body.actor_id,
                type=body.type.value,
                arguments=body.arguments,
                state=OperatorCommandState.ACCEPTED.value,
                idempotency_key=body.idempotency_key,
                reason=body.reason,
            )
            command, created = await OperatorCommandRepository.create(db, command)
            if not created:
                return {
                    "id": str(command.id),
                    "type": command.type,
                    "type_label": ui_labels.describe("command_type", command.type)["label"],
                    "state": command.state,
                    "state_label": ui_labels.describe("command_state", command.state)["label"],
                    "replayed": True,
                    "result": annotate_reasons(command.result),
                }
            state, result = await _apply_command(db, command, body)
            command.state = state.value
            command.result = result
            if state in (OperatorCommandState.COMPLETED, OperatorCommandState.REJECTED):
                command.finished_at = _now()
            await db.flush()
            return {
                "id": str(command.id),
                "type": command.type,
                "type_label": ui_labels.describe("command_type", command.type)["label"],
                "state": command.state,
                "state_label": ui_labels.describe("command_state", command.state)["label"],
                "replayed": False,
                "result": annotate_reasons(result),
            }

    async def _apply_command(
        db: AsyncSession, command: ORMOperatorCommand, body: CommandIn
    ) -> tuple[OperatorCommandState, JsonDict]:
        ctype = OperatorCommandType(command.type)
        # T7.59(в): the node state for THIS command comes from the DB — an external tick writes
        # system_constants.node_state, and a copy taken at startup goes stale (that was the stand
        # defect: «wake now» answered "a session is already running" while the DB said idle).
        state = await _effective_state(db, node)

        if ctype is OperatorCommandType.PAUSE:
            if state == "paused":
                return OperatorCommandState.COMPLETED, {"node_state": "paused", "noop": True}
            if state == "session_running":
                return OperatorCommandState.REJECTED, {"reason": "session is running; stop_gracefully instead"}
            await _save_node_state(db, "paused")
            # T3.29: explain the pause for the wake admission audit
            await _set_pause_reason(db, "operator")
            return OperatorCommandState.COMPLETED, {"node_state": "paused"}

        if ctype is OperatorCommandType.RESUME:
            if state != "paused":
                return OperatorCommandState.REJECTED, {"reason": "node is not paused"}
            await _save_node_state(db, "idle")
            # T3.29: the operator resume clears the failure bookkeeping, so
            # the node is eligible for the next scheduled tick immediately.
            await db.execute(
                text(
                    "UPDATE wake_scheduler_state SET consecutive_failures = 0, "
                    "backoff_until = NULL, last_failure_at = NULL, paused_reason = NULL, "
                    "updated_at = now() WHERE node_id = :n"
                ),
                {"n": _node_owner},
            )
            return OperatorCommandState.COMPLETED, {"node_state": "idle"}

        if ctype is OperatorCommandType.WAKE_NOW:
            if state == "paused":
                return OperatorCommandState.REJECTED, {"reason": "node is paused"}
            if state == "session_running":
                return OperatorCommandState.REJECTED, {"reason": "a session is already running"}
            orchestrator_ref = _command_orchestrator()
            if orchestrator_ref is None:
                return OperatorCommandState.REJECTED, {"reason": "orchestrator not attached"}
            # T3.29 (§5.2.1): wake_now bypasses the schedule timing (interval,
            # minimum gap, backoff) but NOT admission. Evaluated on a fresh
            # session so the request transaction stays intact.
            async with factory() as sdb:
                decision = await WakeScheduler(sdb, node_owner=_node_owner, data_root=_data_root).decide(
                    source="wake_now", now=_now()
                )
            if decision.action != "wake":
                return OperatorCommandState.REJECTED, {
                    "reason": decision.reason,
                    "paused_reason": decision.detail,
                }

            # T7.61(а) (§5.2.1): admitted wake ≠ free session lane. The stand race of this task is exactly
            # this: «wake now» (web) and `noezema-dev-tick.service` (another process) were both admitted
            # 36 s apart because phase 1 keeps the running session's row uncommitted until COMMITTING, and
            # `node_state='session_running'` is not an admission condition. The node advisory lock is taken
            # BEFORE this process writes the marker; whoever cannot take it does not start a session.
            guard = NodeSessionGuard(engine, _node_owner)
            try:
                lane_free = await guard.acquire()
            except NodeSessionGuardError as exc:
                node.last_error = str(exc)[:500]
                return OperatorCommandState.REJECTED, {"reason": f"session lane unavailable: {exc}"}
            if not lane_free:
                # Same operator-visible answer as the in-process case above (§5.2.1: exact reason recorded).
                return OperatorCommandState.REJECTED, {
                    "reason": "a session is already running",
                    "wake_reason": REASON_SESSION_IN_PROGRESS,
                }

            await _save_node_state(db, "session_running")
            node.session_guard = guard

            async def _run_locked_session() -> SessionOutcome:
                """The session task, wrapped so the lane is returned on EVERY exit (§5.2.1).

                A normal finish, an exception inside `run_session` and a cancellation (the lifespan
                shutdown cancels this task) all run this finally — that is what keeps a web-side crash
                from blocking «wake now» until the process restarts. `release()` is idempotent.
                """
                try:
                    return await orchestrator_ref.run_session()
                finally:
                    await guard.release()

            def _reset(task: asyncio.Task[SessionOutcome]) -> None:
                # runs on the event loop after the session task settles
                if not task.cancelled() and task.exception() is not None:
                    node.last_error = str(task.exception())[:500]
                reset_task = asyncio.ensure_future(_record_session_outcome(task))
                node._resets.add(reset_task)
                reset_task.add_done_callback(node._resets.discard)

            node.session_task = asyncio.create_task(_run_locked_session())
            node.session_task.add_done_callback(_reset)
            return OperatorCommandState.COMPLETED, {"node_state": "session_running"}

        if ctype in (OperatorCommandType.STOP_GRACEFULLY, OperatorCommandType.ABORT_SESSION):
            active = await SessionRepository.list_nonterminal(db)
            if not active:
                return OperatorCommandState.REJECTED, {"reason": "no active session"}
            session = active[0]
            if ctype is OperatorCommandType.STOP_GRACEFULLY:
                session.stop_requested_at = _now()
            else:
                session.abort_requested_at = _now()
            await db.flush()
            return OperatorCommandState.COMPLETED, {"session_id": str(session.id)}

        # set_budget / set_access_profile / restore_checkpoint
        return OperatorCommandState.REJECTED, {
            "reason": "not available in M1 (config snapshot / checkpoints land later)"
        }

    async def _set_pause_reason(db: AsyncSession, reason: str) -> None:
        await db.execute(
            text(
                "INSERT INTO wake_scheduler_state (node_id, paused_reason) VALUES (:n, :r) "
                "ON CONFLICT (node_id) DO UPDATE SET paused_reason = :r"
            ),
            {"n": _node_owner, "r": reason},
        )

    async def _record_session_outcome(task: asyncio.Task[SessionOutcome]) -> None:
        # T3.29 (§5.2.1): apply the backoff/pause rules to the finished
        # session and set the resulting node state.
        if task.cancelled():
            final_state = "cancelled"
        elif task.exception() is not None:
            final_state = "failed"
        else:
            final_state = task.result().final_state.value
        async with factory() as db:
            # T3.29 (§5.2.1): the resulting state is written by the scheduler into
            # system_constants.node_state — and read back from there by /api/v1/status and by the
            # next command (T7.59(в)); nothing is mirrored in memory.
            await WakeScheduler(db, node_owner=_node_owner, data_root=_data_root).record_session_result(
                final_state=final_state, now=_now()
            )

    # ── T3.22: message lifecycle (lazy TTL -> expired) ────────────────────

    async def _expire_messages(db: AsyncSession) -> int:
        now = _now()
        result = await db.execute(
            text(
                "UPDATE messages SET state='expired' WHERE state IN ('created','queued','delivered') "
                "AND expires_at IS NOT NULL AND expires_at < :now"
            ),
            {"now": now},
        )
        await db.flush()
        rowcount = getattr(result, "rowcount", 0)
        return int(rowcount or 0)

    @app.get("/api/v1/messages")
    async def list_messages() -> JsonDict:
        async with factory() as db, transaction(db):
            await _expire_messages(db)
            rows = (
                (await db.execute(select(ORMMessage).order_by(ORMMessage.created_at.desc()).limit(200)))
                .scalars()
                .all()
            )
            return {
                "messages": [
                    {
                        "id": str(m.id),
                        "sender": m.sender,
                        "body": m.body,
                        "priority": m.priority,
                        "state": m.state,
                        "expires_at": m.expires_at.isoformat() if m.expires_at else None,
                        "created_at": m.created_at.isoformat(),
                    }
                    for m in rows
                ]
            }

    # ── T7.59: operator question intake (§5.3, §5.3.2, §13.6) ─────────────
    #
    # The read half (the queue view) is open like every other GET. The
    # mutating half belongs to the Command API family: admin token (T3.18) +
    # the host fail-closed gate (T3.24), i.e. the identical 423 body as
    # /api/v1/commands. It does not touch memory directly: it appends a
    # candidate row to the question registry that the FIFO selector serves
    # (§5.3.2).

    @app.get("/api/v1/questions")
    async def list_questions(limit: int = 100) -> JsonDict:
        """The question queue: candidates in FIFO order with their position,
        then already-worked questions, each annotated with the newest session
        that took it (id + state) when there is one."""
        async with factory() as db:
            rows = await question_queue(db, limit=max(1, min(limit, 200)))
            # T7.64 (ADR-0026): человеческие подписи состояния и происхождения
            # добавляются к существующим полям строки очереди, ничего не заменяя.
            return {"questions": [annotate_question(row) for row in rows], "count": len(rows)}

    @app.post("/api/v1/questions", status_code=201)
    async def post_question(body: QuestionIn, request: Request) -> JSONResponse:
        """Accept one operator question (origin 'message', state 'candidate').

        Idempotent by exact text: a repeated formulation returns the existing
        question with 200 and ``replayed=True`` — no duplicate candidate, no
        silent priority mutation. Validation failure is 400 with the intake
        reason; a missing or wrong token is 401; a degraded host closes the
        mutating endpoint with 423 (the same rule as commands).
        """
        _check_admin(request)
        # T3.24 rule, applied to intake: while the host is not fully healthy
        # the web drops to read-only and every mutating endpoint refuses.
        host = host_adapter.read()
        if not host.healthy:
            return JSONResponse(
                status_code=423,
                # T7.64 (ADR-0026): причина отказа и состояние восстановления
                # подписаны поверх существующих ключей; fail-closed поведение прежнее.
                content=annotate_reasons(
                    annotate_host_status(
                        {
                            "rejected": True,
                            "reason": "host_not_healthy",
                            "recovery_state": host.recovery_state,
                            "warnings": host.warnings,
                        }
                    )
                ),
            )
        try:
            async with factory() as db, transaction(db):
                question, created = await put_operator_question(
                    db, raw_text=body.text, raw_priority=body.priority
                )
                position = await queue_position(db, question)
                payload: JsonDict = {
                    "id": str(question.id),
                    "text": question.text,
                    "origin": question.origin,
                    "state": question.state,
                    "priority": question.priority,
                    "created_at": question.created_at.isoformat(),
                    "position": position,
                    "replayed": not created,
                }
        except QuestionIntakeError as exc:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_question", "detail": str(exc)},
            )
        return JSONResponse(
            status_code=201 if created else 200, content=annotate_question(payload)
        )

    # ── T3.19: SSE timeline (committed outbox + host notifications) ───────

    @app.get("/api/v1/timeline/sse")
    async def timeline_sse(
        session_id: uuid.UUID | None = None,
        poll_ms: int = 500,
        max_events: int = 0,
    ) -> StreamingResponse:
        """Server-Sent Events: committed outbox events (+ host
        notifications). Only committed outbox rows are streamed — a host
        record is never presented as a DB audit row before replay.

        ``max_events`` (0 = unlimited) bounds the stream; it is used by
        short-lived consumers and tests so the stream can terminate."""
        limit = 500

        async def gen() -> AsyncIterator[str]:
            last_seq = 0
            sent = 0
            while True:
                try:
                    async with factory() as db:
                        target = session_id
                        if target is None:
                            row = (
                                await db.execute(
                                    select(ORMSession).order_by(ORMSession.created_at.desc()).limit(1)
                                )
                            ).scalar_one_or_none()
                            target = row.id if row is not None else None
                        if target is not None:
                            events = (
                                (
                                    await db.execute(
                                        text(
                                            "SELECT a.sequence, a.type, a.occurred_at, a.public_summary, a.payload "
                                            "FROM audit_events a JOIN outbox_events o ON o.audit_event_id = a.id "
                                            "WHERE a.session_id = :s AND a.sequence > :last ORDER BY a.sequence ASC "
                                            "LIMIT :limit"
                                        ),
                                        {"s": str(target), "last": last_seq, "limit": limit},
                                    )
                                )
                                .mappings()
                                .all()
                            )
                        else:
                            events = []
                except Exception:  # a DB outage must not kill the stream
                    events = []
                for e in events:
                    last_seq = max(last_seq, int(e["sequence"]))
                    sent += 1
                    data = {
                        "sequence": int(e["sequence"]),
                        "type": e["type"],
                        "occurred_at": e["occurred_at"].isoformat() if e["occurred_at"] else None,
                        "public_summary": e["public_summary"],
                        "payload": e["payload"],
                    }
                    yield f"event: timeline\ndata: {json.dumps(data, default=str)}\n\n"
                if not events:
                    yield f"event: ping\ndata: {json.dumps({'t': time.time()})}\n\n"
                if max_events and sent >= max_events:
                    return
                await asyncio.sleep(max(50, poll_ms) / 1000.0)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ── T3.21: session detail ──────────────────────────────────────────────

    @app.get("/api/v1/sessions/{session_id}")
    async def session_detail(session_id: uuid.UUID) -> JsonDict:
        async with factory() as db:
            session = await db.get(ORMSession, session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="session not found")
            events = (
                (
                    await db.execute(
                        select(ORMAuditEvent)
                        .where(ORMAuditEvent.session_id == session_id)
                        .order_by(ORMAuditEvent.sequence.asc())
                        .limit(500)
                    )
                )
                .scalars()
                .all()
            )
            return {
                "id": str(session.id),
                "state": session.state,
                # T7.64 (ADR-0026): человеческое состояние и этап из пяти; индекс
                # None у состояний вне линейной шкалы (остановка, прерывание, неудача).
                "state_label": ui_labels.describe("session_state", session.state)["label"],
                "stage": ui_labels.session_stage(session.state),
                "question_id": str(session.question_id) if session.question_id else None,
                "config_snapshot_id": str(session.config_snapshot_id) if session.config_snapshot_id else None,
                "stop_requested_at": session.stop_requested_at.isoformat() if session.stop_requested_at else None,
                "abort_requested_at": session.abort_requested_at.isoformat() if session.abort_requested_at else None,
                "events": [
                    {
                        "sequence": e.sequence,
                        "type": e.type,
                        "type_label": ui_labels.describe("audit_event_type", e.type)["label"],
                        "occurred_at": e.occurred_at.isoformat(),
                        "public_summary": e.public_summary,
                        "payload": e.payload,
                    }
                    for e in events
                ],
            }

    # ── T7.1: knowledge graph (claims/assessments/dependencies/provenance) ─

    @app.get("/api/v1/knowledge/claims")
    async def knowledge_claims(
        limit: int = 50,
        offset: int = 0,
        state: str | None = None,
    ) -> JsonDict:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        if state is not None and state not in knowledge_queries.HEAD_STATES:
            raise HTTPException(
                status_code=422,
                detail=f"state must be one of {', '.join(knowledge_queries.HEAD_STATES)}",
            )
        async with factory() as db:
            return await knowledge_queries.list_claims(
                db, limit=limit, offset=offset, state=state
            )

    @app.get("/api/v1/knowledge/dependencies")
    async def knowledge_dependencies(
        claim_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> JsonDict:
        async with factory() as db:
            return await knowledge_queries.list_dependencies(
                db, claim_id=claim_id, limit=limit
            )

    @app.get("/api/v1/knowledge/claims/{claim_id}")
    async def knowledge_claim_detail(claim_id: uuid.UUID) -> JsonDict:
        async with factory() as db:
            return await knowledge_queries.claim_detail(db, claim_id)

    @app.get("/api/v1/knowledge/claims/{claim_id}/provenance")
    async def knowledge_claim_provenance(claim_id: uuid.UUID) -> JsonDict:
        async with factory() as db:
            return await knowledge_queries.claim_provenance(db, claim_id)

    # ── T7.1: diagnostics (reconciliation, invalidation, jobs) ────────────

    @app.get("/api/v1/diagnostics")
    async def diagnostics_summary() -> JsonDict:
        async with factory() as db:
            return await diagnostics_queries.summary(db)

    @app.get("/api/v1/diagnostics/reconciliation")
    async def diagnostics_reconciliation() -> JsonDict:
        async with factory() as db:
            return await diagnostics_queries.reconciliation(db)

    @app.get("/api/v1/diagnostics/jobs")
    async def diagnostics_jobs(
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JsonDict:
        if status is not None and status not in (
            "queued", "leased", "retry", "blocked", "completed",
        ):
            raise HTTPException(
                status_code=422,
                detail="status must be queued|leased|retry|blocked|completed",
            )
        async with factory() as db:
            return await diagnostics_queries.jobs(
                db, status=status, limit=limit, offset=offset
            )

    @app.get("/api/v1/diagnostics/barriers")
    async def diagnostics_barriers(
        include_resolved: bool = False,
        limit: int = 100,
    ) -> JsonDict:
        async with factory() as db:
            return await diagnostics_queries.barriers(
                db, include_resolved=include_resolved, limit=limit
            )

    @app.get("/api/v1/metrics")
    async def metrics_report() -> JsonDict:
        """T7.4 (§16, §16.3): the technical/cognitive/security report."""
        async with factory() as db:
            return await metrics_queries.all_metrics(db)

    # ── T7.5: evaluation run (§22.2) ────────────────────────────────────

    @app.get("/api/v1/evaluation")
    async def evaluation_runs(limit: int = 20) -> JsonDict:
        """T7.5 (§22.2): the evaluation runs (newest first)."""
        from packages.evaluation.service import list_evaluation_runs

        async with factory() as db:
            runs = await list_evaluation_runs(db, limit=limit)
            return {
                "runs": [
                    {
                        "id": str(r.id),
                        "label": r.label,
                        "outcome": r.outcome,
                        "started_at": r.started_at.isoformat(),
                        "finished_at": (
                            r.finished_at.isoformat() if r.finished_at else None
                        ),
                        "eligible_sessions": r.eligible_sessions,
                        "completed_sessions": r.completed_sessions,
                        "gates": r.gates,
                        "blind_sample_size": r.blind_sample_size,
                    }
                    for r in runs
                ]
            }

    @app.get("/api/v1/evaluation/{run_id}")
    async def evaluation_run_detail(run_id: uuid.UUID) -> JsonDict:
        """T7.5 (§22.2): one evaluation run (frozen config + gates)."""
        from packages.evaluation.service import get_evaluation_run

        async with factory() as db:
            run = await get_evaluation_run(db, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="not found")
            return {
                "id": str(run.id),
                "label": run.label,
                "config_snapshot_id": str(run.config_snapshot_id),
                "model_fingerprint": run.model_fingerprint,
                "rules_version": run.rules_version,
                "rules_hash": run.rules_hash,
                "thresholds": run.thresholds,
                "started_at": run.started_at.isoformat(),
                "finished_at": (
                    run.finished_at.isoformat() if run.finished_at else None
                ),
                "eligible_sessions": run.eligible_sessions,
                "completed_sessions": run.completed_sessions,
                "gates": run.gates,
                "blind_sample_seed": run.blind_sample_seed,
                "blind_sample_size": run.blind_sample_size,
                "outcome": run.outcome,
            }

    # ── T7.64 (ADR-0026): единый словарь человеческих подписей ─────────────

    @app.get("/api/v1/glossary")
    async def glossary() -> JsonDict:
        """Открытый GET: категория → значение → {label, hint, action}.

        Источник — презентационный слой `apps/web.labels`: будущая страница
        «Справка» (T7.66) читает тексты отсюда, а не дублирует их. Здесь только
        подписи: правила, пороги и оценку держат домен и rules engine.
        """
        categories = ui_labels.glossary()
        return {
            "categories": categories,
            "category_count": len(categories),
            "entry_count": sum(len(entries) for entries in categories.values()),
            "stage_count": ui_labels.STAGE_COUNT,
        }

    # ── T3.20/T3.21/T7.1: HTML pages ─────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def main_page() -> str:
        return _MAIN_HTML

    @app.get("/session/{session_id}", response_class=HTMLResponse)
    async def session_page(session_id: uuid.UUID) -> str:
        return _SESSION_HTML.replace("__SESSION_ID__", str(session_id))

    @app.get("/knowledge", response_class=HTMLResponse)
    async def knowledge_page() -> str:
        return _KNOWLEDGE_HTML

    @app.get("/claim/{claim_id}", response_class=HTMLResponse)
    async def claim_page(claim_id: uuid.UUID) -> str:
        return _CLAIM_HTML.replace("__CLAIM_ID__", str(claim_id))

    @app.get("/diagnostics", response_class=HTMLResponse)
    async def diagnostics_page() -> str:
        return _DIAGNOSTICS_HTML

    @app.get("/metrics", response_class=HTMLResponse)
    async def metrics_page() -> str:
        return _METRICS_HTML

    @app.get("/evaluation", response_class=HTMLResponse)
    async def evaluation_page() -> str:
        return _EVALUATION_HTML

    return app


def build_standalone_app() -> FastAPI:
    """Entry helper: build the app with an orchestrator from env config.

    The app is created first (it owns the engine + session factory), the orchestrator is built on
    THAT factory and attached to `app.state.orchestrator`. A command reads the attachment at
    command time (`_command_orchestrator` in create_app) — this is the path a real stand uses, and
    the order here is exactly why an argument-only lookup used to answer "orchestrator not
    attached" on the stand while tests that passed the orchestrator as an argument stayed green.
    """
    from apps.orchestrator.tool_executors import build_tool_executor
    from packages.llm_gateway.client import LLMMiddleware
    from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile

    settings = DatabaseSettings()
    llm_config = LLMGatewayConfig()
    # app owns the single engine; the orchestrator shares its factory
    app = create_app(db_url=settings.database_url)
    factory = app.state.factory
    gateway = LLMMiddleware(llm_config)
    orchestrator = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias=llm_config.model, backend_name="local"),
        # T7.58 (ADR-0023): the same NOEZEMA_TOOL_EXECUTOR switch as the wake tick
        # and the eval run; the default ("stub") keeps the previous behavior.
        # T7.59(b): the workspace follows NOEZEMA_DATA_ROOT (the env the wake tick uses) instead
        # of a hardcoded production path — same value when the env is unset, but a stand that runs
        # as an ordinary user can actually create it. Sandbox mode ignores this path: its host
        # overlay lives in NOEZEMA_SANDBOX_WORK_ROOT.
        executor=build_tool_executor(resolve_standalone_workspace(data_root_from_env())),
    )
    app.state.orchestrator = orchestrator
    return app


# keep SessionState referenced for API consumers typing session.state
_ = SessionState
