from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

from flask import Flask, Response, redirect, render_template_string, request, url_for
from waitress import serve

from wb_db import (
    database_enabled,
    get_dashboard_report,
    get_product_summary,
    list_dashboard_reports,
    list_dashboard_skus,
    list_product_periods,
    list_product_summaries,
)
from wb_management import (
    delete_cost_profile,
    delete_expense,
    get_cost_profile,
    get_expense,
    list_cost_profiles,
    list_expenses,
    management_counts,
    management_store_ready,
    upsert_cost_profile,
    upsert_expense,
)

logger = logging.getLogger("wb-profit-dashboard")
app = Flask(__name__)
_server_started = False
_server_lock = threading.Lock()


BASE_STYLE = r"""
<style>
:root{--bg:#0c0d12;--panel:#151720;--panel2:#1b1e29;--text:#f5f7fb;--muted:#9aa3b5;--accent:#9d4edd;--accent2:#e040fb;--good:#3ddc97;--bad:#ff6678;--warn:#ffcc66;--line:rgba(255,255,255,.09)}
*{box-sizing:border-box}body{margin:0;font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--text);background:radial-gradient(circle at 12% 0%,rgba(157,78,221,.18),transparent 28rem),radial-gradient(circle at 90% 10%,rgba(224,64,251,.10),transparent 30rem),var(--bg);min-height:100vh}
.wrap{max-width:1460px;margin:0 auto;padding:24px}header{display:flex;justify-content:space-between;gap:20px;align-items:flex-end;margin-bottom:18px}h1{margin:0;font-size:clamp(26px,4vw,42px);letter-spacing:-.04em}.subtitle{color:var(--muted);margin-top:7px;font-size:14px}.badge{padding:9px 12px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.03);color:var(--muted);font-size:13px;white-space:nowrap}
.nav{display:flex;gap:9px;flex-wrap:wrap;margin:0 0 18px}.nav a{color:var(--text);text-decoration:none;padding:10px 13px;border:1px solid var(--line);border-radius:12px;background:rgba(255,255,255,.025);font-size:14px;font-weight:650}.nav a.active,.nav a:hover{background:linear-gradient(135deg,rgba(157,78,221,.42),rgba(224,64,251,.25));border-color:rgba(199,125,255,.45)}
.filters,.form-grid{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}select,input,textarea,button{border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:12px;padding:10px 12px;font-size:14px}textarea{min-height:82px;resize:vertical}input::placeholder,textarea::placeholder{color:#747d90}button,.button{cursor:pointer;background:linear-gradient(135deg,var(--accent),var(--accent2));border:0;font-weight:750;color:white;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}.button.secondary,button.secondary{background:rgba(255,255,255,.06);border:1px solid var(--line)}button.danger{background:rgba(255,102,120,.15);color:#ff9aa7;border:1px solid rgba(255,102,120,.3)}
.grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}.card{background:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.015));border:1px solid var(--line);border-radius:18px;padding:17px;min-width:0}.label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.value{margin-top:9px;font-size:clamp(19px,2.3vw,29px);font-weight:800;letter-spacing:-.035em;overflow-wrap:anywhere}.value.good{color:var(--good)}.value.bad{color:var(--bad)}.value.warn{color:var(--warn)}.section{margin-top:16px}.section-head{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:12px}h2{margin:0;font-size:20px}h3{margin:0 0 12px;font-size:17px}.chart-box{height:360px}.two-col{display:grid;grid-template-columns:1.35fr .65fr;gap:16px}.admin-cols{display:grid;grid-template-columns:minmax(310px,.72fr) minmax(0,1.28fr);gap:16px}.table-wrap{overflow:auto;border-radius:14px}
table{width:100%;border-collapse:collapse;min-width:1050px}th,td{padding:11px 10px;border-bottom:1px solid var(--line);text-align:right;font-size:12.5px;white-space:nowrap}th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--panel)}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}tr:last-child td{border-bottom:0}.profit-pos{color:var(--good);font-weight:700}.profit-neg{color:var(--bad);font-weight:700}.muted{color:var(--muted)}.empty{padding:40px 20px;text-align:center;color:var(--muted)}.period-link,.text-link{color:var(--text);text-decoration:none;font-weight:650}.period-link:hover,.text-link:hover{color:#d6a7ff}.hint{margin-top:12px;padding:12px 14px;border:1px dashed var(--line);border-radius:12px;color:var(--muted);font-size:12px;line-height:1.5}.notice{margin-bottom:14px;padding:12px 14px;border-radius:12px;border:1px solid var(--line);font-size:13px}.notice.ok{background:rgba(61,220,151,.10);border-color:rgba(61,220,151,.28);color:#9af0c9}.notice.error{background:rgba(255,102,120,.10);border-color:rgba(255,102,120,.28);color:#ffadb7}.field{display:flex;flex-direction:column;gap:6px;margin-bottom:10px}.field label{color:var(--muted);font-size:12px}.field input,.field textarea,.field select{width:100%}.form-actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:12px}.inline-actions{display:flex;gap:7px;justify-content:flex-end}.inline-actions form{margin:0}.small{font-size:11px;padding:7px 9px;border-radius:9px}.summary{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:15px}.summary span{padding:8px 11px;border-radius:999px;background:rgba(255,255,255,.04);border:1px solid var(--line);color:var(--muted);font-size:12px}.product-title{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.sku-chip{display:inline-flex;padding:7px 10px;border-radius:999px;border:1px solid var(--line);background:rgba(255,255,255,.04);color:var(--muted);font-size:12px}.rank-good{color:var(--good);font-weight:750}.rank-bad{color:var(--bad);font-weight:750}.filters label{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:12px}.filters label input,.filters label select{min-width:145px}footer{color:var(--muted);font-size:12px;text-align:center;padding:28px 0 10px}
@media(max-width:1150px){.grid{grid-template-columns:repeat(3,1fr)}.two-col,.admin-cols{grid-template-columns:1fr}}@media(max-width:650px){.wrap{padding:16px}header{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:repeat(2,1fr)}.card{padding:14px}.chart-box{height:300px}.form-grid>*{width:100%}}
</style>
"""


