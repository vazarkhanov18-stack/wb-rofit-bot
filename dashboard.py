from __future__ import annotations

import hmac
import logging
import os
import threading
from datetime import date
from typing import Any

from flask import Flask, Response, render_template_string, request
from waitress import serve

from wb_db import (
    database_enabled,
    get_dashboard_report,
    list_dashboard_reports,
    list_dashboard_skus,
)

logger = logging.getLogger("wb-profit-dashboard")
app = Flask(__name__)
_server_started = False
_server_lock = threading.Lock()


DASHBOARD_TEMPLATE = r"""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex,nofollow">
  <title>{{ title }}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg:#0c0d12; --panel:#151720; --text:#f5f7fb; --muted:#9aa3b5;
      --accent:#9d4edd; --accent-2:#e040fb; --good:#3ddc97;
      --bad:#ff6678; --warn:#ffcc66; --line:rgba(255,255,255,.08);
    }
    *{box-sizing:border-box} body{margin:0;font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--text);
      background:radial-gradient(circle at 12% 0%,rgba(157,78,221,.18),transparent 28rem),radial-gradient(circle at 90% 10%,rgba(224,64,251,.10),transparent 30rem),var(--bg);min-height:100vh}
    .wrap{max-width:1460px;margin:0 auto;padding:24px} header{display:flex;justify-content:space-between;gap:20px;align-items:flex-end;margin-bottom:22px}
    h1{margin:0;font-size:clamp(26px,4vw,42px);letter-spacing:-.04em}.subtitle{color:var(--muted);margin-top:7px;font-size:14px}
    .badge{padding:9px 12px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.03);color:var(--muted);font-size:13px;white-space:nowrap}
    .filters{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px} select,button{border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:12px;padding:10px 12px;font-size:14px}
    button{cursor:pointer;background:linear-gradient(135deg,var(--accent),var(--accent-2));border:0;font-weight:700}
    .grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}.card{background:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.015));border:1px solid var(--line);border-radius:18px;padding:17px;min-width:0}
    .label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.value{margin-top:9px;font-size:clamp(19px,2.3vw,29px);font-weight:800;letter-spacing:-.035em;overflow-wrap:anywhere}
    .value.good{color:var(--good)}.value.bad{color:var(--bad)}.value.warn{color:var(--warn)}.section{margin-top:16px}.section-head{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:12px}
    h2{margin:0;font-size:20px}.chart-box{height:360px}.two-col{display:grid;grid-template-columns:1.35fr .65fr;gap:16px}.table-wrap{overflow:auto;border-radius:14px}
    table{width:100%;border-collapse:collapse;min-width:1050px}th,td{padding:11px 10px;border-bottom:1px solid var(--line);text-align:right;font-size:12.5px;white-space:nowrap}
    th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--panel)}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}tr:last-child td{border-bottom:0}
    .profit-pos{color:var(--good);font-weight:700}.profit-neg{color:var(--bad);font-weight:700}.muted{color:var(--muted)}.empty{padding:40px 20px;text-align:center;color:var(--muted)}
    .period-link{color:var(--text);text-decoration:none;font-weight:650}.period-link:hover{color:#d6a7ff}.hint{margin-top:12px;padding:12px 14px;border:1px dashed var(--line);border-radius:12px;color:var(--muted);font-size:12px;line-height:1.5}
    footer{color:var(--muted);font-size:12px;text-align:center;padding:28px 0 10px}
    @media(max-width:1150px){.grid{grid-template-columns:repeat(3,1fr)}.two-col{grid-template-columns:1fr}}
    @media(max-width:650px){.wrap{padding:16px}header{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:repeat(2,1fr)}.card{padding:14px}.chart-box{height:300px}}
  </style>
</head>
<body>
<div class="wrap">
  <header>
    <div><h1>{{ title }}</h1><div class="subtitle">Финансы Wildberries · история себестоимости · внешний P&amp;L</div></div>
    <div class="badge">Последнее сохранение: {{ latest_created }}</div>
  </header>

  <form class="filters" method="get">
    <select name="period_type" aria-label="Тип периода">
      <option value="" {% if not selected_type %}selected{% endif %}>Все сохранённые периоды</option>
      <option value="daily" {% if selected_type == 'daily' %}selected{% endif %}>Дневные и произвольные</option>
      <option value="weekly" {% if selected_type == 'weekly' %}selected{% endif %}>Недельные</option>
      <option value="xlsx" {% if selected_type == 'xlsx' %}selected{% endif %}>Загруженные Excel</option>
    </select>
    <button type="submit">Показать</button>
  </form>

  {% if latest %}
  <div class="grid">
    <div class="card"><div class="label">Доход покупателей</div><div class="value">{{ money(latest.revenue) }}</div></div>
    <div class="card"><div class="label">Расчётная выплата</div><div class="value">{{ money(latest.payout) }}</div></div>
    <div class="card"><div class="label">Прибыль до налога</div><div class="value {{ 'good' if latest.profit_before_tax >= 0 else 'bad' }}">{{ money(latest.profit_before_tax) }}</div></div>
    <div class="card"><div class="label">УСН 6%</div><div class="value">{{ money(latest.tax) }}</div></div>
    <div class="card"><div class="label">Чистая прибыль</div><div class="value {{ 'good' if latest.profit >= 0 else 'bad' }}">{{ money(latest.profit) }}</div></div>
    <div class="card"><div class="label">Себестоимость</div><div class="value">{{ money(latest.cogs) }}</div></div>
    <div class="card"><div class="label">Внешние расходы</div><div class="value">{{ money(latest.external_expenses) }}</div></div>
    <div class="card"><div class="label">Реклама WB</div><div class="value">{{ money(latest.advertising) }}</div></div>
    <div class="card"><div class="label">ДРР / Маржа</div><div class="value {{ margin_class(latest.margin) }}">{{ percent(latest.drr) }} / {{ percent(latest.margin) }}</div></div>
    <div class="card"><div class="label">Продано</div><div class="value">{{ units(latest.units) }} шт.</div></div>
  </div>

  <div class="section card">
    <div class="section-head"><div><h2>Динамика сохранённых отчётов</h2><div class="subtitle">Доход, прибыль до налога и чистая прибыль</div></div></div>
    <div class="chart-box"><canvas id="trendChart"></canvas></div>
  </div>

  <div class="section two-col">
    <div class="card">
      <div class="section-head"><h2>Товары выбранного периода</h2><span class="muted">{{ latest_period }}</span></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Товар</th><th>Артикул</th><th>Шт.</th><th>Доход</th><th>Себес.</th><th>Внешние</th><th>Реклама</th><th>До налога</th><th>УСН</th><th>Чистая</th><th>Маржа</th></tr></thead>
          <tbody>
          {% for row in skus %}
            <tr>
              <td>{{ row.name or row.sku }}</td><td class="muted">{{ row.sku }}</td><td>{{ units(row.units) }}</td>
              <td>{{ money(row.revenue) }}</td><td>{{ money(row.cogs) }}</td><td>{{ money(row.external_expenses) }}</td><td>{{ money(row.advertising) }}</td>
              <td class="{{ 'profit-pos' if row.profit_before_tax >= 0 else 'profit-neg' }}">{{ money(row.profit_before_tax) }}</td><td>{{ money(row.tax) }}</td>
              <td class="{{ 'profit-pos' if row.profit >= 0 else 'profit-neg' }}">{{ money(row.profit) }}</td><td>{{ percent(row.margin) }}</td>
            </tr>
          {% else %}<tr><td colspan="11" class="empty">В этом отчёте нет строк по товарам.</td></tr>{% endfor %}
          </tbody>
        </table>
      </div>
      <div class="hint">Внешние расходы состоят из расходов на единицу из <b>cost_history.xlsx</b> и общих расходов из <b>expenses.xlsx</b>. Общие расходы распределяются по товарам пропорционально доходу.</div>
    </div>

    <div class="card">
      <div class="section-head"><h2>Последние периоды</h2><span class="muted">Нажми на период</span></div>
      <div class="table-wrap">
        <table style="min-width:760px">
          <thead><tr><th>Период</th><th>Тип</th><th>Доход</th><th>До налога</th><th>УСН</th><th>Чистая</th></tr></thead>
          <tbody>
          {% for row in reports_desc %}
            <tr>
              <td><a class="period-link" href="/?report_id={{ row.report_id }}{% if selected_type %}&period_type={{ selected_type }}{% endif %}">{{ period(row.period_start,row.period_end) }}</a></td>
              <td class="muted">{{ type_label(row.period_type) }}</td><td>{{ money(row.revenue) }}</td>
              <td class="{{ 'profit-pos' if row.profit_before_tax >= 0 else 'profit-neg' }}">{{ money(row.profit_before_tax) }}</td><td>{{ money(row.tax) }}</td>
              <td class="{{ 'profit-pos' if row.profit >= 0 else 'profit-neg' }}">{{ money(row.profit) }}</td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </div>
  {% else %}<div class="card empty">В базе пока нет отчётов. Отправь боту <b>/yesterday</b>, <b>/week</b> или <b>/month</b>, затем обнови страницу.</div>{% endif %}
  <footer>WB Profit Dashboard · данные доступны только после авторизации</footer>
</div>

{% if latest %}
<script>
const labels={{ chart_labels|tojson }}, profit={{ chart_profit|tojson }}, preTax={{ chart_pre_tax|tojson }}, revenue={{ chart_revenue|tojson }};
const canvas=document.getElementById('trendChart');
if(window.Chart&&canvas){new Chart(canvas,{data:{labels,datasets:[
  {type:'bar',label:'Чистая прибыль',data:profit,borderWidth:0,borderRadius:6,backgroundColor:'rgba(61,220,151,.70)',yAxisID:'y'},
  {type:'line',label:'Прибыль до налога',data:preTax,borderColor:'#ffcc66',backgroundColor:'rgba(255,204,102,.08)',fill:false,tension:.28,pointRadius:3,yAxisID:'y'},
  {type:'line',label:'Доход покупателей',data:revenue,borderColor:'#c77dff',backgroundColor:'rgba(199,125,255,.14)',fill:true,tension:.28,pointRadius:3,yAxisID:'y1'}
]},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{labels:{color:'#cbd2df'}}},scales:{x:{ticks:{color:'#9aa3b5',maxRotation:40,minRotation:0},grid:{color:'rgba(255,255,255,.05)'}},y:{position:'left',ticks:{color:'#9aa3b5'},grid:{color:'rgba(255,255,255,.05)'}},y1:{position:'right',ticks:{color:'#9aa3b5'},grid:{drawOnChartArea:false}}}}});}
</script>
{% endif %}
</body></html>
"""


