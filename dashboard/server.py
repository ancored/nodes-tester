"""Веб-сервер дашборда (stdlib http.server). Только чтение, слушает в LAN."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .data import collect

_PAGE = r"""<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>nodes-tester</title>
<style>
:root{color-scheme:dark light;
  --bg:#0f131c;--panel:#171d29;--panel2:#1c2330;--line:#28313f;
  --text:#e7eaf0;--muted:#7c8797;--accent:#4dabf7;
  --good:#51cf9a;--bad:#ff6b6b;--num:#c4ccd8;--active:#123a24;
  --hover:rgba(77,171,247,.10);--shadow:0 1px 2px rgba(0,0,0,.4),0 6px 20px rgba(0,0,0,.14)}
*{box-sizing:border-box}
body{margin:0;font:13.5px/1.5 system-ui,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
  background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased}
header{display:flex;align-items:baseline;gap:12px;padding:16px 22px;
  background:var(--panel);border-bottom:1px solid var(--line)}
header h1{font-size:15px;font-weight:700;margin:0}
header h1::before{content:'◆';color:var(--accent);margin-right:8px;font-size:11px}
small{color:var(--muted);font-weight:400;font-size:12px}
#root{max-width:1240px;margin:0 auto;padding:8px 16px 56px}
section{margin:24px 0 0}
h2{font-size:11px;font-weight:700;margin:0 0 9px 2px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.08em}
h2 small{text-transform:none;letter-spacing:0}
.wrap{overflow-x:auto;background:var(--panel);border:1px solid var(--line);
  border-radius:12px;box-shadow:var(--shadow)}
table{border-collapse:collapse;width:100%;min-width:520px;font-variant-numeric:tabular-nums}
th,td{padding:9px 14px;text-align:right;white-space:nowrap}
th{position:sticky;top:0;z-index:1;background:var(--panel2);color:var(--muted);font-weight:600;
  font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--line)}
td{border-top:1px solid var(--line)}
tbody tr:first-child td{border-top:0}
tbody tr:hover{background:var(--hover)}
td.l,th.l{text-align:left}
table tbody tr.active,table tbody tr.active:hover{background:var(--active)}
.num{color:var(--num)}.good{color:var(--good)}.bad{color:var(--bad)}.mut{color:var(--muted)}
.tag{max-width:340px;overflow:hidden;text-overflow:ellipsis;display:inline-block;vertical-align:bottom}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}
.tabs button{font:inherit;font-size:12px;cursor:pointer;padding:5px 13px;border:1px solid var(--line);
  background:var(--panel);color:var(--muted);border-radius:999px;transition:.12s}