DASHBOARD_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{{ title }}</title><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>{{ title }}</h1><div class="subtitle">Финансы Wildberries · история себестоимости · внешний P&amp;L</div></div><div class="badge">Последнее сохранение: {{ latest_created }}</div></header>
<nav class="nav"><a class="active" href="/">Дашборд</a><a href="/products">Товары</a><a href="/unit-economics">Юнит-экономика</a><a href="/admin">Себестоимость и расходы</a></nav>
<form class="filters" method="get"><select name="period_type" aria-label="Тип периода"><option value="" {% if not selected_type %}selected{% endif %}>Все сохранённые периоды</option><option value="daily" {% if selected_type == 'daily' %}selected{% endif %}>Дневные и произвольные</option><option value="weekly" {% if selected_type == 'weekly' %}selected{% endif %}>Недельные</option><option value="xlsx" {% if selected_type == 'xlsx' %}selected{% endif %}>Загруженные Excel</option></select><button type="submit">Показать</button></form>
{% if latest %}<div class="grid">
<div class="card"><div class="label">Доход покупателей</div><div class="value">{{ money(latest.revenue) }}</div></div><div class="card"><div class="label">Расчётная выплата</div><div class="value">{{ money(latest.payout) }}</div></div><div class="card"><div class="label">Прибыль до налога</div><div class="value {{ 'good' if latest.profit_before_tax >= 0 else 'bad' }}">{{ money(latest.profit_before_tax) }}</div></div><div class="card"><div class="label">УСН 6%</div><div class="value">{{ money(latest.tax) }}</div></div><div class="card"><div class="label">Чистая прибыль</div><div class="value {{ 'good' if latest.profit >= 0 else 'bad' }}">{{ money(latest.profit) }}</div></div><div class="card"><div class="label">Себестоимость</div><div class="value">{{ money(latest.cogs) }}</div></div><div class="card"><div class="label">Внешние расходы</div><div class="value">{{ money(latest.external_expenses) }}</div></div><div class="card"><div class="label">Реклама WB</div><div class="value">{{ money(latest.advertising) }}</div></div><div class="card"><div class="label">ДРР / Маржа</div><div class="value {{ margin_class(latest.margin) }}">{{ percent(latest.drr) }} / {{ percent(latest.margin) }}</div></div><div class="card"><div class="label">Продано</div><div class="value">{{ units(latest.units) }} шт.</div></div></div>
<div class="section card"><div class="section-head"><div><h2>Динамика сохранённых отчётов</h2><div class="subtitle">Доход, прибыль до налога и чистая прибыль</div></div></div><div class="chart-box"><canvas id="trendChart"></canvas></div></div>
<div class="section two-col"><div class="card"><div class="section-head"><h2>Товары выбранного периода</h2><span class="muted">{{ latest_period }}</span></div><div class="table-wrap"><table><thead><tr><th>Товар</th><th>Артикул</th><th>Шт.</th><th>Доход</th><th>Себес.</th><th>Внешние</th><th>Реклама</th><th>До налога</th><th>УСН</th><th>Чистая</th><th>Маржа</th></tr></thead><tbody>{% for row in skus %}<tr><td><a class="text-link" href="{{ url_for('product_detail', sku=row.sku, period_type=latest.period_type) }}">{{ row.name or row.sku }}</a></td><td class="muted">{{ row.sku }}</td><td>{{ units(row.units) }}</td><td>{{ money(row.revenue) }}</td><td>{{ money(row.cogs) }}</td><td>{{ money(row.external_expenses) }}</td><td>{{ money(row.advertising) }}</td><td class="{{ 'profit-pos' if row.profit_before_tax >= 0 else 'profit-neg' }}">{{ money(row.profit_before_tax) }}</td><td>{{ money(row.tax) }}</td><td class="{{ 'profit-pos' if row.profit >= 0 else 'profit-neg' }}">{{ money(row.profit) }}</td><td>{{ percent(row.margin) }}</td></tr>{% else %}<tr><td colspan="11" class="empty">В этом отчёте нет строк по товарам.</td></tr>{% endfor %}</tbody></table></div><div class="hint">Себестоимость и внешние расходы теперь редактируются прямо в разделе <b>«Себестоимость и расходы»</b>. После изменения прошлые отчёты пересчитываются командой <b>/backfill ДД.ММ.ГГГГ</b>.</div></div>
<div class="card"><div class="section-head"><h2>Последние периоды</h2><span class="muted">Нажми на период</span></div><div class="table-wrap"><table style="min-width:760px"><thead><tr><th>Период</th><th>Тип</th><th>Доход</th><th>До налога</th><th>УСН</th><th>Чистая</th></tr></thead><tbody>{% for row in reports_desc %}<tr><td><a class="period-link" href="/?report_id={{ row.report_id }}{% if selected_type %}&period_type={{ selected_type }}{% endif %}">{{ period(row.period_start,row.period_end) }}</a></td><td class="muted">{{ type_label(row.period_type) }}</td><td>{{ money(row.revenue) }}</td><td class="{{ 'profit-pos' if row.profit_before_tax >= 0 else 'profit-neg' }}">{{ money(row.profit_before_tax) }}</td><td>{{ money(row.tax) }}</td><td class="{{ 'profit-pos' if row.profit >= 0 else 'profit-neg' }}">{{ money(row.profit) }}</td></tr>{% endfor %}</tbody></table></div></div></div>
{% else %}<div class="card empty">В базе пока нет отчётов. Отправь боту <b>/yesterday</b>, <b>/week</b> или <b>/month</b>, затем обнови страницу.</div>{% endif %}<footer>WB Profit Dashboard · данные доступны только после авторизации</footer></div>
{% if latest %}<script>const labels={{ chart_labels|tojson }},profit={{ chart_profit|tojson }},preTax={{ chart_pre_tax|tojson }},revenue={{ chart_revenue|tojson }};const canvas=document.getElementById('trendChart');if(window.Chart&&canvas){new Chart(canvas,{data:{labels,datasets:[{type:'bar',label:'Чистая прибыль',data:profit,borderWidth:0,borderRadius:6,backgroundColor:'rgba(61,220,151,.70)',yAxisID:'y'},{type:'line',label:'Прибыль до налога',data:preTax,borderColor:'#ffcc66',backgroundColor:'rgba(255,204,102,.08)',fill:false,tension:.28,pointRadius:3,yAxisID:'y'},{type:'line',label:'Доход покупателей',data:revenue,borderColor:'#c77dff',backgroundColor:'rgba(199,125,255,.14)',fill:true,tension:.28,pointRadius:3,yAxisID:'y1'}]},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{labels:{color:'#cbd2df'}}},scales:{x:{ticks:{color:'#9aa3b5',maxRotation:40,minRotation:0},grid:{color:'rgba(255,255,255,.05)'}},y:{position:'left',ticks:{color:'#9aa3b5'},grid:{color:'rgba(255,255,255,.05)'}},y1:{position:'right',ticks:{color:'#9aa3b5'},grid:{drawOnChartArea:false}}}}});}</script>{% endif %}</body></html>
"""



PRODUCTS_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Товары · {{ title }}</title>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>Аналитика по товарам</h1><div class="subtitle">Прибыль, маржа и ДРР каждого SKU за выбранные сохранённые периоды</div></div><div class="badge">Найдено товаров: {{ totals.products }}</div></header>
<nav class="nav"><a href="/">Дашборд</a><a class="active" href="/products">Товары</a><a href="/unit-economics">Юнит-экономика</a><a href="/admin">Себестоимость и расходы</a></nav>
<form class="filters" method="get"><label>Периоды<select name="period_type"><option value="weekly" {% if selected_type == 'weekly' %}selected{% endif %}>Недельные</option><option value="daily" {% if selected_type == 'daily' %}selected{% endif %}>Дневные и произвольные</option><option value="xlsx" {% if selected_type == 'xlsx' %}selected{% endif %}>Excel</option></select></label><label>С даты<input type="date" name="date_from" value="{{ date_from_value }}"></label><label>По дату<input type="date" name="date_to" value="{{ date_to_value }}"></label><label>Поиск<input name="q" value="{{ query }}" placeholder="Название, артикул или nmID"></label><button type="submit">Показать</button>{% if query or date_from_value or date_to_value %}<a class="button secondary" href="/products?period_type={{ selected_type }}">Сбросить</a>{% endif %}</form>
<div class="hint">Чтобы суммы не задваивались, здесь одновременно используется только один тип периодов. По умолчанию выбраны закрытые недельные отчёты.</div>
{% if rows %}<div class="grid section"><div class="card"><div class="label">Товаров</div><div class="value">{{ totals.products }}</div></div><div class="card"><div class="label">Продано</div><div class="value">{{ units(totals.units) }} шт.</div></div><div class="card"><div class="label">Доход покупателей</div><div class="value">{{ money(totals.revenue) }}</div></div><div class="card"><div class="label">Прибыль до налога</div><div class="value {{ 'good' if totals.profit_before_tax >= 0 else 'bad' }}">{{ money(totals.profit_before_tax) }}</div></div><div class="card"><div class="label">Чистая прибыль</div><div class="value {{ 'good' if totals.profit >= 0 else 'bad' }}">{{ money(totals.profit) }}</div></div><div class="card"><div class="label">Маржа</div><div class="value {{ margin_class(totals.margin) }}">{{ percent(totals.margin) }}</div></div><div class="card"><div class="label">Реклама WB</div><div class="value">{{ money(totals.advertising) }}</div></div><div class="card"><div class="label">ДРР</div><div class="value">{{ percent(totals.drr) }}</div></div><div class="card"><div class="label">Себестоимость</div><div class="value">{{ money(totals.cogs) }}</div></div><div class="card"><div class="label">Внешние расходы</div><div class="value">{{ money(totals.external_expenses) }}</div></div></div>
<div class="section card"><div class="section-head"><div><h2>Экономика товаров</h2><div class="subtitle">Нажми на товар, чтобы открыть его график и историю по периодам</div></div><span class="muted">{{ type_label(selected_type) }}</span></div><div class="table-wrap"><table style="min-width:1430px"><thead><tr><th>Товар</th><th>Артикул</th><th>Периодов</th><th>Шт.</th><th>Доход</th><th>Выплата</th><th>Себес.</th><th>Внешние</th><th>Реклама</th><th>До налога</th><th>УСН</th><th>Чистая</th><th>Прибыль / шт.</th><th>Маржа</th><th>ДРР</th><th>Последний период</th></tr></thead><tbody>{% for row in rows %}<tr><td><a class="text-link" href="{{ url_for('product_detail', sku=row.sku, period_type=selected_type, date_from=date_from_value, date_to=date_to_value) }}">{{ row.name or row.sku }}</a></td><td class="muted">{{ row.sku }}</td><td>{{ row.periods }}</td><td>{{ units(row.units) }}</td><td>{{ money(row.revenue) }}</td><td>{{ money(row.payout) }}</td><td>{{ money(row.cogs) }}</td><td>{{ money(row.external_expenses) }}</td><td>{{ money(row.advertising) }}</td><td class="{{ 'profit-pos' if row.profit_before_tax >= 0 else 'profit-neg' }}">{{ money(row.profit_before_tax) }}</td><td>{{ money(row.tax) }}</td><td class="{{ 'profit-pos' if row.profit >= 0 else 'profit-neg' }}">{{ money(row.profit) }}</td><td>{{ money(row.profit / row.units if row.units else 0) }}</td><td>{{ percent(row.margin) }}</td><td>{{ percent(row.drr) }}</td><td class="muted">{{ date_display(row.last_period) }}</td></tr>{% endfor %}</tbody></table></div></div>{% else %}<div class="card empty section">По выбранным фильтрам товары не найдены. Сначала сохрани отчёты через /week или /backfill.</div>{% endif %}
<footer>WB Profit Dashboard · аналитика строится по сохранённым данным PostgreSQL</footer></div></body></html>
"""


