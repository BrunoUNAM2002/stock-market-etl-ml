"""
Dashboard de Fundamentales PIT.
Uso (desde la raiz del repo, con pyarrow instalado):
    python build_dashboard.py
Lee la capa golden (data/fundamentals/golden/fundamentales_pit.parquet) y genera
data/dashboard_fundamentales.html. Abrelo con doble clic en el navegador.
Las graficas se dibujan con SVG propio: no necesita internet ni librerias externas
(sin conexion, solo cambia la tipografia por la del sistema).
Opciones:  python build_dashboard.py [archivo.parquet|archivo.csv] [salida.html]
"""
import sys, json
from pathlib import Path
import pandas as pd

GOLDEN = Path("data/fundamentals/golden/fundamentales_pit.parquet")
if len(sys.argv) > 1:
    src = Path(sys.argv[1])
elif GOLDEN.exists():
    src = GOLDEN
else:
    sys.exit("No existe la capa golden. Corre primero: python -m productos.fundamentales_pit.capas todo")
out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/dashboard_fundamentales.html")
df = pd.read_csv(src) if src.suffix == ".csv" else pd.read_parquet(src)
df["quarter_end"] = pd.to_datetime(df["quarter_end"])
df["available_date"] = pd.to_datetime(df["available_date"])
M = {"gm": "fund_gross_margin", "om": "fund_oper_margin", "nm": "fund_net_margin",
     "ry": "fund_revenue_yoy", "ey": "fund_earnings_yoy"}
d = lambda s: s.dt.strftime("%Y-%m-%d")
r = lambda x: None if pd.isna(x) else round(float(x), 4)

df = df.sort_values(["ticker", "quarter_end"])
quarters = sorted(df["quarter_end"].unique())
qlab = [pd.Timestamp(q).strftime("%Y-%m-%d") for q in quarters]
cnt = df.groupby('quarter_end').ticker.nunique()
MIN_T = 5  # no graficar la mediana si hay menos de 5 emisoras en ese trimestre
med = {k: [r(df[df.quarter_end == q][c].median()) if cnt[q] >= MIN_T else None for q in quarters] for k, c in M.items()}

cov = (df.groupby("ticker").agg(n=("quarter_end", "size"), first=("quarter_end", "min"),
       last=("quarter_end", "max")).reset_index().sort_values("n", ascending=False))
coverage = [dict(t=x.ticker, n=int(x.n), first=x.first.strftime("%Y-%m-%d"),
                 last=x.last.strftime("%Y-%m-%d")) for x in cov.itertuples()]

last = df.groupby("ticker").tail(1)
latest = [dict(t=x.ticker, q=x.quarter_end.strftime("%Y-%m-%d"),
               gm=r(x.fund_gross_margin), om=r(x.fund_oper_margin), nm=r(x.fund_net_margin),
               ry=r(x.fund_revenue_yoy), ey=r(x.fund_earnings_yoy)) for x in last.itertuples()]

series = {t: dict(q=d(g.quarter_end).tolist(), **{k: [r(v) for v in g[c]] for k, c in M.items()})
          for t, g in df.groupby("ticker")}

lag = (df.available_date - df.quarter_end).dt.days
today = pd.Timestamp.today().normalize()
future = df[df.available_date > today]
pit = dict(lag_min=int(lag.min()), lag_max=int(lag.max()), lag_mode=int(lag.mode()[0]),
           pct_lag_ok=round(float((lag == lag.mode()[0]).mean() * 100), 1),
           future_rows=int(len(future)),
           future=[f"{x.ticker} {x.quarter_end:%Y-%m-%d} (disp. {x.available_date:%Y-%m-%d})" for x in future.itertuples()][:10])