def _dashboard_user() -> str:
    return os.getenv("DASHBOARD_USER", "admin").strip() or "admin"


def _dashboard_password() -> str:
    return os.getenv("DASHBOARD_PASSWORD", "").strip()


def _authorized() -> bool:
    auth = request.authorization
    password = _dashboard_password()
    if not password or auth is None:
        return False
    return hmac.compare_digest(auth.username or "", _dashboard_user()) and hmac.compare_digest(auth.password or "", password)


def _auth_required() -> Response:
    return Response("Нужна авторизация для доступа к WB Profit Dashboard.",401,{"WWW-Authenticate":'Basic realm="WB Profit Dashboard", charset="UTF-8"'})


@app.before_request
def protect_dashboard():
    if request.path == "/health": return None
    if not _dashboard_password():
        return Response("DASHBOARD_PASSWORD не задан в Railway Variables.",503,{"Content-Type":"text/plain; charset=utf-8"})
    if not _authorized(): return _auth_required()
    return None


@app.after_request
def add_security_headers(response: Response):
    response.headers["X-Content-Type-Options"]="nosniff"; response.headers["X-Frame-Options"]="DENY"; response.headers["Referrer-Policy"]="no-referrer"; response.headers["Cache-Control"]="no-store"
    return response


@app.get("/health")
def health(): return {"status":"ok","database":database_enabled()}