PRODUCT_DETAIL_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{{ summary.name }} · {{ title }}</title><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><div class="product-title"><h1>{{ summary.name or summary.sku }}</h1><span class="sku-chip">{{ summary.sku }}</span>{% if summary.nm_id %}<span class="sku-chip">nmID {{ summary.nm_id }}</span>{% endif %}</div><div class="subtitle">Юнит-экономика товара по сохранённым периодам</div></div><div class="badge">{{ period(summary.first_period, summary.last_period) }}</div></header>
<nav class="nav"><a href="/">Дашборд</a><a class="active" href="/products?period_type={{ selected_type }}">Товары</a><a href="/admin">Себестоимость и расходы</a></nav>
<form class="filters" method="get"><input type="hidden" name="sku" value="{{ summary.sku }}"><label>Периоды<select name="period_type"><option value="weekly" {% if selected_type == 'weekly' %}selected{% endif %}>Недельные</option><option value="daily" {% if selected_type == 'daily' %}selected{% endif %}>Дневные и произвольные</option><option value="xlsx" {% if selected_type == 'xlsx' %}selected{% endif %}>Excel</option></select></label><label>С даты<input type="date" name="date_from" value="{{ date_from_value }}"></label><label>По дату<input type="date" name="date_to" value="{{ date_to_value }}"></label><button type="submit">Показать</button><a class="button secondary" href="/products?period_type={{ selected_type }}">К списку товаров</a></form>
<div class="grid"><div class="card"><div class="label">Продано</div><div class="value">{{ units(summary.units) }} шт.</div></div><div class="card"><div class="label">Доход покупателей</div><div class="value">{{ money(summary.revenue) }}</div></div><div class="card"><div class="label">Расчётная выплата</div><div class="value">{{ money(summary.payout) }}</div></div><div class="card"><div class="label">Прибыль до налога</div><div class="value {{ 'good' if summary.profit_before_tax >= 0 else 'bad' }}">{{ money(summary.profit_before_tax) }}</div></div><div class="card"><div class="label">Чистая прибыль</div><div class="value {{ 'good' if summary.profit >= 0 else 'bad' }}">{{ money(summary.profit) }}</div></div><div class="card"><div class="label">Прибыль / шт.</div><div class="value {{ 'good' if profit_per_unit >= 0 else 'bad' }}">{{ money(profit_per_unit) }}</div></div><div class="card"><div class="label">Себестоимость</div><div class="value">{{ money(summary.cogs) }}</div></div><div class="card"><div class="label">Внешние расходы</div><div class="value">{{ money(summary.external_expenses) }}</div></div><div class="card"><div class="label">Реклама / ДРР</div><div class="value">{{ money(summary.advertising) }} / {{ percent(summary.drr) }}</div></div><div class="card"><div class="label">Маржа</div><div class="value {{ margin_class(summary.margin) }}">{{ percent(summary.margin) }}</div></div></div>
<div class="section card"><div class="section-head"><div><h2>Динамика товара</h2><div class="subtitle">Доход, прибыль до налога и чистая прибыль</div></div><span class="muted">{{ summary.periods }} периодов</span></div><div class="chart-box"><canvas id="productChart"></canvas></div></div>
<div class="section two-col"><div class="card"><div class="section-head"><h2>История по периодам</h2><span class="muted">{{ type_label(selected_type) }}</span></div><div class="table-wrap"><table style="min-width:1250px"><thead><tr><th>Период</th><th>Шт.</th><th>Доход</th><th>Выплата</th><th>Себес.</th><th>Внешние</th><th>Реклама</th><th>До налога</th><th>УСН</th><th>Чистая</th><th>Маржа</th><th>ДРР</th></tr></thead><tbody>{% for row in periods %}<tr><td><a class="period-link" href="/?report_id={{ row.report_id }}&period_type={{ row.period_type }}">{{ period(row.period_start,row.period_end) }}</a></td><td>{{ units(row.units) }}</td><td>{{ money(row.revenue) }}</td><td>{{ money(row.payout) }}</td><td>{{ money(row.cogs) }}</td><td>{{ money(row.external_expenses) }}</td><td>{{ money(row.advertising) }}</td><td class="{{ 'profit-pos' if row.profit_before_tax >= 0 else 'profit-neg' }}">{{ money(row.profit_before_tax) }}</td><td>{{ money(row.tax) }}</td><td class="{{ 'profit-pos' if row.profit >= 0 else 'profit-neg' }}">{{ money(row.profit) }}</td><td>{{ percent(row.margin) }}</td><td>{{ percent(row.drr) }}</td></tr>{% endfor %}</tbody></table></div></div>
<div class="card"><div class="section-head"><div><h2>История себестоимости</h2><div class="subtitle">Редактируется в разделе расходов</div></div><a class="button secondary small" href="/admin#cost-form">Добавить цену</a></div>{% if cost_profiles %}<div class="table-wrap"><table style="min-width:720px"><thead><tr><th>С даты</th><th>Себес.</th><th>Расходы / шт.</th><th>Всего / шт.</th></tr></thead><tbody>{% for row in cost_profiles %}<tr><td>{{ date_display(row.effective_from) }}</td><td>{{ money(row.unit_cost) }}</td><td>{{ money(row.fulfillment_per_unit + row.packaging_per_unit + row.warehouse_delivery_per_unit + row.other_per_unit) }}</td><td>{{ money(row.unit_cost + row.fulfillment_per_unit + row.packaging_per_unit + row.warehouse_delivery_per_unit + row.other_per_unit) }}</td></tr>{% endfor %}</tbody></table></div>{% else %}<div class="empty">Для этого артикула нет записи себестоимости.</div>{% endif %}<div class="hint">После изменения цены прошлые сохранённые периоды пересчитываются командой <b>/backfill ДД.ММ.ГГГГ</b>.</div></div></div>
<script>const labels={{ chart_labels|tojson }};new Chart(document.getElementById('productChart'),{type:'line',data:{labels:labels,datasets:[{label:'Доход',data:{{ chart_revenue|tojson }},borderColor:'#d6a7ff',backgroundColor:'rgba(214,167,255,.10)',tension:.28,fill:false},{label:'Прибыль до налога',data:{{ chart_pre_tax|tojson }},borderColor:'#ffcc66',backgroundColor:'rgba(255,204,102,.10)',tension:.28,fill:false},{label:'Чистая прибыль',data:{{ chart_profit|tojson }},borderColor:'#3ddc97',backgroundColor:'rgba(61,220,151,.10)',tension:.28,fill:false}]},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{labels:{color:'#cbd2df'}}},scales:{x:{ticks:{color:'#929bad',maxRotation:45,minRotation:0},grid:{color:'rgba(255,255,255,.05)'}},y:{ticks:{color:'#929bad',callback:(v)=>new Intl.NumberFormat('ru-RU').format(v)+' ₽'},grid:{color:'rgba(255,255,255,.06)'}}}}});</script>
<footer>WB Profit Dashboard · карточка товара</footer></div></body></html>
"""


UNIT_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Юнит-экономика · {{ title }}</title>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>Юнит-экономика</h1><div class="subtitle">Цена, выплата WB, расходы, налог, реклама, маржа и точка безубыточности по каждому SKU</div></div><div class="badge">Товаров: {{ totals.products }}</div></header>
<nav class="nav"><a href="/">Дашборд</a><a href="/products">Товары</a><a class="active" href="/unit-economics">Юнит-экономика</a><a href="/admin">Себестоимость и расходы</a></nav>
<form class="filters" method="get"><label>Периоды<select name="period_type"><option value="weekly" {% if selected_type == 'weekly' %}selected{% endif %}>Недельные</option><option value="daily" {% if selected_type == 'daily' %}selected{% endif %}>Дневные и произвольные</option><option value="xlsx" {% if selected_type == 'xlsx' %}selected{% endif %}>Excel</option></select></label><label>С даты<input type="date" name="date_from" value="{{ date_from_value }}"></label><label>По дату<input type="date" name="date_to" value="{{ date_to_value }}"></label><label>Поиск<input name="q" value="{{ query }}" placeholder="Название, артикул или nmID"></label><label>Целевая маржа, %<input inputmode="decimal" name="target_margin" value="{{ target_margin_input }}"></label><label>Сценарий ДРР, %<input inputmode="decimal" name="scenario_drr" value="{{ scenario_drr_input }}"></label><button type="submit">Пересчитать</button></form>
<div class="hint">Расчёт основан на сохранённых отчётах PostgreSQL. Для корректности используй один тип периодов, лучше <b>недельные</b>, чтобы не смешивать дневные и недельные отчёты. Точка безубыточности считается приблизительно: предполагается, что доля выплаты WB от цены остаётся такой же, как в выбранном периоде.</div>
{% if rows %}<div class="grid section"><div class="card"><div class="label">Продано</div><div class="value">{{ units(totals.units) }} шт.</div></div><div class="card"><div class="label">Средняя цена</div><div class="value">{{ money(totals.avg_price) }}</div></div><div class="card"><div class="label">Прибыль / шт.</div><div class="value {{ 'good' if totals.profit_per_unit >= 0 else 'bad' }}">{{ money(totals.profit_per_unit) }}</div></div><div class="card"><div class="label">Маржа</div><div class="value {{ margin_class(totals.margin) }}">{{ percent(totals.margin) }}</div></div><div class="card"><div class="label">ДРР</div><div class="value">{{ percent(totals.drr) }}</div></div><div class="card"><div class="label">Макс ДРР до нуля</div><div class="value {{ 'bad' if totals.max_drr_zero < totals.drr else 'good' }}">{{ percent(totals.max_drr_zero) }}</div></div><div class="card"><div class="label">Макс ДРР для цели</div><div class="value {{ 'bad' if totals.max_drr_target < totals.drr else 'good' }}">{{ percent(totals.max_drr_target) }}</div></div><div class="card"><div class="label">Сценарий прибыль</div><div class="value {{ 'good' if totals.scenario_profit >= 0 else 'bad' }}">{{ money(totals.scenario_profit) }}</div></div><div class="card"><div class="label">Сценарий маржа</div><div class="value {{ margin_class(totals.scenario_margin) }}">{{ percent(totals.scenario_margin) }}</div></div><div class="card"><div class="label">Товаров в минус</div><div class="value {{ 'bad' if totals.loss_products else 'good' }}">{{ totals.loss_products }}</div></div></div>
<div class="section card"><div class="section-head"><div><h2>Таблица юнит-экономики</h2><div class="subtitle">Нажми на товар, чтобы открыть его отдельную аналитику</div></div><span class="muted">Цель: {{ percent(target_margin) }} · Сценарий ДРР: {{ percent(scenario_drr) }}</span></div><div class="table-wrap"><table style="min-width:1820px"><thead><tr><th>Товар</th><th>Артикул</th><th>Шт.</th><th>Цена ср.</th><th>Выплата / шт.</th><th>Услуги WB / шт.</th><th>Себес. / шт.</th><th>Внешн. / шт.</th><th>Реклама / шт.</th><th>УСН / шт.</th><th>Прибыль / шт.</th><th>Маржа</th><th>ДРР</th><th>Макс ДРР 0</th><th>Макс ДРР цель</th><th>Безуб. цена</th><th>Цена для цели</th><th>Сценарий прибыль</th><th>Статус</th></tr></thead><tbody>{% for row in rows %}<tr><td><a class="text-link" href="{{ url_for('product_detail', sku=row.sku, period_type=selected_type, date_from=date_from_value, date_to=date_to_value) }}">{{ row.name or row.sku }}</a></td><td class="muted">{{ row.sku }}</td><td>{{ units(row.units) }}</td><td>{{ money(row.avg_price) }}</td><td>{{ money(row.payout_per_unit) }}</td><td>{{ money(row.wb_services_per_unit) }}</td><td>{{ money(row.cogs_per_unit) }}</td><td>{{ money(row.external_per_unit) }}</td><td>{{ money(row.ad_per_unit) }}</td><td>{{ money(row.tax_per_unit) }}</td><td class="{{ 'profit-pos' if row.profit_per_unit >= 0 else 'profit-neg' }}">{{ money(row.profit_per_unit) }}</td><td>{{ percent(row.margin) }}</td><td>{{ percent(row.drr) }}</td><td class="{{ 'profit-neg' if row.max_drr_zero < row.drr else 'profit-pos' }}">{{ percent(row.max_drr_zero) }}</td><td class="{{ 'profit-neg' if row.max_drr_target < row.drr else 'profit-pos' }}">{{ percent(row.max_drr_target) }}</td><td>{{ money(row.break_even_price) if row.break_even_price is not none else '—' }}</td><td>{{ money(row.target_price) if row.target_price is not none else '—' }}</td><td class="{{ 'profit-pos' if row.scenario_profit >= 0 else 'profit-neg' }}">{{ money(row.scenario_profit) }}</td><td class="{{ 'rank-good' if row.status_level == 'good' else 'rank-bad' if row.status_level == 'bad' else 'value warn' }}">{{ row.status }}</td></tr>{% endfor %}</tbody></table></div></div>
<div class="section card"><h2>Как читать показатели</h2><div class="hint"><b>Услуги WB / шт.</b> = доход покупателей минус расчётная выплата WB. Сюда попадает комиссия, логистика, удержания и прочие услуги из финансового отчёта. <b>Макс ДРР 0</b> — рекламный расход, при котором товар выходит примерно в ноль после УСН. <b>Макс ДРР цель</b> — максимальный ДРР, чтобы сохранить выбранную целевую маржу. <b>Цена для цели</b> — ориентировочная цена продажи при текущих расходах и выбранной марже.</div></div>
{% else %}<div class="card empty section">Нет данных для расчёта. Сначала сохрани недельные отчёты через /week или /backfill.</div>{% endif %}
<footer>WB Profit Dashboard · юнит-экономика рассчитывается по сохранённым отчётам</footer></div></body></html>
"""


