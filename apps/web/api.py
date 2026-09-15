"""Minimal Query/Command API (T1.17, §13).

M1 slice:
  - GET  /api/v1/status     — node state, config head, active session, counts;
  - GET  /api/v1/timeline   — audit events for a session (sequence order);
  - POST /api/v1/messages   — inbox message (created/queued);
  - POST /api/v1/commands   — closed operator commands with idempotency key.

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
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

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

from apps.orchestrator.orchestrator import Orchestrator, SessionOutcome
from apps.orchestrator.scheduler import WakeScheduler, data_root_from_env, node_owner_from_env
from apps.web import diagnostics as diagnostics_queries
from apps.web import knowledge as knowledge_queries
from apps.web import metrics as metrics_queries
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

NODE_STATE_KEY = "node_state"
NODE_STATES = ("idle", "paused", "session_running")

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
tick(); setInterval(tick, 3000);
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


class CommandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: OperatorCommandType
    idempotency_key: str = Field(min_length=1, max_length=200)
    arguments: JsonDict = Field(default_factory=dict)
    actor_id: str = Field(default="operator", min_length=1, max_length=100)
    reason: str | None = Field(default=None, max_length=500)


class _Node:
    """M1 node state: in-memory, persisted to system_constants across
    restarts (the host transition protocol lands in M2)."""

    def __init__(self) -> None:
        self.state: str = "idle"
        self.session_task: asyncio.Task[SessionOutcome] | None = None
        self.last_error: str | None = None
        self._resets: set[asyncio.Task[None]] = set()


def _now() -> datetime:
    return datetime.now(UTC)


async def _load_node_state(db: AsyncSession) -> str:
    stmt = select(ORMSystemConstant).where(ORMSystemConstant.key == NODE_STATE_KEY)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return "idle"
    return row.value if row.value in NODE_STATES else "idle"


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
        async with factory() as db:
            node.state = await _load_node_state(db)
        yield
        if node.session_task is not None and not node.session_task.done():
            node.session_task.cancel()
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
            counts = {
                "questions": (await db.execute(select(func.count(ORMQuestion.id)))).scalar_one(),
                "sessions": (await db.execute(select(func.count(ORMSession.id)))).scalar_one(),
                "messages": (await db.execute(select(func.count(ORMMessage.id)))).scalar_one(),
            }
            out = {
                "node_state": node.state,
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
        out["host"] = host.to_dict()
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
                content={
                    "rejected": True,
                    "reason": "host_not_healthy",
                    "recovery_state": host.recovery_state,
                    "warnings": host.warnings,
                },
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
                    "state": command.state,
                    "replayed": True,
                    "result": command.result,
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
                "state": command.state,
                "replayed": False,
                "result": result,
            }

    async def _apply_command(
        db: AsyncSession, command: ORMOperatorCommand, body: CommandIn
    ) -> tuple[OperatorCommandState, JsonDict]:
        ctype = OperatorCommandType(command.type)

        if ctype is OperatorCommandType.PAUSE:
            if node.state in ("paused",):
                return OperatorCommandState.COMPLETED, {"node_state": "paused", "noop": True}
            if node.state == "session_running":
                return OperatorCommandState.REJECTED, {"reason": "session is running; stop_gracefully instead"}
            node.state = "paused"
            await _save_node_state(db, node.state)
            # T3.29: explain the pause for the wake admission audit
            await _set_pause_reason(db, "operator")
            return OperatorCommandState.COMPLETED, {"node_state": "paused"}

        if ctype is OperatorCommandType.RESUME:
            if node.state != "paused":
                return OperatorCommandState.REJECTED, {"reason": "node is not paused"}
            node.state = "idle"
            await _save_node_state(db, node.state)
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
            if node.state == "paused":
                return OperatorCommandState.REJECTED, {"reason": "node is paused"}
            if node.state == "session_running":
                return OperatorCommandState.REJECTED, {"reason": "a session is already running"}
            if orchestrator is None:
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
            node.state = "session_running"
            await _save_node_state(db, node.state)

            def _reset(task: asyncio.Task[SessionOutcome]) -> None:
                # runs on the event loop after the session task settles
                if not task.cancelled() and task.exception() is not None:
                    node.last_error = str(task.exception())[:500]
                reset_task = asyncio.ensure_future(_record_session_outcome(task))
                node._resets.add(reset_task)
                reset_task.add_done_callback(node._resets.discard)

            node.session_task = asyncio.create_task(orchestrator.run_session())
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
            new_state = await WakeScheduler(db, node_owner=_node_owner, data_root=_data_root).record_session_result(
                final_state=final_state, now=_now()
            )
        node.state = new_state

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
                "question_id": str(session.question_id) if session.question_id else None,
                "config_snapshot_id": str(session.config_snapshot_id) if session.config_snapshot_id else None,
                "stop_requested_at": session.stop_requested_at.isoformat() if session.stop_requested_at else None,
                "abort_requested_at": session.abort_requested_at.isoformat() if session.abort_requested_at else None,
                "events": [
                    {
                        "sequence": e.sequence,
                        "type": e.type,
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
    """Entry helper: build the app with an orchestrator from env config."""
    from pathlib import Path

    from apps.orchestrator.executor import StubToolExecutor
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
        executor=StubToolExecutor(Path("/var/lib/noezema/workspace")),
    )
    app.state.orchestrator = orchestrator
    return app


# keep SessionState referenced for API consumers typing session.state
_ = SessionState
