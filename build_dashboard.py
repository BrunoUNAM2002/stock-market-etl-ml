"""
Dashboard de Fundamentales PIT.
Uso (desde la raiz del repo, con pyarrow instalado):
    python build_dashboard.py
Lee data/fundamentals.parquet y genera data/dashboard_fundamentales.html
(abrelo con doble clic en el navegador; necesita internet para cargar Chart.js).
Opciones:  python build_dashboard.py [archivo.parquet|archivo.csv] [salida.html]
"""
import sys, json
from pathlib import Path
import pandas as pd

src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/fundamentals.parquet")
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
med = {k: [r(df[df.quarter_end == q][c].median()) for q in quarters] for k, c in M.items()}

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
<title>Dashboard - Fundamentales PIT</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#f4f7f9;--card:#fff;--tx:#1b2a38;--mu:#5f7080;--bd:#d9e2e8;--a:#2a7f8e;--b:#1f3a5f;--w:#b3412f}
@media(prefers-color-scheme:dark){:root{--bg:#12191f;--card:#1b252d;--tx:#e6edf2;--mu:#93a4b2;--bd:#2b3945;--a:#4fb3c4;--b:#7aa7d9;--w:#e07a68}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.45 system-ui,Segoe UI,sans-serif;padding:20px;max-width:1200px;margin:auto}
h1{margin:0;font-size:24px;color:var(--b)}.sub{color:var(--mu);margin:2px 0 16px}
.g{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-bottom:12px}
.c{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px}
.k{font-size:26px;font-weight:700;color:var(--a)}.l{color:var(--mu);font-size:12px}
.row{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));margin-bottom:12px}
h2{font-size:15px;margin:0 0 8px;color:var(--b)}.box{position:relative;height:280px}
table{width:100%;border-collapse:collapse;font-size:12.5px}th,td{padding:5px 7px;border-bottom:1px solid var(--bd);text-align:right}
th:first-child,td:first-child{text-align:left}th{color:var(--mu);cursor:pointer;position:sticky;top:0;background:var(--card)}
.sc{max-height:340px;overflow:auto}.ok{color:var(--a);font-weight:600}.bad{color:var(--w);font-weight:600}
select{padding:5px;border-radius:6px;border:1px solid var(--bd);background:var(--card);color:var(--tx)}
.note{font-size:12px;color:var(--mu);margin-top:6px}
</style></head><body>
<h1>Fundamentales PIT</h1><div class="sub" id="sub"></div>
<div class="g" id="kpis"></div>
<div class="row">
 <div class="c"><h2>Calidad PIT (available_date)</h2><div id="pit"></div></div>
 <div class="c"><h2>Datos faltantes por columna (%)</h2><div class="box"><canvas id="nul"></canvas></div>
 <div class="note">Los NaN en los YoY son normales: los primeros 4 trimestres no tienen año previo.</div></div>
</div>
<div class="row">
 <div class="c"><h2>Margenes: mediana de todas las emisoras</h2><div class="box"><canvas id="med"></canvas></div></div>
 <div class="c"><h2>Crecimiento YoY: mediana</h2><div class="box"><canvas id="yoy"></canvas></div></div>
</div>
<div class="row">
 <div class="c"><h2>Cobertura: trimestres por emisora</h2><div class="box"><canvas id="cov"></canvas></div></div>
 <div class="c"><h2>Margen neto, ultimo trimestre</h2><div class="box"><canvas id="lat"></canvas></div></div>
</div>
<div class="c" style="margin-bottom:12px"><h2>Explorar una emisora &nbsp;<select id="sel"></select></h2>
 <div class="box" style="height:320px"><canvas id="one"></canvas></div></div>