ADMIN_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Управление данными · WB Profit</title>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>Себестоимость и расходы</h1><div class="subtitle">Изменения сохраняются в PostgreSQL и не пропадают после перезапуска Railway</div></div><div class="badge">{{ cost_count }} профилей · {{ expense_count }} расходов</div></header>
<nav class="nav"><a href="/">Дашборд</a><a href="/products">Товары</a><a href="/unit-economics">Юнит-экономика</a><a class="active" href="/admin">Себестоимость и расходы</a></nav>
{% if ok %}<div class="notice ok">✅ {{ ok }}</div>{% endif %}{% if error %}<div class="notice error">❌ {{ error }}</div>{% endif %}
<div class="hint" style="margin:0 0 16px">Новая цена добавляется отдельной строкой с датой начала действия. Старую строку не меняй, если цена действительно изменилась позже. Новые отчёты сразу используют эти данные; для пересчёта истории запусти в Telegram <b>/backfill ДД.ММ.ГГГГ</b>.</div>
<section class="section admin-cols">
<div class="card" id="cost-form"><h2>{{ 'Изменить запись' if edit_cost else 'Добавить себестоимость' }}</h2><div class="subtitle">Стоимость и расходы на одну проданную единицу</div><form method="post" action="/admin/costs/save" style="margin-top:16px"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="profile_id" value="{{ edit_cost.profile_id if edit_cost else '' }}">
<div class="field"><label>Артикул поставщика *</label><input name="sku" required value="{{ edit_cost.sku if edit_cost else '' }}" placeholder="например, олимп креат"></div><div class="field"><label>Название товара</label><input name="name" value="{{ edit_cost.name if edit_cost else '' }}" placeholder="Креатин Olimp 250 г"></div><div class="field"><label>Действует с *</label><input type="date" name="effective_from" required value="{{ date_input(edit_cost.effective_from) if edit_cost else today }}"></div>
<div class="form-grid"><div class="field"><label>Себестоимость, ₽ *</label><input inputmode="decimal" name="unit_cost" required value="{{ number_input(edit_cost.unit_cost) if edit_cost else '0' }}"></div><div class="field"><label>Фулфилмент / шт., ₽</label><input inputmode="decimal" name="fulfillment" value="{{ number_input(edit_cost.fulfillment_per_unit) if edit_cost else '0' }}"></div><div class="field"><label>Упаковка / шт., ₽</label><input inputmode="decimal" name="packaging" value="{{ number_input(edit_cost.packaging_per_unit) if edit_cost else '0' }}"></div><div class="field"><label>Доставка до склада / шт., ₽</label><input inputmode="decimal" name="warehouse_delivery" value="{{ number_input(edit_cost.warehouse_delivery_per_unit) if edit_cost else '0' }}"></div><div class="field"><label>Прочие / шт., ₽</label><input inputmode="decimal" name="other" value="{{ number_input(edit_cost.other_per_unit) if edit_cost else '0' }}"></div></div>
<div class="form-actions"><button type="submit">{{ 'Сохранить изменения' if edit_cost else 'Добавить запись' }}</button>{% if edit_cost %}<a class="button secondary" href="/admin#cost-form">Отмена</a>{% endif %}</div></form></div>
<div class="card"><div class="section-head"><div><h2>История себестоимости</h2><div class="subtitle">Запись с датой 01.01.1900 — исходная цена из старого costs.xlsx</div></div></div><div class="table-wrap"><table style="min-width:1120px"><thead><tr><th>Товар</th><th>Артикул</th><th>С даты</th><th>Себес.</th><th>Фулф.</th><th>Упак.</th><th>Доставка</th><th>Прочие</th><th>Всего / шт.</th><th>Действия</th></tr></thead><tbody>{% for row in costs %}<tr><td>{{ row.name }}</td><td class="muted">{{ row.sku }}</td><td>{{ date_display(row.effective_from) }}</td><td>{{ money(row.unit_cost) }}</td><td>{{ money(row.fulfillment_per_unit) }}</td><td>{{ money(row.packaging_per_unit) }}</td><td>{{ money(row.warehouse_delivery_per_unit) }}</td><td>{{ money(row.other_per_unit) }}</td><td>{{ money(row.unit_cost + row.fulfillment_per_unit + row.packaging_per_unit + row.warehouse_delivery_per_unit + row.other_per_unit) }}</td><td><div class="inline-actions"><a class="button secondary small" href="/admin?edit_cost={{ row.profile_id }}#cost-form">Изменить</a><form method="post" action="/admin/costs/delete" onsubmit="return confirm('Удалить эту запись себестоимости?')"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="profile_id" value="{{ row.profile_id }}"><button class="danger small" type="submit">Удалить</button></form></div></td></tr>{% else %}<tr><td class="empty" colspan="10">Записей пока нет.</td></tr>{% endfor %}</tbody></table></div></div></section>
<section class="section admin-cols">
<div class="card" id="expense-form"><h2>{{ 'Изменить расход' if edit_expense else 'Добавить внешний расход' }}</h2><div class="subtitle">Зарплата, аренда, доставка партии, дизайн и другие расходы вне отчёта WB</div><form method="post" action="/admin/expenses/save" style="margin-top:16px"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="expense_id" value="{{ edit_expense.expense_id if edit_expense else '' }}"><div class="form-grid"><div class="field"><label>Дата начала *</label><input type="date" name="period_start" required value="{{ date_input(edit_expense.period_start) if edit_expense else today }}"></div><div class="field"><label>Дата окончания *</label><input type="date" name="period_end" required value="{{ date_input(edit_expense.period_end) if edit_expense else today }}"></div></div><div class="field"><label>Категория *</label><input name="category" required value="{{ edit_expense.category if edit_expense else '' }}" placeholder="Зарплата / аренда / доставка партии"></div><div class="field"><label>Сумма, ₽ *</label><input inputmode="decimal" name="amount" required value="{{ number_input(edit_expense.amount) if edit_expense else '' }}"></div><div class="field"><label>Артикул поставщика</label><input name="sku" value="{{ edit_expense.sku if edit_expense else '' }}" placeholder="Оставь пустым для общего расхода"></div><div class="field"><label>Комментарий</label><textarea name="comment" placeholder="За что расход и к какой партии относится">{{ edit_expense.comment if edit_expense else '' }}</textarea></div><div class="form-actions"><button type="submit">{{ 'Сохранить изменения' if edit_expense else 'Добавить расход' }}</button>{% if edit_expense %}<a class="button secondary" href="/admin#expense-form">Отмена</a>{% endif %}</div></form></div>
<div class="card"><div class="section-head"><div><h2>Внешние расходы</h2><div class="subtitle">Общий расход без артикула распределяется по товарам пропорционально доходу</div></div></div><div class="table-wrap"><table style="min-width:930px"><thead><tr><th>Категория</th><th>Период</th><th>Сумма</th><th>Артикул</th><th>Комментарий</th><th>Действия</th></tr></thead><tbody>{% for row in expenses %}<tr><td>{{ row.category }}</td><td>{{ period(row.period_start,row.period_end) }}</td><td>{{ money(row.amount) }}</td><td class="muted">{{ row.sku or 'Общий расход' }}</td><td class="muted">{{ row.comment or '—' }}</td><td><div class="inline-actions"><a class="button secondary small" href="/admin?edit_expense={{ row.expense_id }}#expense-form">Изменить</a><form method="post" action="/admin/expenses/delete" onsubmit="return confirm('Удалить этот внешний расход?')"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="expense_id" value="{{ row.expense_id }}"><button class="danger small" type="submit">Удалить</button></form></div></td></tr>{% else %}<tr><td class="empty" colspan="6">Внешних расходов пока нет.</td></tr>{% endfor %}</tbody></table></div></div></section>
<footer>WB Profit Dashboard · редактирование защищено тем же логином и паролем</footer></div></body></html>
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
    return Response("Нужна авторизация для доступа к WB Profit Dashboard.", 401, {"WWW-Authenticate": 'Basic realm="WB Profit Dashboard", charset="UTF-8"'})