nulls = {c: round(float(df[c].isna().mean() * 100), 1) for c in M.values()}
kpi = dict(rows=int(len(df)), tickers=int(df.ticker.nunique()), q_min=qlab[0], q_max=qlab[-1],
           dups=int(df.duplicated(["ticker", "quarter_end"]).sum()),
           generated=pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"))
try:  # tickers del catalogo que no llegaron (solo si se corre desde la raiz del repo)
    sys.path.insert(0, ".")
    from src.data import bmv_catalog as bc
    cat = next(v for k, v in vars(bc).items() if k.endswith("FUNDAMENTALS") and isinstance(v, dict))
    kpi["missing"] = sorted(set(cat) - set(df.ticker.unique()))
    kpi["catalog"] = len(cat)
except Exception:
    kpi["missing"], kpi["catalog"] = [], None

DATA = dict(kpi=kpi, quarters=qlab, med=med, coverage=coverage, latest=latest,
            series=series, pit=pit, nulls=nulls)

HTML = r"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fundamentales PIT · Equipo Rojo</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:wght@500;600&display=swap">
<style>
:root{--paper:#f6f4ef;--panel:#fbfaf7;--ink:#16202e;--ink2:#3d4757;--mute:#6b7280;--rule:#dcd7cb;--rule2:#ebe7dd;
 --red:#b8322a;--blue:#2f5d8a;--ochre:#a8761f;--green:#3d7a5a;--plum:#6d4f8c;--redbg:#f6e3df;--okbg:#e4eee7}
@media(prefers-color-scheme:dark){:root{--paper:#14181e;--panel:#191e25;--ink:#ece8df;--ink2:#c4c0b6;--mute:#8d939c;--rule:#2e3540;--rule2:#232931;
 --red:#e0675c;--blue:#79a6d6;--ochre:#d9a648;--green:#77b391;--plum:#a98bc9;--redbg:#3a2220;--okbg:#1f3328}}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0 auto;max-width:1240px;padding:40px 32px 64px;color:var(--ink);background:var(--paper);
 font:15px/1.5 "IBM Plex Sans","Segoe UI",system-ui,sans-serif;font-variant-numeric:tabular-nums}
.num,.mono{font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace}
header{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;flex-wrap:wrap;padding-bottom:20px;border-bottom:2px solid var(--ink)}
.eyebrow{font:500 12px/1 "IBM Plex Mono",monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--red);margin:0 0 10px}
h1{font:600 40px/1.05 "IBM Plex Serif",Georgia,serif;margin:0;letter-spacing:-.01em}
.meta{color:var(--mute);font-size:13px;text-align:right;line-height:1.6}
.meta b{color:var(--ink2);font-weight:500}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);border-bottom:1px solid var(--rule);margin-bottom:8px}
.kpi{padding:22px 20px 20px 0}.kpi+.kpi{padding-left:20px;border-left:1px solid var(--rule)}
.kpi .v{font:500 34px/1 "IBM Plex Mono",monospace;letter-spacing:-.02em}
.kpi .v small{font-size:18px;color:var(--mute)}
.kpi .l{color:var(--mute);font-size:13px;margin-top:8px}
.kpi.warn .v{color:var(--red)}
section{margin-top:44px}
.sh{display:flex;align-items:baseline;gap:14px;margin-bottom:18px}
.sh .n{font:500 13px "IBM Plex Mono",monospace;color:var(--red)}
.sh h2{font:600 22px/1.2 "IBM Plex Serif",Georgia,serif;margin:0}
.sh p{margin:0;color:var(--mute);font-size:14px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:32px}
.panel{border-top:1px solid var(--ink);padding-top:14px}
.panel h3{font-size:14px;font-weight:600;margin:0 0 2px}
.panel .cap{color:var(--mute);font-size:13px;margin:0 0 12px}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:var(--ink2);margin-bottom:6px}
.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
.chart{position:relative}
.chart svg{display:block;width:100%;overflow:visible}
.chart text{fill:var(--mute);font:12px "IBM Plex Mono",monospace}
.chart .lbl{fill:var(--ink2);font:12.5px "IBM Plex Sans",sans-serif}
.tip{position:absolute;pointer-events:none;background:var(--ink);color:var(--paper);font-size:12.5px;line-height:1.5;
 padding:8px 10px;border-radius:4px;white-space:nowrap;opacity:0;transition:opacity .1s;z-index:2}