<div class="c"><h2>Detalle por emisora (ultimo trimestre; clic en encabezado para ordenar)</h2><div class="sc"><table id="tb"></table></div></div>
<script>
const D=__DATA__;const $=i=>document.getElementById(i);
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const C=[css('--a'),css('--b'),'#e0a030',css('--w'),'#8a6fb3'];
Chart.defaults.color=css('--mu');Chart.defaults.borderColor=css('--bd');
const pc=v=>v==null?'-':(v*100).toFixed(1)+'%';
const K=D.kpi,P=D.pit;
$('sub').textContent=`Trimestres ${K.q_min} a ${K.q_max} · generado ${K.generated}`;
const kp=[[K.rows,'filas'],[K.tickers+(K.catalog?' / '+K.catalog:''),'emisoras con datos'],[D.quarters.length,'trimestres distintos'],[K.dups,'duplicados (ticker, trimestre)'],[P.future_rows,'filas con available_date futura']];
$('kpis').innerHTML=kp.map(([v,l])=>`<div class="c"><div class="k">${v}</div><div class="l">${l}</div></div>`).join('');
$('pit').innerHTML=`<p>Rezago mas comun: <b>${P.lag_mode} dias</b> (${P.pct_lag_ok}% de las filas). Min ${P.lag_min}, max ${P.lag_max}.</p>
<p class="${P.lag_min==P.lag_max?'ok':'bad'}">${P.lag_min==P.lag_max?'OK: el rezago es identico en todas las filas.':'Atencion: el rezago varia.'}</p>
<p>${P.future_rows?`<span class="bad">${P.future_rows} fila(s) con available_date posterior a hoy</span>: el modelo no deberia usarlas todavia.<br><small>${P.future.join('<br>')}</small>`:'<span class="ok">Ninguna fila tiene available_date en el futuro.</span>'}</p>
<p>${K.missing.length?`<span class="bad">Sin datos (${K.missing.length}):</span> ${K.missing.join(', ')}`:'Todas las emisoras del catalogo tienen datos.'}</p>`;
new Chart($('nul'),{type:'bar',data:{labels:Object.keys(D.nulls).map(s=>s.replace('fund_','')),datasets:[{data:Object.values(D.nulls),backgroundColor:C[0]}]},options:{indexAxis:'y',maintainAspectRatio:false,plugins:{legend:{display:false}}}});
const ln=(id,keys,names)=>new Chart($(id),{type:'line',data:{labels:D.quarters,datasets:keys.map((k,i)=>({label:names[i],data:D.med[k].map(v=>v==null?null:v*100),borderColor:C[i],backgroundColor:C[i],pointRadius:0,borderWidth:2,tension:.2}))},options:{maintainAspectRatio:false,interaction:{mode:'index',intersect:false},scales:{y:{ticks:{callback:v=>v+'%'}},x:{ticks:{maxTicksLimit:8}}}}});
ln('med',['gm','om','nm'],['Bruto','Operativo','Neto']);ln('yoy',['ry','ey'],['Ingresos YoY','Utilidad neta YoY']);
new Chart($('cov'),{type:'bar',data:{labels:D.coverage.map(x=>x.t),datasets:[{data:D.coverage.map(x=>x.n),backgroundColor:D.coverage.map(x=>x.n<Math.max(...D.coverage.map(y=>y.n))-1?C[3]:C[0])}]},options:{maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{autoSkip:false,maxRotation:90,font:{size:9}}}}}});
const L=D.latest.filter(x=>x.nm!=null).sort((a,b)=>b.nm-a.nm);
new Chart($('lat'),{type:'bar',data:{labels:L.map(x=>x.t),datasets:[{data:L.map(x=>x.nm*100),backgroundColor:L.map(x=>x.nm<0?C[3]:C[1])}]},options:{maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{autoSkip:false,maxRotation:90,font:{size:9}}},y:{ticks:{callback:v=>v+'%'}}}}});
const names=['Margen bruto','Margen operativo','Margen neto','Ingresos YoY','Utilidad YoY'],ks=['gm','om','nm','ry','ey'];
const tk=Object.keys(D.series).sort();$('sel').innerHTML=tk.map(t=>`<option>${t}</option>`).join('');
const one=new Chart($('one'),{type:'line',data:{labels:[],datasets:ks.map((k,i)=>({label:names[i],data:[],borderColor:C[i],backgroundColor:C[i],pointRadius:1,borderWidth:2,hidden:i>2,tension:.2}))},options:{maintainAspectRatio:false,interaction:{mode:'index',intersect:false},scales:{y:{ticks:{callback:v=>v+'%'}},x:{ticks:{maxTicksLimit:10}}}}});
function show(){const s=D.series[$('sel').value];one.data.labels=s.q;ks.forEach((k,i)=>one.data.datasets[i].data=s[k].map(v=>v==null?null:v*100));one.update()}
$('sel').onchange=show;show();
let rows=D.latest.slice(),dir=-1;const cols=[['t','Emisora'],['q','Trimestre'],['gm','M. bruto'],['om','M. oper.'],['nm','M. neto'],['ry','Ing. YoY'],['ey','Util. YoY']];
function draw(){$('tb').innerHTML='<tr>'+cols.map(c=>`<th data-k="${c[0]}">${c[1]}</th>`).join('')+'</tr>'+rows.map(x=>`<tr><td>${x.t}</td><td>${x.q}</td>${['gm','om','nm','ry','ey'].map(k=>`<td>${pc(x[k])}</td>`).join('')}</tr>`).join('');
document.querySelectorAll('th').forEach(h=>h.onclick=()=>{const k=h.dataset.k;dir=-dir;rows.sort((a,b)=>((a[k]??-9e9)>(b[k]??-9e9)?1:-1)*dir);draw()})}
draw();
</script></body></html>"""
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(HTML.replace("__DATA__", json.dumps(DATA, ensure_ascii=False)), encoding="utf-8")
print(f"Dashboard generado: {out.resolve()}  ({kpi['rows']} filas, {kpi['tickers']} emisoras)")