def _csrf_token() -> str:
    secret = (_dashboard_password() + "|" + _dashboard_user()).encode("utf-8")
    return hmac.new(secret, b"wb-profit-dashboard-v16", hashlib.sha256).hexdigest()


def _valid_csrf() -> bool:
    supplied = request.form.get("csrf", "")
    return bool(supplied) and hmac.compare_digest(supplied, _csrf_token())


@app.before_request
def protect_dashboard():
    if request.path == "/health":
        return None
    if not _dashboard_password():
        return Response("DASHBOARD_PASSWORD не задан в Railway Variables.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    if not _authorized():
        return _auth_required()
    return None


@app.after_request
def add_security_headers(response: Response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    return {"status": "ok", "database": database_enabled(), "management": management_store_ready()}


def _money(value: float) -> str:
    number = float(value)
    sign = "−" if number < 0 else ""
    text = f"{abs(number):,.2f} ₽".replace(",", " ").replace(".00 ₽", " ₽")
    return sign + text


def _percent(value: float) -> str:
    return f"{float(value) * 100:.1f}%".replace(".", ",")


def _units(value: float) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.1f}".replace(".", ",")


def _period(start: date, end: date) -> str:
    return start.strftime("%d.%m.%Y") if start == end else f"{start.strftime('%d.%m.%Y')}–{end.strftime('%d.%m.%Y')}"


def _type_label(value: str) -> str:
    return {"daily": "Дневной", "weekly": "Недельный", "xlsx": "Excel"}.get(value, value)


def _margin_class(value: float) -> str:
    return "bad" if value < 0 else "warn" if value < .10 else "good"


def _date_input(value: date) -> str:
    return value.isoformat()


def _date_display(value: date) -> str:
    return "Базовая" if value == date(1900, 1, 1) else value.strftime("%d.%m.%Y")


def _number_input(value: float) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.2f}".rstrip("0").rstrip(".")