.tip b{font-weight:600}.tip i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.checks{list-style:none;margin:0;padding:0}
.checks li{display:grid;grid-template-columns:88px 1fr;gap:14px;padding:12px 0;border-bottom:1px solid var(--rule2);align-items:start}
.tag{font:500 11px/1 "IBM Plex Mono",monospace;letter-spacing:.08em;text-transform:uppercase;padding:5px 7px;border-radius:3px;text-align:center}
.tag.ok{background:var(--okbg);color:var(--green)}.tag.bad{background:var(--redbg);color:var(--red)}
.checks small{display:block;color:var(--mute);font-size:12.5px;margin-top:4px;line-height:1.55}
.note{color:var(--mute);font-size:12.5px;margin-top:10px;line-height:1.55}
.toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
select,input{font:inherit;font-size:14px;color:var(--ink);background:var(--panel);border:1px solid var(--rule);border-radius:4px;padding:7px 10px}
select{font-family:"IBM Plex Mono",monospace;font-weight:500}
.seg{display:inline-flex;border:1px solid var(--rule);border-radius:4px;overflow:hidden}
.seg button{font:inherit;font-size:13px;background:var(--panel);color:var(--ink2);border:0;padding:7px 12px;cursor:pointer}
.seg button+button{border-left:1px solid var(--rule)}
.seg button.on{background:var(--ink);color:var(--paper)}
.facts{display:flex;gap:28px;flex-wrap:wrap;margin-left:auto;font-size:13px;color:var(--mute)}
.facts b{display:block;font:500 17px "IBM Plex Mono",monospace;color:var(--ink)}
.tablewrap{max-height:520px;overflow:auto;border-top:1px solid var(--ink)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{padding:9px 12px;text-align:right;border-bottom:1px solid var(--rule2)}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
th{position:sticky;top:0;background:var(--paper);color:var(--mute);font-weight:500;font-size:12px;letter-spacing:.04em;text-transform:uppercase;cursor:pointer;user-select:none;border-bottom:1px solid var(--rule)}
th:hover{color:var(--ink)}th .ar{color:var(--red)}
td{font-family:"IBM Plex Mono",monospace}td:first-child{font-weight:500}
tbody tr:hover{background:var(--panel)}
td.neg{color:var(--red)}td.na{color:var(--rule)}
footer{margin-top:48px;padding-top:14px;border-top:1px solid var(--rule);color:var(--mute);font-size:12.5px;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}
@media(max-width:900px){.grid2{grid-template-columns:1fr}.kpis{grid-template-columns:repeat(2,1fr)}.kpi+.kpi{border-left:0;padding-left:0}.kpi{border-top:1px solid var(--rule)}.facts{margin-left:0}h1{font-size:32px}.meta{text-align:left}body{padding:24px 16px 48px}}
</style></head><body>

<header>
 <div><p class="eyebrow">Equipo Rojo · Producto de datos</p><h1>Fundamentales point-in-time</h1></div>
 <div class="meta" id="meta"></div>
</header>
<div class="kpis" id="kpis"></div>

<section>
 <div class="sh"><span class="n">01</span><h2>Control point-in-time</h2><p>¿Se puede usar este dato sin ver el futuro?</p></div>
 <div class="grid2">
  <div class="panel"><h3>Revisiones de la capa golden</h3><p class="cap">available_date = fin de trimestre + rezago fijo</p><ul class="checks" id="checks"></ul></div>
  <div class="panel"><h3>Datos faltantes por columna</h3><p class="cap">Porcentaje de filas sin valor</p><div class="chart" id="nul"></div>
   <p class="note">Los vacíos en YoY son esperables: los primeros cuatro trimestres de cada emisora no tienen año previo. Un YoY de ±500% es el tope del recorte [−5, 5], no un valor real.</p></div>
 </div>
</section>

<section>
 <div class="sh"><span class="n">02</span><h2>Tendencia del mercado</h2><p>Mediana entre emisoras, solo trimestres con 5 o más emisoras</p></div>
 <div class="grid2">
  <div class="panel"><h3>Márgenes</h3><div class="legend" id="lg-med"></div><div class="chart" id="med"></div></div>
  <div class="panel"><h3>Crecimiento contra el mismo trimestre del año anterior</h3><div class="legend" id="lg-yoy"></div><div class="chart" id="yoy"></div></div>
 </div>
</section>

<section>
 <div class="sh"><span class="n">03</span><h2>Explorar una emisora</h2></div>
 <div class="toolbar">
  <select id="sel" aria-label="Emisora"></select>
  <div class="seg" id="mode"><button data-m="m" class="on">Márgenes</button><button data-m="y">Crecimiento YoY</button></div>
  <div class="facts" id="facts"></div>
 </div>
 <div class="panel"><div class="legend" id="lg-one"></div><div class="chart" id="one"></div></div>
</section>

<section>
 <div class="sh"><span class="n">04</span><h2>Cobertura y último trimestre</h2></div>
 <div class="grid2">
  <div class="panel"><h3>Trimestres por emisora</h3><p class="cap" id="cov-cap"></p><div class="chart" id="cov"></div></div>
  <div class="panel"><h3>Margen neto del último trimestre disponible</h3><p class="cap">En rojo, emisoras con pérdida</p><div class="chart" id="lat"></div></div>
 </div>
</section>

<section>
 <div class="sh"><span class="n">05</span><h2>Detalle por emisora</h2><p>Último trimestre de cada una · clic en un encabezado para ordenar</p></div>
 <div class="toolbar"><input id="q" placeholder="Buscar emisora…" aria-label="Buscar emisora"></div>
 <div class="tablewrap"><table><thead id="th"></thead><tbody id="tb"></tbody></table></div>
</section>

<footer><span>Fuente: API DataBursátil · capa golden de Fundamentales PIT</span><span id="foot"></span></footer>

<script>
const D=__DATA__;const $=i=>document.getElementById(i);
const cv=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const COL={gm:'--blue',om:'--ochre',nm:'--red',ry:'--blue',ey:'--plum'};
const NAME={gm:'Margen bruto',om:'Margen operativo',nm:'Margen neto',ry:'Ingresos',ey:'Utilidad neta'};
const pc=(v,d=1)=>v==null?'—':(v*100).toFixed(d).replace('-','−')+'%';
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const SV='http://www.w3.org/2000/svg';
function el(t,a,p){const e=document.createElementNS(SV,t);for(const k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e}
function ticks(lo,hi,n=5){if(lo===hi){lo-=1;hi+=1}const raw=(hi-lo)/n,p=Math.pow(10,Math.floor(Math.log10(raw))),s=[1,2,2.5,5,10].map(x=>x*p).find(x=>x>=raw);
 const a=Math.floor(lo/s)*s,b=Math.ceil(hi/s)*s,o=[];for(let v=a;v<=b+s/2;v+=s)o.push(+v.toFixed(10));return o}
function legend(id,keys){$(id).innerHTML=keys.map(k=>`<span><i style="background:${cv(COL[k])}"></i>${NAME[k]}</span>`).join('')}

/* Grafica de lineas: labels = fechas ISO; series = [{k,data(fracciones)}] */
function lineChart(id,labels,series,h=280){
 const box=$(id);box.innerHTML='';const W=box.clientWidth||600,L=52,R=12,T=10,B=28,iw=W-L-R,ih=h-T-B;
 const vals=series.flatMap(s=>s.data).filter(v=>v!=null);if(!vals.length){box.innerHTML='<p class="note">Sin datos.</p>';return}
 const tk=ticks(Math.min(0,...vals),Math.max(...vals)),y0=tk[0],y1=tk[tk.length-1];
 const X=i=>L+(labels.length<2?iw/2:i*iw/(labels.length-1)),Y=v=>T+ih-(v-y0)/(y1-y0)*ih;
 const svg=el('svg',{viewBox:`0 0 ${W} ${h}`,height:h},box);
 tk.forEach(v=>{el('line',{x1:L,x2:W-R,y1:Y(v),y2:Y(v),stroke:cv(v===0?'--mute':'--rule2'),'stroke-width':v===0?1:1},svg);
  const t=el('text',{x:L-8,y:Y(v)+4,'text-anchor':'end'},svg);t.textContent=(Math.round(v*1000)/10)+'%'});
 let lastY='';labels.forEach((q,i)=>{const yr=q.slice(0,4);if(yr!==lastY&&(q.slice(5,7)==='03'||i===0)){lastY=yr;
  if(+yr%2===0||labels.length<24){const t=el('text',{x:X(i),y:h-8,'text-anchor':'middle'},svg);t.textContent=yr}}});
 series.forEach(s=>{let d='';s.data.forEach((v,i)=>{if(v==null){return}d+=(i&&s.data[i-1]!=null?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)});
  el('path',{d,fill:'none',stroke:cv(COL[s.k]),'stroke-width':2,'stroke-linejoin':'round','stroke-linecap':'round'},svg);
  s.data.forEach((v,i)=>{if(v!=null&&(i===0||s.data[i-1]==null)&&(i===s.data.length-1||s.data[i+1]==null))el('circle',{cx:X(i),cy:Y(v),r:2.5,fill:cv(COL[s.k])},svg)})});
 const guide=el('line',{y1:T,y2:T+ih,stroke:cv('--mute'),'stroke-dasharray':'3 3',opacity:0},svg);
 const dots=series.map(s=>el('circle',{r:4,fill:cv('--paper'),stroke:cv(COL[s.k]),'stroke-width':2,opacity:0},svg));
 const tip=document.createElement('div');tip.className='tip';box.appendChild(tip);
 svg.addEventListener('mousemove',e=>{const r=svg.getBoundingClientRect(),x=(e.clientX-r.left)*W/r.width;
  const i=Math.max(0,Math.min(labels.length-1,Math.round((x-L)/iw*(labels.length-1))));
  guide.setAttribute('x1',X(i));guide.setAttribute('x2',X(i));guide.setAttribute('opacity',1);
  series.forEach((s,j)=>{const v=s.data[i];dots[j].setAttribute('opacity',v==null?0:1);if(v!=null){dots[j].setAttribute('cx',X(i));dots[j].setAttribute('cy',Y(v))}});
  tip.innerHTML=`<b>${labels[i]}</b><br>`+series.map(s=>`<i style="background:${cv(COL[s.k])}"></i>${NAME[s.k]}: ${pc(s.data[i])}`).join('<br>');
  tip.style.opacity=1;const px=X(i)*r.width/W;tip.style.left=(px>r.width*.6?px-tip.offsetWidth-14:px+14)+'px';tip.style.top='8px'});
 svg.addEventListener('mouseleave',()=>{tip.style.opacity=0;guide.setAttribute('opacity',0);dots.forEach(d=>d.setAttribute('opacity',0))});
}

/* Barras horizontales: items = [{label,v,color,txt}] */
function hbar(id,items,fmt,row=22){
 const box=$(id);box.innerHTML='';const W=box.clientWidth||600,LW=96,VW=64,h=items.length*row+8,iw=W-LW-VW;
 const vs=items.map(x=>x.v),lo=Math.min(0,...vs),hi=Math.max(0,...vs),Xs=v=>LW+(v-lo)/((hi-lo)||1)*iw;
 const svg=el('svg',{viewBox:`0 0 ${W} ${h}`,height:h},box);
 items.forEach((it,i)=>{const y=i*row+4,x0=Xs(0),x1=Xs(it.v);
  const t=el('text',{x:LW-10,y:y+row/2+4,'text-anchor':'end',class:'lbl'},svg);t.textContent=it.label;
  el('rect',{x:Math.min(x0,x1),y:y+4,width:Math.max(1,Math.abs(x1-x0)),height:row-8,fill:it.color,rx:1},svg);
  const vt=el('text',{x:it.v<0?x0+6:x1+6,y:y+row/2+4},svg);vt.textContent=it.txt??fmt(it.v)});
 if(lo<0)el('line',{x1:Xs(0),x2:Xs(0),y1:0,y2:h,stroke:cv('--mute')},svg);
}

/* ---------- Encabezado y KPIs ---------- */
const K=D.kpi,P=D.pit;
$('meta').innerHTML=`Trimestres <b>${K.q_min}</b> a <b>${K.q_max}</b><br>Generado ${K.generated}`;
$('foot').textContent=`Generado ${K.generated}`;
const kp=[[K.rows.toLocaleString('es-MX'),'filas en golden'],[K.tickers+(K.catalog?`<small> / ${K.catalog}</small>`:''),'emisoras con datos'],
 [D.quarters.length,'trimestres distintos'],[K.dups,'duplicados (ticker, trimestre)',K.dups>0],[P.future_rows,'filas con available_date futura',P.future_rows>0]];
$('kpis').innerHTML=kp.map(([v,l,w])=>`<div class="kpi${w?' warn':''}"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');

/* ---------- 01 Control PIT ---------- */
const lagOk=P.lag_min===P.lag_max;
const checks=[
 [lagOk,lagOk?`Rezago idéntico en todas las filas: <b>${P.lag_mode} días</b>`:`El rezago varía entre ${P.lag_min} y ${P.lag_max} días`,lagOk?'':`${P.pct_lag_ok}% de las filas usa ${P.lag_mode} días.`],
 [K.dups===0,K.dups===0?'Sin duplicados por emisora y trimestre':`${K.dups} duplicados por emisora y trimestre`,''],
 [P.future_rows===0,P.future_rows===0?'Ninguna fila tiene available_date futura':`${P.future_rows} filas todavía no están disponibles`,P.future_rows?'El modelo no debe usarlas aún. '+P.future.slice(0,4).map(esc).join(' · ')+(P.future_rows>4?` y ${P.future_rows-4} más`:''):''],
 [!K.missing.length,K.missing.length?`${K.missing.length} emisoras del catálogo sin datos en la API`:'Todas las emisoras del catálogo tienen datos',K.missing.map(esc).join(', ')]];
$('checks').innerHTML=checks.map(([ok,t,s])=>`<li><span class="tag ${ok?'ok':'bad'}">${ok?'OK':'Revisar'}</span><div>${t}${s?`<small>${s}</small>`:''}</div></li>`).join('');

function drawAll(){
 hbar('nul',Object.entries(D.nulls).map(([c,v])=>({label:c.replace('fund_',''),v,color:cv(v>20?'--ochre':'--blue')})),v=>v.toFixed(1)+'%');
 lineChart('med',D.quarters,['gm','om','nm'].map(k=>({k,data:D.med[k]})));
 lineChart('yoy',D.quarters,['ry','ey'].map(k=>({k,data:D.med[k]})));
 const ns=D.coverage.map(x=>x.n).sort((a,b)=>a-b),NT=ns[Math.floor(ns.length/2)];
 $('cov-cap').textContent=`Lo típico son ${NT} trimestres; en ocre, las que tienen menos`;
 hbar('cov',D.coverage.map(x=>({label:x.t,v:x.n,color:cv(x.n<NT?'--ochre':'--blue'),txt:String(x.n)})),String);
 const Lt=D.latest.filter(x=>x.nm!=null).sort((a,b)=>b.nm-a.nm);
 hbar('lat',Lt.map(x=>({label:x.t,v:x.nm,color:cv(x.nm<0?'--red':'--green')})),v=>pc(v));
 showOne();
}
legend('lg-med',['gm','om','nm']);legend('lg-yoy',['ry','ey']);

/* ---------- 03 Explorar ---------- */
let mode='m';const tk=Object.keys(D.series).sort();
$('sel').innerHTML=tk.map(t=>`<option>${esc(t)}</option>`).join('');
function showOne(){const t=$('sel').value,s=D.series[t],keys=mode==='m'?['gm','om','nm']:['ry','ey'];legend('lg-one',keys);
 lineChart('one',s.q,keys.map(k=>({k,data:s[k]})),340);
 const i=s.q.length-1,lt=D.latest.find(x=>x.t===t)||{};
 $('facts').innerHTML=`<span><b>${s.q[i]}</b>último trimestre</span><span><b>${pc(lt.nm)}</b>margen neto</span><span><b>${pc(lt.ry)}</b>ingresos YoY</span><span><b>${s.q.length}</b>trimestres</span>`}
$('sel').onchange=showOne;
document.querySelectorAll('#mode button').forEach(b=>b.onclick=()=>{mode=b.dataset.m;document.querySelectorAll('#mode button').forEach(x=>x.classList.toggle('on',x===b));showOne()});

/* ---------- 05 Tabla ---------- */
const cols=[['t','Emisora'],['q','Trimestre'],['gm','M. bruto'],['om','M. operativo'],['nm','M. neto'],['ry','Ingresos YoY'],['ey','Utilidad YoY']];
let sk='nm',dir=-1;
function draw(){const f=$('q').value.trim().toUpperCase();
 const rows=D.latest.filter(x=>x.t.toUpperCase().includes(f)).sort((a,b)=>{const A=a[sk],B=b[sk];if(A==null)return 1;if(B==null)return -1;return (A>B?1:A<B?-1:0)*dir});
 $('th').innerHTML='<tr>'+cols.map(([k,n])=>`<th data-k="${k}">${n}${k===sk?` <span class="ar">${dir<0?'↓':'↑'}</span>`:''}</th>`).join('')+'</tr>';
 $('tb').innerHTML=rows.map(x=>`<tr><td>${esc(x.t)}</td><td>${x.q}</td>${['gm','om','nm','ry','ey'].map(k=>`<td class="${x[k]==null?'na':x[k]<0?'neg':''}">${pc(x[k])}</td>`).join('')}</tr>`).join('');
 document.querySelectorAll('#th th').forEach(h=>h.onclick=()=>{const k=h.dataset.k;if(k===sk)dir=-dir;else{sk=k;dir=-1}draw()})}
$('q').oninput=draw;draw();

drawAll();let rz;addEventListener('resize',()=>{clearTimeout(rz);rz=setTimeout(drawAll,150)});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{legend('lg-med',['gm','om','nm']);legend('lg-yoy',['ry','ey']);drawAll()});
</script></body></html>"""
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(HTML.replace("__DATA__", json.dumps(DATA, ensure_ascii=False)), encoding="utf-8")
print(f"Dashboard generado: {out.resolve()}  ({kpi['rows']} filas, {kpi['tickers']} emisoras)")