.tabs button:hover{color:var(--text);border-color:var(--muted)}
.tabs button.on{background:var(--accent);color:#08121f;border-color:var(--accent);font-weight:600}
.pager{display:flex;align-items:center;justify-content:flex-end;gap:12px;
  padding:9px 14px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
.pager button{font:inherit;cursor:pointer;width:28px;height:28px;line-height:1;
  border:1px solid var(--line);background:var(--panel2);color:var(--text);border-radius:7px}
.pager button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
.pager button:disabled{opacity:.35;cursor:default}
@media (prefers-color-scheme:light){:root{
  --bg:#f5f7fb;--panel:#fff;--panel2:#f4f6fa;--line:#e6e9ef;--text:#232b38;
  --muted:#6b7684;--accent:#206bc4;--num:#39424f;--active:#e3f5ea;
  --hover:rgba(32,107,196,.07);--shadow:0 1px 2px rgba(0,0,0,.06),0 6px 18px rgba(0,0,0,.05)}
  .tabs button.on{color:#fff}}
</style></head><body>
<header><h1>nodes-tester</h1><small id=ts>загрузка…</small></header>
<div id=root></div>
<script>
const INTERVAL=__INTERVAL__;
const B=n=>{n=+n||0;const u=['B','K','M','G','T'];let i=0;while(n>=1024&&i<4){n/=1024;i++}return n.toFixed(i?1:0)+u[i]}
const T=e=>e?new Date(e*1000).toLocaleTimeString('ru-RU',{hour12:false}):'';
const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function tbl(rows,cols){
  if(!rows.length)return '<div class=mut style="margin:0 16px">нет данных</div>';
  let h='<div class=wrap><table><thead><tr>'+cols.map(c=>`<th class="${c.l?'l':''}">${c.t}</th>`).join('')+'</tr></thead><tbody>';
  for(const r of rows){
    const act=(+r.active===1);
    h+='<tr'+(act?' class=active':'')+'>'+cols.map(c=>{
      let v=r[c.k]; v=c.f?c.f(v,r):esc(v);
      return `<td class="${c.l?'l':''}">${v}</td>`;
    }).join('')+'</tr>';
  }
  return h+'</tbody></table></div>';
}
const tag=v=>`<span class=tag title="${esc(v)}">${esc(v)}</span>`;
// Единая таблица «Трафик» с переключателем измерения; состояние переживает авто-refresh.
const TRAF={
  providers:{d:'traffic_providers',label:'провайдеры',cols:[{k:'provider',t:'провайдер',l:1},{k:'up',t:'исх',f:B},{k:'down',t:'вх',f:B},{k:'total',t:'всего',f:B}]},
  countries:{d:'traffic_countries',label:'страны',cols:[{k:'cc',t:'cc',l:1},{k:'up',t:'исх',f:B},{k:'down',t:'вх',f:B},{k:'total',t:'всего',f:B}]},
  protocols:{d:'traffic_protocols',label:'протоколы',cols:[{k:'protocol',t:'протокол',l:1},{k:'up',t:'исх',f:B},{k:'down',t:'вх',f:B},{k:'total',t:'всего',f:B}]},
  nodes:{d:'traffic_nodes',label:'ноды (топ-10)',cols:[{k:'node',t:'нода',l:1,f:tag},{k:'up',t:'исх',f:B},{k:'down',t:'вх',f:B},{k:'total',t:'всего',f:B}]},
};
let _trafTab='providers', _d={};
function trafficSection(){
  return '<div class=tabs id=traftabs>'+Object.keys(TRAF).map(k=>
    `<button data-k="${k}" onclick="setTrafTab('${k}')">${TRAF[k].label}</button>`).join('')
    +'</div><div id=trafbody></div>';
}
function renderTraf(){
  const t=TRAF[_trafTab]||TRAF.providers, el=document.getElementById('trafbody');
  if(!el)return;
  el.innerHTML=tbl(_d[t.d]||[], t.cols);
  document.querySelectorAll('#traftabs button').forEach(b=>b.classList.toggle('on',b.dataset.k===_trafTab));
}
function setTrafTab(k){ _trafTab=k; renderTraf(); }
// Обобщённая таблица с переключателем по региону (для истории/рейтинга/качества).
const RTBL={};  // id -> {dataKey, cols, region}
function regionTabbed(id,dataKey,cols){
  RTBL[id]={dataKey,cols,region:(RTBL[id]?RTBL[id].region:'все')};
  return `<div class=tabs id="rt_${id}"></div><div id="rb_${id}"></div>`;
}
function renderRTBL(id){
  const c=RTBL[id]; if(!c)return;
  const rows=_d[c.dataKey]||[];
  const regs=['все',...[...new Set(rows.map(r=>r.region).filter(Boolean))].sort()];
  if(!regs.includes(c.region)) c.region='все';
  const rt=document.getElementById('rt_'+id), rb=document.getElementById('rb_'+id);
  if(rt) rt.innerHTML=regs.map(rg=>`<button class="${rg===c.region?'on':''}" onclick="setRTBL('${id}','${rg}')">${rg}</button>`).join('');
  if(rb) rb.innerHTML=pagedBody('rt:'+id, c.region==='все'?rows:rows.filter(r=>r.region===c.region), c.cols, ()=>renderRTBL(id));
}
function setRTBL(id,rg){ if(RTBL[id]){RTBL[id].region=rg; if(PG['rt:'+id])PG['rt:'+id].page=0; renderRTBL(id);} }
// --- Пагинация (состояние переживает авто-refresh) ---
const PG={}, HDR={_select:'переключ.',heavy_download:'dl50↓'};
function paginate(id,n){let per=PG[id]?PG[id].per:15,pg=PG[id]?PG[id].page:0;const pages=Math.max(1,Math.ceil(n/per));if(pg>=pages)pg=pages-1;if(pg<0)pg=0;return {pg,per,pages};}
function pager(id,pg,pages){return `<div class=pager><button onclick="pgGo('${id}',-1)" ${pg===0?'disabled':''}>‹</button><span>${pg+1} / ${pages}</span><button onclick="pgGo('${id}',1)" ${pg>=pages-1?'disabled':''}>›</button></div>`;}
function pagedBody(id,rows,cols,render){const {pg,per,pages}=paginate(id,rows.length);PG[id]={page:pg,per,render};return tbl(rows.slice(pg*per,(pg+1)*per),cols)+(pages>1?pager(id,pg,pages):'');}
function pgGo(id,delta){const p=PG[id];if(!p)return;p.page+=delta;p.render();}
// Топ назначений — плоская таблица с пагинацией.
const ENDP_COLS=[{k:'provider',t:'провайдер',l:1},{k:'source_ip',t:'источник',l:1},{k:'dest_host',t:'назначение',l:1},{k:'network',t:'net',l:1},{k:'up',t:'исх',f:B},{k:'down',t:'вх',f:B},{k:'flows',t:'flows'}];
function renderEndpoints(){const el=document.getElementById('endpbody');if(el)el.innerHTML=pagedBody('endp',_d.endpoints||[],ENDP_COLS,renderEndpoints);}
// Результаты тестов — пивот по тестам, с пагинацией.
function resultsTable(tests,rows){
  let h='<div class=wrap><table><thead><tr><th class=l>провайдер</th><th class=l>протокол</th><th class=l>cc</th><th class=l>crc</th><th class=l title="дата/номер прогона">прогон</th>'
    +tests.map(t=>`<th title="${esc(t)}">${esc(HDR[t]||t)}</th>`).join('')+'</tr></thead><tbody>';
  for(const r of rows){
    h+='<tr><td class=l>'+esc(r.provider)+'</td><td class=l>'+esc(r.protocol)+'</td><td class=l>'+esc(r.country)+'</td><td class="l mut">'+esc(r.crc)+'</td><td class="l num">'+esc(r.pass_label)+'</td>'
      +tests.map(t=>{const c=r.cells[t];return c?`<td class="${c.ok?'good':'bad'}" title="${esc(c.title||'')}">${esc(c.v)}</td>`:'<td class=mut>–</td>';}).join('')+'</tr>';
  }
  return h+'</tbody></table></div>';
}
function renderResults(){
  const el=document.getElementById('resbody'); if(!el)return;
  const X=_d.results||{tests:[],rows:[]};
  if(!X.rows.length){el.innerHTML='<div class=mut style="padding:10px 14px">нет данных (первый прогон ещё идёт?)</div>';return;}
  const {pg,per,pages}=paginate('res',X.rows.length); PG.res={page:pg,per,render:renderResults};
  el.innerHTML=resultsTable(X.tests,X.rows.slice(pg*per,(pg+1)*per))+(pages>1?pager('res',pg,pages):'');
}
async function load(){
  let d; try{ d=await (await fetch('api/data',{cache:'no-store'})).json(); }
  catch(e){ document.getElementById('ts').textContent='ошибка загрузки'; return; }
  document.getElementById('ts').textContent='обновлено '+d.generated;
  const score=v=>{const n=+v; const c=n>=70?'good':n<=20?'bad':'num'; return `<b class=${c}>${(+v).toFixed(1)}</b>`};
  const act=v=>+v===1?'<b class=good>●</b>':'<span class=mut>·</span>';
  const c01=v=>v===''||v==null?'<span class=mut>–</span>':(+v).toFixed(2);
  const pct=v=>{const n=+v||0; return `<b class=${n>50?'bad':'num'}>${n}%</b>`};
  const crcf=v=>'<span class=mut>'+esc(v)+'</span>';
  const reasonf=(v,r)=>{
    if(r.stuck) return '<b class=bad title="EMERGENCY без замены — активная заблокирована, здоровых кандидатов нет">⚠ EMERGENCY</b>';
    if(r.emergency) return '<b class=bad title="аварийное переключение (активная заблокирована)">EMERGENCY</b>';
    return '<span class=mut>'+esc(v||'')+'</span>';
  };
  const ratingCols=[
    {k:'provider',t:'провайдер',l:1},{k:'protocol',t:'протокол',l:1},{k:'country',t:'cc',l:1},{k:'id',t:'crc',l:1,f:crcf},
    {k:'score',t:'score',f:score},{k:'active',t:'act',f:act},
    {k:'reliability',t:'rel',f:c01},{k:'consistency',t:'cons',f:c01},{k:'throttle',t:'thr',f:c01},
    {k:'jitter',t:'jit',f:c01},{k:'latency',t:'lat',f:c01},{k:'throughput',t:'dl',f:c01},
    {k:'samples',t:'n'}];
  const histCols=[{k:'provider',t:'провайдер',l:1},{k:'protocol',t:'протокол',l:1},{k:'cc',t:'cc',l:1},{k:'crc',t:'crc',l:1,f:crcf},
    {k:'ts',t:'переключено',f:T},{k:'reason',t:'причина',l:1,f:reasonf},{k:'active',t:'активная',f:act}];
  const qualCols=[{k:'provider',t:'провайдер',l:1},{k:'region',t:'регион',l:1},
    {k:'total',t:'всего нод'},{k:'dead_pct',t:'доля мёртвых',f:pct},{k:'avg',t:'ср. рейтинг живых',f:score}];
  const sec=(t,html)=>'<section><h2>'+t+'</h2>'+html+'</section>';
  _d=d;
  document.getElementById('root').innerHTML=
    sec('История переключений',regionTabbed('hist','history',histCols))+
    sec('Рейтинг нод',regionTabbed('rating','rating',ratingCols))+
    sec('Результаты тестов','<div id=resbody></div>')+
    sec('Качество провайдеров',regionTabbed('qual','provider_quality',qualCols))+
    sec('Трафик',trafficSection())+
    sec('Топ назначений','<div id=endpbody></div>');
  renderTraf();
  renderRTBL('hist'); renderRTBL('rating'); renderRTBL('qual');
  renderResults(); renderEndpoints();
}
load(); setInterval(load, INTERVAL*1000);
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/data"):
            self._send(200, "application/json",
                       json.dumps(collect(self.server.cfg), ensure_ascii=False).encode("utf-8"))
        elif self.path in ("/", "/index.html"):
            page = _PAGE.replace("__INTERVAL__", str(self.server.interval))
            self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass   # без шумного access-лога


def serve(cfg, host: str, port: int, interval: int) -> None:
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.cfg = cfg
    httpd.interval = interval
    print(f"Дашборд: http://{host}:{port}/  (обновление {interval}s, Ctrl+C для остановки)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        httpd.server_close()