def _form_float(name: str, default: float = 0) -> float:
    raw = request.form.get(name, "").strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if raw == "":
        return float(default)
    return float(raw)


def _form_date(name: str) -> date:
    raw = request.form.get(name, "").strip()
    return datetime.strptime(raw, "%Y-%m-%d").date()



def _query_date(name: str) -> date | None:
    raw = request.args.get(name, "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _admin_redirect(*, ok: str = "", error: str = "", anchor: str = ""):
    query = []
    if ok:
        query.append("ok=" + quote(ok))
    if error:
        query.append("error=" + quote(error))
    target = url_for("admin_data") + (("?" + "&".join(query)) if query else "") + anchor
    return redirect(target, code=303)


def _query_percent(name: str, default: float) -> float:
    raw = request.args.get(name, "").strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if raw == "":
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        return float(default)
    if value > 1:
        value = value / 100
    return max(-1.0, min(value, 3.0))


def _input_percent(value: float) -> str:
    number = float(value) * 100
    return str(int(number)) if number.is_integer() else f"{number:.1f}".replace(".", ",")


def _div(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) if abs(float(denominator)) > 0.000001 else 0.0


def _target_price(total_variable_per_unit: float, payout_rate: float, tax_rate: float, target_margin: float) -> float | None:
    denominator = payout_rate - tax_rate - target_margin
    if denominator <= 0.000001:
        return None
    return total_variable_per_unit / denominator


def _unit_economy_row(row, *, target_margin: float, scenario_drr: float) -> dict[str, Any]:
    units = float(row.units or 0)
    revenue = float(row.revenue or 0)
    payout = float(row.payout or 0)
    advertising = float(row.advertising or 0)
    profit = float(row.profit or 0)
    avg_price = _div(revenue, units)
    payout_per_unit = _div(payout, units)
    wb_services_per_unit = _div(revenue - payout, units)
    cogs_per_unit = _div(row.cogs, units)
    external_per_unit = _div(row.external_expenses, units)
    ad_per_unit = _div(advertising, units)
    tax_per_unit = _div(row.tax, units)
    profit_per_unit = _div(profit, units)
    payout_rate = _div(payout, revenue)
    tax_rate = _div(row.tax, revenue) or 0.06
    max_drr_zero = _div(profit + advertising, revenue)
    max_drr_target = max_drr_zero - target_margin
    break_even_price = _target_price(cogs_per_unit + external_per_unit + ad_per_unit, payout_rate, tax_rate, 0)
    target_price = _target_price(cogs_per_unit + external_per_unit + ad_per_unit, payout_rate, tax_rate, target_margin)
    scenario_advertising = revenue * scenario_drr
    scenario_profit = payout - float(row.cogs or 0) - float(row.external_expenses or 0) - scenario_advertising - float(row.tax or 0)
    scenario_margin = _div(scenario_profit, revenue)

    if profit < -0.000001:
        status, status_level = "В минусе", "bad"
    elif max_drr_target < float(row.drr or 0):
        status, status_level = "Ниже цели", "warn"
    elif target_price is not None and avg_price < target_price:
        status, status_level = "Цена ниже цели", "warn"
    else:
        status, status_level = "Норма", "good"

    return {
        "sku": row.sku,
        "name": row.name,
        "units": units,
        "revenue": revenue,
        "avg_price": avg_price,
        "payout_per_unit": payout_per_unit,
        "wb_services_per_unit": wb_services_per_unit,
        "cogs_per_unit": cogs_per_unit,
        "external_per_unit": external_per_unit,
        "ad_per_unit": ad_per_unit,
        "tax_per_unit": tax_per_unit,
        "profit_per_unit": profit_per_unit,
        "margin": float(row.margin or 0),
        "drr": float(row.drr or 0),
        "max_drr_zero": max_drr_zero,
        "max_drr_target": max_drr_target,
        "break_even_price": break_even_price,
        "target_price": target_price,
        "scenario_profit": scenario_profit,
        "scenario_margin": scenario_margin,
        "status": status,
        "status_level": status_level,
    }


@app.get("/")
def dashboard():
    if not database_enabled():
        return Response("DATABASE_URL не задан. Сначала подключи PostgreSQL к сервису бота.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    selected_type = request.args.get("period_type", "").strip()
    if selected_type not in {"", "daily", "weekly", "xlsx"}:
        selected_type = ""
    reports_desc = list_dashboard_reports(200, period_type=selected_type or None)
    latest = reports_desc[0] if reports_desc else None
    selected_report_id = request.args.get("report_id", "").strip()
    if selected_report_id.isdigit():
        candidate = get_dashboard_report(int(selected_report_id))
        if candidate is not None and (not selected_type or candidate.period_type == selected_type):
            latest = candidate
    skus = list_dashboard_skus(latest.report_id) if latest else []
    reports_asc = list(reversed(reports_desc))
    context: dict[str, Any] = {
        "title": os.getenv("DASHBOARD_TITLE", "WB Profit Dashboard").strip() or "WB Profit Dashboard",
        "latest": latest,
        "skus": skus,
        "reports_desc": reports_desc,
        "selected_type": selected_type,
        "chart_labels": [_period(r.period_start, r.period_end) for r in reports_asc],
        "chart_profit": [round(r.profit, 2) for r in reports_asc],
        "chart_pre_tax": [round(r.profit_before_tax, 2) for r in reports_asc],
        "chart_revenue": [round(r.revenue, 2) for r in reports_asc],
        "latest_created": latest.created_at.strftime("%d.%m.%Y %H:%M") if latest else "ещё нет",
        "latest_period": _period(latest.period_start, latest.period_end) if latest else "",
        "money": _money,
        "percent": _percent,
        "units": _units,
        "period": _period,
        "type_label": _type_label,
        "margin_class": _margin_class,
    }
    return render_template_string(DASHBOARD_TEMPLATE, **context)



@app.get("/products")
def products():
    if not database_enabled():
        return Response("DATABASE_URL не задан.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    selected_type = request.args.get("period_type", "weekly").strip()
    if selected_type not in {"weekly", "daily", "xlsx"}:
        selected_type = "weekly"
    date_from = _query_date("date_from")
    date_to = _query_date("date_to")
    query = request.args.get("q", "").strip()[:120]
    rows = list_product_summaries(
        1000,
        period_type=selected_type,
        date_from=date_from,
        date_to=date_to,
        query=query,
    )
    revenue = sum(row.revenue for row in rows)
    profit = sum(row.profit for row in rows)
    advertising = sum(row.advertising for row in rows)
    totals = {
        "products": len(rows),
        "units": sum(row.units for row in rows),
        "revenue": revenue,
        "payout": sum(row.payout for row in rows),
        "cogs": sum(row.cogs for row in rows),
        "tax": sum(row.tax for row in rows),
        "advertising": advertising,
        "external_expenses": sum(row.external_expenses for row in rows),
        "profit_before_tax": sum(row.profit_before_tax for row in rows),
        "profit": profit,
        "margin": profit / revenue if abs(revenue) > 0.000001 else 0,
        "drr": advertising / revenue if abs(revenue) > 0.000001 else 0,
    }
    return render_template_string(
        PRODUCTS_TEMPLATE,
        title=os.getenv("DASHBOARD_TITLE", "WB Profit Dashboard").strip() or "WB Profit Dashboard",
        rows=rows,
        totals=totals,
        selected_type=selected_type,
        date_from_value=date_from.isoformat() if date_from else "",
        date_to_value=date_to.isoformat() if date_to else "",
        query=query,
        money=_money,
        percent=_percent,
        units=_units,
        type_label=_type_label,
        margin_class=_margin_class,
        date_display=_date_display,
    )


@app.get("/unit-economics")
def unit_economics():
    if not database_enabled():
        return Response("DATABASE_URL не задан.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    selected_type = request.args.get("period_type", "weekly").strip()
    if selected_type not in {"weekly", "daily", "xlsx"}:
        selected_type = "weekly"
    date_from = _query_date("date_from")
    date_to = _query_date("date_to")
    query = request.args.get("q", "").strip()[:120]
    target_margin = _query_percent("target_margin", 0.15)
    scenario_drr = _query_percent("scenario_drr", 0.15)
    source_rows = list_product_summaries(
        1000,
        period_type=selected_type,
        date_from=date_from,
        date_to=date_to,
        query=query,
    )
    rows = [_unit_economy_row(row, target_margin=target_margin, scenario_drr=scenario_drr) for row in source_rows if abs(float(row.units or 0)) > 0.000001]
    rows.sort(key=lambda r: (r["profit_per_unit"], r["revenue"]), reverse=True)

    total_units = sum(float(row.units or 0) for row in source_rows)
    total_revenue = sum(float(row.revenue or 0) for row in source_rows)
    total_payout = sum(float(row.payout or 0) for row in source_rows)
    total_cogs = sum(float(row.cogs or 0) for row in source_rows)
    total_tax = sum(float(row.tax or 0) for row in source_rows)
    total_advertising = sum(float(row.advertising or 0) for row in source_rows)
    total_external = sum(float(row.external_expenses or 0) for row in source_rows)
    total_profit = sum(float(row.profit or 0) for row in source_rows)
    total_profit_no_ads = total_profit + total_advertising
    total_scenario_advertising = total_revenue * scenario_drr
    total_scenario_profit = total_payout - total_cogs - total_external - total_scenario_advertising - total_tax
    totals = {
        "products": len(rows),
        "loss_products": sum(1 for row in rows if row["profit_per_unit"] < -0.000001),
        "units": total_units,
        "avg_price": _div(total_revenue, total_units),
        "profit_per_unit": _div(total_profit, total_units),
        "revenue": total_revenue,
        "profit": total_profit,
        "margin": _div(total_profit, total_revenue),
        "drr": _div(total_advertising, total_revenue),
        "max_drr_zero": _div(total_profit_no_ads, total_revenue),
        "max_drr_target": _div(total_profit_no_ads, total_revenue) - target_margin,
        "scenario_profit": total_scenario_profit,
        "scenario_margin": _div(total_scenario_profit, total_revenue),
    }
    return render_template_string(
        UNIT_TEMPLATE,
        title=os.getenv("DASHBOARD_TITLE", "WB Profit Dashboard").strip() or "WB Profit Dashboard",
        rows=rows,
        totals=totals,
        selected_type=selected_type,
        date_from_value=date_from.isoformat() if date_from else "",
        date_to_value=date_to.isoformat() if date_to else "",
        query=query,
        target_margin=target_margin,
        scenario_drr=scenario_drr,
        target_margin_input=_input_percent(target_margin),
        scenario_drr_input=_input_percent(scenario_drr),
        money=_money,
        percent=_percent,
        units=_units,
        margin_class=_margin_class,
    )


@app.get("/product")
def product_detail():
    if not database_enabled():
        return Response("DATABASE_URL не задан.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    sku = request.args.get("sku", "").strip()
    if not sku:
        return redirect(url_for("products"), code=302)
    selected_type = request.args.get("period_type", "weekly").strip()
    if selected_type not in {"weekly", "daily", "xlsx"}:
        selected_type = "weekly"
    date_from = _query_date("date_from")
    date_to = _query_date("date_to")
    summary = get_product_summary(
        sku,
        period_type=selected_type,
        date_from=date_from,
        date_to=date_to,
    )
    if summary is None:
        return Response("Товар не найден в сохранённых отчётах.", 404, {"Content-Type": "text/plain; charset=utf-8"})
    periods = list_product_periods(
        summary.sku,
        1000,
        period_type=selected_type,
        date_from=date_from,
        date_to=date_to,
    )
    sku_key = summary.sku.strip().casefold()
    cost_profiles = [row for row in list_cost_profiles(limit=5000) if row.sku.strip().casefold() == sku_key]
    return render_template_string(
        PRODUCT_DETAIL_TEMPLATE,
        title=os.getenv("DASHBOARD_TITLE", "WB Profit Dashboard").strip() or "WB Profit Dashboard",
        summary=summary,
        periods=periods,
        cost_profiles=cost_profiles,
        selected_type=selected_type,
        date_from_value=date_from.isoformat() if date_from else "",
        date_to_value=date_to.isoformat() if date_to else "",
        profit_per_unit=summary.profit / summary.units if abs(summary.units) > 0.000001 else 0,
        chart_labels=[_period(row.period_start, row.period_end) for row in periods],
        chart_revenue=[round(row.revenue, 2) for row in periods],
        chart_pre_tax=[round(row.profit_before_tax, 2) for row in periods],
        chart_profit=[round(row.profit, 2) for row in periods],
        money=_money,
        percent=_percent,
        units=_units,
        period=_period,
        type_label=_type_label,
        margin_class=_margin_class,
        date_display=_date_display,
    )


@app.get("/admin")
def admin_data():
    if not database_enabled() or not management_store_ready():
        return Response("Панель данных ещё не инициализирована. Подожди завершения Deploy в Railway.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    costs = list_cost_profiles(limit=5000)
    expenses = list_expenses(limit=5000)
    edit_cost_id = request.args.get("edit_cost", "").strip()
    edit_expense_id = request.args.get("edit_expense", "").strip()
    edit_cost = get_cost_profile(int(edit_cost_id)) if edit_cost_id.isdigit() else None
    edit_expense = get_expense(int(edit_expense_id)) if edit_expense_id.isdigit() else None
    cost_count, expense_count = management_counts()
    return render_template_string(
        ADMIN_TEMPLATE,
        costs=costs,
        expenses=expenses,
        edit_cost=edit_cost,
        edit_expense=edit_expense,
        cost_count=cost_count,
        expense_count=expense_count,
        csrf=_csrf_token(),
        today=date.today().isoformat(),
        ok=request.args.get("ok", "").strip(),
        error=request.args.get("error", "").strip(),
        money=_money,
        period=_period,
        date_input=_date_input,
        date_display=_date_display,
        number_input=_number_input,
    )


@app.post("/admin/costs/save")
def admin_cost_save():
    if not _valid_csrf():
        return Response("Неверный CSRF-токен.", 400, {"Content-Type": "text/plain; charset=utf-8"})
    try:
        raw_id = request.form.get("profile_id", "").strip()
        upsert_cost_profile(
            profile_id=int(raw_id) if raw_id.isdigit() else None,
            sku=request.form.get("sku", ""),
            name=request.form.get("name", ""),
            effective_from=_form_date("effective_from"),
            unit_cost=_form_float("unit_cost"),
            fulfillment_per_unit=_form_float("fulfillment"),
            packaging_per_unit=_form_float("packaging"),
            warehouse_delivery_per_unit=_form_float("warehouse_delivery"),
            other_per_unit=_form_float("other"),
        )
        return _admin_redirect(ok="Себестоимость сохранена. Для пересчёта прошлых периодов запусти /backfill.", anchor="#cost-form")
    except Exception as exc:
        logger.exception("Не удалось сохранить себестоимость")
        return _admin_redirect(error=str(exc), anchor="#cost-form")


@app.post("/admin/costs/delete")
def admin_cost_delete():
    if not _valid_csrf():
        return Response("Неверный CSRF-токен.", 400, {"Content-Type": "text/plain; charset=utf-8"})
    raw_id = request.form.get("profile_id", "").strip()
    try:
        if not raw_id.isdigit() or not delete_cost_profile(int(raw_id)):
            raise ValueError("Запись себестоимости не найдена.")
        return _admin_redirect(ok="Запись себестоимости удалена. При необходимости запусти /backfill.")
    except Exception as exc:
        return _admin_redirect(error=str(exc))


@app.post("/admin/expenses/save")
def admin_expense_save():
    if not _valid_csrf():
        return Response("Неверный CSRF-токен.", 400, {"Content-Type": "text/plain; charset=utf-8"})
    try:
        raw_id = request.form.get("expense_id", "").strip()
        upsert_expense(
            expense_id=int(raw_id) if raw_id.isdigit() else None,
            period_start=_form_date("period_start"),
            period_end=_form_date("period_end"),
            category=request.form.get("category", ""),
            amount=_form_float("amount"),
            sku=request.form.get("sku", ""),
            comment=request.form.get("comment", ""),
        )
        return _admin_redirect(ok="Внешний расход сохранён. Для пересчёта прошлых периодов запусти /backfill.", anchor="#expense-form")
    except Exception as exc:
        logger.exception("Не удалось сохранить внешний расход")
        return _admin_redirect(error=str(exc), anchor="#expense-form")


@app.post("/admin/expenses/delete")
def admin_expense_delete():
    if not _valid_csrf():
        return Response("Неверный CSRF-токен.", 400, {"Content-Type": "text/plain; charset=utf-8"})
    raw_id = request.form.get("expense_id", "").strip()
    try:
        if not raw_id.isdigit() or not delete_expense(int(raw_id)):
            raise ValueError("Расход не найден.")
        return _admin_redirect(ok="Внешний расход удалён. При необходимости запусти /backfill.")
    except Exception as exc:
        return _admin_redirect(error=str(exc))


def start_dashboard_server() -> None:
    global _server_started
    with _server_lock:
        if _server_started:
            return
        _server_started = True
    raw_port = os.getenv("PORT", "8080").strip()
    try:
        port = int(raw_port)
    except ValueError:
        port = 8080

    def _run() -> None:
        logger.info("Веб-дашборд запущен на 0.0.0.0:%d", port)
        serve(app, host="0.0.0.0", port=port, threads=4, channel_timeout=30)

    threading.Thread(target=_run, name="wb-dashboard", daemon=True).start()