def _money(value: float) -> str:
    number=float(value); sign="−" if number<0 else ""; text=f"{abs(number):,.2f} ₽".replace(","," ").replace(".00 ₽"," ₽"); return sign+text

def _percent(value: float) -> str: return f"{float(value)*100:.1f}%".replace(".",",")
def _units(value: float) -> str:
    number=float(value); return str(int(number)) if number.is_integer() else f"{number:.1f}".replace(".",",")
def _period(start: date,end: date)->str: return start.strftime("%d.%m.%Y") if start==end else f"{start.strftime('%d.%m.%Y')}–{end.strftime('%d.%m.%Y')}"
def _type_label(value:str)->str: return {"daily":"Дневной","weekly":"Недельный","xlsx":"Excel"}.get(value,value)
def _margin_class(value:float)->str: return "bad" if value<0 else "warn" if value<.10 else "good"


@app.get("/")
def dashboard():
    if not database_enabled():
        return Response("DATABASE_URL не задан. Сначала подключи PostgreSQL к сервису бота.",503,{"Content-Type":"text/plain; charset=utf-8"})
    selected_type=request.args.get("period_type","").strip()
    if selected_type not in {"","daily","weekly","xlsx"}: selected_type=""
    reports_desc=list_dashboard_reports(200,period_type=selected_type or None)
    latest=reports_desc[0] if reports_desc else None
    selected_report_id=request.args.get("report_id","").strip()
    if selected_report_id.isdigit():
        candidate=get_dashboard_report(int(selected_report_id))
        if candidate is not None and (not selected_type or candidate.period_type==selected_type): latest=candidate
    skus=list_dashboard_skus(latest.report_id) if latest else []
    reports_asc=list(reversed(reports_desc))
    context:dict[str,Any]={
        "title":os.getenv("DASHBOARD_TITLE","WB Profit Dashboard").strip() or "WB Profit Dashboard",
        "latest":latest,"skus":skus,"reports_desc":reports_desc,"selected_type":selected_type,
        "chart_labels":[_period(r.period_start,r.period_end) for r in reports_asc],
        "chart_profit":[round(r.profit,2) for r in reports_asc],
        "chart_pre_tax":[round(r.profit_before_tax,2) for r in reports_asc],
        "chart_revenue":[round(r.revenue,2) for r in reports_asc],
        "latest_created":latest.created_at.strftime("%d.%m.%Y %H:%M") if latest else "ещё нет",
        "latest_period":_period(latest.period_start,latest.period_end) if latest else "",
        "money":_money,"percent":_percent,"units":_units,"period":_period,"type_label":_type_label,"margin_class":_margin_class,
    }
    return render_template_string(DASHBOARD_TEMPLATE,**context)


def start_dashboard_server() -> None:
    global _server_started
    with _server_lock:
        if _server_started: return
        _server_started=True
    raw_port=os.getenv("PORT","8080").strip()
    try: port=int(raw_port)
    except ValueError: port=8080
    def _run()->None:
        logger.info("Веб-дашборд запущен на 0.0.0.0:%d",port)
        serve(app,host="0.0.0.0",port=port,threads=4,channel_timeout=30)
    threading.Thread(target=_run,name="wb-dashboard",daemon=True).start()
