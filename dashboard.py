from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any
from types import SimpleNamespace
from urllib.parse import quote

from flask import Flask, Response, redirect, render_template_string, request, url_for
from waitress import serve

import psycopg
from psycopg.rows import dict_row

from wb_db import (
    database_enabled,
    get_dashboard_report,
    get_product_summary,
    list_dashboard_reports,
    list_dashboard_skus,
    list_product_periods,
    list_product_summaries,
    list_sale_operations,
    sale_operations_summary,
)
from wb_api import (
    WbApiError,
    get_product_cards,
    get_seller_warehouse_stocks,
    get_seller_warehouses,
    get_wb_warehouse_stocks,
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
table{width:100%;border-collapse:collapse;min-width:1050px}th,td{padding:11px 10px;border-bottom:1px solid var(--line);text-align:right;font-size:12.5px;white-space:nowrap}th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--panel)}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}tr:last-child td{border-bottom:0}.profit-pos{color:var(--good);font-weight:700}.profit-neg{color:var(--bad);font-weight:700}.muted{color:var(--muted)}.empty{padding:40px 20px;text-align:center;color:var(--muted)}.period-link,.text-link{color:var(--text);text-decoration:none;font-weight:650}.period-link:hover,.text-link:hover{color:#d6a7ff}.hint{margin-top:12px;padding:12px 14px;border:1px dashed var(--line);border-radius:12px;color:var(--muted);font-size:12px;line-height:1.5}.notice{margin-bottom:14px;padding:12px 14px;border-radius:12px;border:1px solid var(--line);font-size:13px}.notice.ok{background:rgba(61,220,151,.10);border-color:rgba(61,220,151,.28);color:#9af0c9}.notice.error{background:rgba(255,102,120,.10);border-color:rgba(255,102,120,.28);color:#ffadb7}.field{display:flex;flex-direction:column;gap:6px;margin-bottom:10px}.field label{color:var(--muted);font-size:12px}.field input,.field textarea,.field select{width:100%}.form-actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:12px}.inline-actions{display:flex;gap:7px;justify-content:flex-end}.inline-actions form{margin:0}.small{font-size:11px;padding:7px 9px;border-radius:9px}.summary{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:15px}.summary span{padding:8px 11px;border-radius:999px;background:rgba(255,255,255,.04);border:1px solid var(--line);color:var(--muted);font-size:12px}.expense-bars{display:grid;gap:9px;margin-top:14px}.expense-bar{display:grid;grid-template-columns:minmax(170px,.35fr) 1fr minmax(110px,.18fr);gap:10px;align-items:center}.expense-track{height:10px;border-radius:999px;background:rgba(255,255,255,.06);overflow:hidden;border:1px solid var(--line)}.expense-fill{height:100%;border-radius:999px;background:linear-gradient(90deg,var(--accent),var(--accent2))}.expense-note{color:var(--muted);font-size:12px;text-align:right}.expense-grid{grid-template-columns:repeat(5,minmax(0,1fr))}.product-title{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.sku-chip{display:inline-flex;padding:7px 10px;border-radius:999px;border:1px solid var(--line);background:rgba(255,255,255,.04);color:var(--muted);font-size:12px}.rank-good{color:var(--good);font-weight:750}.rank-bad{color:var(--bad);font-weight:750}.filters label{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:12px}.filters label input,.filters label select{min-width:145px}footer{color:var(--muted);font-size:12px;text-align:center;padding:28px 0 10px}
@media(max-width:1150px){.grid{grid-template-columns:repeat(3,1fr)}.two-col,.admin-cols{grid-template-columns:1fr}}@media(max-width:650px){.wrap{padding:16px}header{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:repeat(2,1fr)}.card{padding:14px}.chart-box{height:300px}.form-grid>*{width:100%}}

.sku-matrix-wrap{overflow:auto;border-radius:16px;border:1px solid var(--line);max-height:72vh}.sku-matrix{min-width:2350px;border-collapse:separate;border-spacing:0}.sku-matrix th,.sku-matrix td{font-size:12px;padding:9px 8px}.sku-matrix th{z-index:3}.sku-matrix .sticky-product{position:sticky;left:0;background:#141722;z-index:2;min-width:330px;text-align:left}.sku-matrix th.sticky-product{z-index:4}.sku-product-cell{display:flex;gap:10px;align-items:center}.sku-img{width:46px;height:60px;border-radius:10px;object-fit:cover;background:rgba(255,255,255,.06);border:1px solid var(--line)}.sku-name{font-weight:750;white-space:normal;line-height:1.25}.sku-meta{font-size:11px;color:var(--muted);margin-top:4px}.sku-matrix input,.sku-matrix select{width:105px;padding:7px 8px;border-radius:9px;font-size:12px}.sku-matrix select.warehouse-input{width:165px}.sku-matrix input.changed,.sku-matrix select.changed{border-color:rgba(224,64,251,.75);box-shadow:0 0 0 2px rgba(224,64,251,.12)}.sku-status{display:inline-flex;padding:5px 8px;border-radius:999px;font-weight:750}.sku-status.good{background:rgba(61,220,151,.12);color:var(--good);border:1px solid rgba(61,220,151,.3)}.sku-status.warn{background:rgba(255,204,102,.12);color:var(--warn);border:1px solid rgba(255,204,102,.35)}.sku-status.bad{background:rgba(255,102,120,.12);color:var(--bad);border:1px solid rgba(255,102,120,.35)}.matrix-toolbar{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap}.matrix-toolbar .field{min-width:170px;margin-bottom:0}.matrix-impact.pos{color:var(--good);font-weight:750}.matrix-impact.neg{color:var(--bad);font-weight:750}.matrix-mode-badge{font-size:12px;color:var(--muted);padding:7px 10px;border:1px dashed var(--line);border-radius:999px}.scenario-select{min-width:230px}.volume-cell{font-weight:750;color:#cbd2df}.wb-link{display:inline-flex;gap:4px;align-items:center}.compare-panel{margin-top:10px;padding:12px;border:1px dashed var(--line);border-radius:12px;color:var(--muted);font-size:12px}.compare-panel b{color:var(--text)}

</style>
"""


DASHBOARD_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{{ title }}</title><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>{{ title }}</h1><div class="subtitle">Финансы Wildberries · история себестоимости · внешний P&amp;L</div></div><div class="badge">Последнее сохранение: {{ latest_created }}</div></header>
<nav class="nav"><a class="active" href="/">Дашборд</a><a href="/products">Товары</a><a href="/sales">История продаж</a><a href="/reconcile">Сверка</a><a href="/unit-economics">Юнит-экономика</a><a href="/unit-matrix">SKU-юнитка</a><a href="/unit-calculator">Калькулятор юнитки</a><a href="/supply-planner">Поставки</a><a href="/logistics">Логистика</a><a href="/ai-analyst">AI-аналитик</a><a href="/funnel-ads">Воронка/реклама</a><a href="/ai-chat">AI-чат</a><a href="/plan-fact">План-факт</a><a href="/admin">Себестоимость и расходы</a></nav>
<form class="filters" method="get"><select name="period_type" aria-label="Тип периода"><option value="actual" {% if selected_type == 'actual' %}selected{% endif %}>Актуально без дублей</option><option value="" {% if selected_type == '' %}selected{% endif %}>Все сохранённые периоды</option><option value="daily" {% if selected_type == 'daily' %}selected{% endif %}>Дневные и произвольные</option><option value="weekly" {% if selected_type == 'weekly' %}selected{% endif %}>Недельные</option><option value="xlsx" {% if selected_type == 'xlsx' %}selected{% endif %}>Загруженные Excel</option></select><label>С даты <input type="date" name="date_from" value="{{ date_from_value }}"></label><label>По дату <input type="date" name="date_to" value="{{ date_to_value }}"></label><select name="expense_base" aria-label="База процентов"><option value="revenue" {% if expense_base == 'revenue' %}selected{% endif %}>Расходы: % от выручки</option><option value="expenses" {% if expense_base == 'expenses' %}selected{% endif %}>Расходы: % от всех расходов</option></select><button type="submit">Показать</button><a class="button secondary" href="/?period_type=actual">С первой продажи по сейчас</a></form>{% if selected_type == 'actual' %}<div class="hint">Режим <b>«Актуально без дублей»</b>: закрытые недели берутся из недельных отчётов, а текущая незакрытая неделя — из ежедневных отчётов. Если не выбрать даты, дашборд показывает период от первой сохранённой продажи до текущего дня. Для свежих дней запусти в Telegram <b>/syncdaily</b>.</div>{% endif %}
{% if latest %}<div class="grid">
<div class="card"><div class="label">Доход покупателей</div><div class="value">{{ money(latest.revenue) }}</div></div><div class="card"><div class="label">Расчётная выплата</div><div class="value">{{ money(latest.payout) }}</div></div><div class="card"><div class="label">Прибыль до налога</div><div class="value {{ 'good' if latest.profit_before_tax >= 0 else 'bad' }}">{{ money(latest.profit_before_tax) }}</div></div><div class="card"><div class="label">УСН 6%</div><div class="value">{{ money(latest.tax) }}</div></div><div class="card"><div class="label">Чистая прибыль</div><div class="value {{ 'good' if latest.profit >= 0 else 'bad' }}">{{ money(latest.profit) }}</div></div><div class="card"><div class="label">Себестоимость</div><div class="value">{{ money(latest.cogs) }}</div></div><div class="card"><div class="label">Внешние расходы</div><div class="value">{{ money(latest.external_expenses) }}</div></div><div class="card"><div class="label">Реклама WB</div><div class="value">{{ money(latest.advertising) }}</div></div><div class="card"><div class="label">ДРР / Маржа</div><div class="value {{ margin_class(latest.margin) }}">{{ percent(latest.drr) }} / {{ percent(latest.margin) }}</div></div><div class="card"><div class="label">Продано</div><div class="value">{{ units(latest.units) }} шт.</div></div></div>

<div class="section card"><div class="section-head"><div><h2>Структура расходов</h2><div class="subtitle">Сумма и доля каждой статьи расходов по выбранному периоду</div></div><span class="muted">База: {{ expense_structure.base_label }}</span></div>
<div class="summary"><span>Выручка: {{ money(expense_structure.revenue) }}</span><span>Всего расходов в структуре: {{ money(expense_structure.total_expenses) }}</span><span>Комиссия WB не вычитается повторно: она уже сидит в сумме к перечислению.</span></div>
<div class="grid expense-grid" style="margin-top:12px">{% for row in expense_structure.rows[:10] %}<div class="card"><div class="label">{{ row.name }}</div><div class="value {{ row.cls }}">{{ money(row.amount) }}</div><div class="hint">{{ percent(row.share_revenue) }} от выручки · {{ percent(row.share_expenses) }} от расходов</div></div>{% endfor %}</div>
<div class="expense-bars">{% for row in expense_structure.rows if row.amount > 0 %}<div class="expense-bar"><div><b>{{ row.name }}</b><div class="muted" style="font-size:12px">{{ row.hint }}</div></div><div class="expense-track"><div class="expense-fill" style="width:{{ '%.2f'|format(row.bar_width) }}%"></div></div><div class="expense-note">{{ money(row.amount) }} · {{ percent(row.display_share) }}</div></div>{% endfor %}</div>
<div class="table-wrap" style="margin-top:14px"><table style="min-width:860px"><thead><tr><th>Статья</th><th>Сумма</th><th>% от выручки</th><th>% от всех расходов</th><th>Комментарий</th></tr></thead><tbody>{% for row in expense_structure.rows %}<tr><td>{{ row.name }}</td><td>{{ money(row.amount) }}</td><td>{{ percent(row.share_revenue) }}</td><td>{{ percent(row.share_expenses) }}</td><td class="muted">{{ row.hint }}</td></tr>{% endfor %}</tbody></table></div>
<div class="hint">«Основное удержание WB» = доход покупателей − расчётная выплата WB. Это нужно для прозрачности: здесь обычно находится комиссия/вознаграждение WB. В чистую прибыль оно не вычитается второй раз, потому что расчёт уже идёт от выплаты WB.</div></div>

<div class="section card"><div class="section-head"><div><h2>Динамика сохранённых отчётов</h2><div class="subtitle">Доход, прибыль до налога и чистая прибыль</div></div></div><div class="chart-box"><canvas id="trendChart"></canvas></div></div>
<div class="section two-col"><div class="card"><div class="section-head"><h2>Товары выбранного периода</h2><span class="muted">{{ latest_period }}</span></div><div class="table-wrap"><table><thead><tr><th>Товар</th><th>Артикул</th><th>Шт.</th><th>Доход</th><th>Себес.</th><th>Внешние</th><th>Реклама</th><th>До налога</th><th>УСН</th><th>Чистая</th><th>Маржа</th></tr></thead><tbody>{% for row in skus %}<tr><td><a class="text-link" href="{{ url_for('product_detail', sku=row.sku, period_type=latest.period_type) }}">{{ row.name or row.sku }}</a></td><td class="muted">{{ row.sku }}</td><td>{{ units(row.units) }}</td><td>{{ money(row.revenue) }}</td><td>{{ money(row.cogs) }}</td><td>{{ money(row.external_expenses) }}</td><td>{{ money(row.advertising) }}</td><td class="{{ 'profit-pos' if row.profit_before_tax >= 0 else 'profit-neg' }}">{{ money(row.profit_before_tax) }}</td><td>{{ money(row.tax) }}</td><td class="{{ 'profit-pos' if row.profit >= 0 else 'profit-neg' }}">{{ money(row.profit) }}</td><td>{{ percent(row.margin) }}</td></tr>{% else %}<tr><td colspan="11" class="empty">В этом отчёте нет строк по товарам.</td></tr>{% endfor %}</tbody></table></div><div class="hint">Себестоимость и внешние расходы теперь редактируются прямо в разделе <b>«Себестоимость и расходы»</b>. После изменения прошлые отчёты пересчитываются командой <b>/backfill ДД.ММ.ГГГГ</b>.</div></div>
<div class="card"><div class="section-head"><h2>Последние периоды</h2><span class="muted">Нажми на период</span></div><div class="table-wrap"><table style="min-width:760px"><thead><tr><th>Период</th><th>Тип</th><th>Доход</th><th>До налога</th><th>УСН</th><th>Чистая</th></tr></thead><tbody>{% for row in reports_desc %}<tr><td><a class="period-link" href="/?report_id={{ row.report_id }}{% if selected_type %}&period_type={{ selected_type }}{% endif %}">{{ period(row.period_start,row.period_end) }}</a></td><td class="muted">{{ type_label(row.period_type) }}</td><td>{{ money(row.revenue) }}</td><td class="{{ 'profit-pos' if row.profit_before_tax >= 0 else 'profit-neg' }}">{{ money(row.profit_before_tax) }}</td><td>{{ money(row.tax) }}</td><td class="{{ 'profit-pos' if row.profit >= 0 else 'profit-neg' }}">{{ money(row.profit) }}</td></tr>{% endfor %}</tbody></table></div></div></div>
{% else %}<div class="card empty">В базе пока нет отчётов. Отправь боту <b>/yesterday</b>, <b>/week</b> или <b>/month</b>, затем обнови страницу.</div>{% endif %}<footer>WB Profit Dashboard · данные доступны только после авторизации</footer></div>
{% if latest %}<script>const labels={{ chart_labels|tojson }},profit={{ chart_profit|tojson }},preTax={{ chart_pre_tax|tojson }},revenue={{ chart_revenue|tojson }};const canvas=document.getElementById('trendChart');if(window.Chart&&canvas){new Chart(canvas,{data:{labels,datasets:[{type:'bar',label:'Чистая прибыль',data:profit,borderWidth:0,borderRadius:6,backgroundColor:'rgba(61,220,151,.70)',yAxisID:'y'},{type:'line',label:'Прибыль до налога',data:preTax,borderColor:'#ffcc66',backgroundColor:'rgba(255,204,102,.08)',fill:false,tension:.28,pointRadius:3,yAxisID:'y'},{type:'line',label:'Доход покупателей',data:revenue,borderColor:'#c77dff',backgroundColor:'rgba(199,125,255,.14)',fill:true,tension:.28,pointRadius:3,yAxisID:'y1'}]},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{labels:{color:'#cbd2df'}}},scales:{x:{ticks:{color:'#9aa3b5',maxRotation:40,minRotation:0},grid:{color:'rgba(255,255,255,.05)'}},y:{position:'left',ticks:{color:'#9aa3b5'},grid:{color:'rgba(255,255,255,.05)'}},y1:{position:'right',ticks:{color:'#9aa3b5'},grid:{drawOnChartArea:false}}}}});}</script>{% endif %}</body></html>
"""



PRODUCTS_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Товары · {{ title }}</title>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>Аналитика по товарам</h1><div class="subtitle">Прибыль, маржа и ДРР каждого SKU за выбранные сохранённые периоды</div></div><div class="badge">Найдено товаров: {{ totals.products }}</div></header>
<nav class="nav"><a href="/">Дашборд</a><a class="active" href="/products">Товары</a><a href="/sales">История продаж</a><a href="/reconcile">Сверка</a><a href="/unit-economics">Юнит-экономика</a><a href="/unit-matrix">SKU-юнитка</a><a href="/unit-calculator">Калькулятор юнитки</a><a href="/supply-planner">Поставки</a><a href="/logistics">Логистика</a><a href="/ai-analyst">AI-аналитик</a><a href="/funnel-ads">Воронка/реклама</a><a href="/ai-chat">AI-чат</a><a href="/plan-fact">План-факт</a><a href="/admin">Себестоимость и расходы</a></nav>
<form class="filters" method="get"><label>Периоды<select name="period_type"><option value="weekly" {% if selected_type == 'weekly' %}selected{% endif %}>Недельные</option><option value="daily" {% if selected_type == 'daily' %}selected{% endif %}>Дневные и произвольные</option><option value="xlsx" {% if selected_type == 'xlsx' %}selected{% endif %}>Excel</option></select></label><label>С даты<input type="date" name="date_from" value="{{ date_from_value }}"></label><label>По дату<input type="date" name="date_to" value="{{ date_to_value }}"></label><label>Поиск<input name="q" value="{{ query }}" placeholder="Название, артикул или nmID"></label><button type="submit">Показать</button>{% if query or date_from_value or date_to_value %}<a class="button secondary" href="/products?period_type={{ selected_type }}">Сбросить</a>{% endif %}</form>
<div class="hint">Чтобы суммы не задваивались, здесь одновременно используется только один тип периодов. По умолчанию выбраны закрытые недельные отчёты.</div>
{% if rows %}<div class="grid section"><div class="card"><div class="label">Товаров</div><div class="value">{{ totals.products }}</div></div><div class="card"><div class="label">Продано</div><div class="value">{{ units(totals.units) }} шт.</div></div><div class="card"><div class="label">Доход покупателей</div><div class="value">{{ money(totals.revenue) }}</div></div><div class="card"><div class="label">Прибыль до налога</div><div class="value {{ 'good' if totals.profit_before_tax >= 0 else 'bad' }}">{{ money(totals.profit_before_tax) }}</div></div><div class="card"><div class="label">Чистая прибыль</div><div class="value {{ 'good' if totals.profit >= 0 else 'bad' }}">{{ money(totals.profit) }}</div></div><div class="card"><div class="label">Маржа</div><div class="value {{ margin_class(totals.margin) }}">{{ percent(totals.margin) }}</div></div><div class="card"><div class="label">Реклама WB</div><div class="value">{{ money(totals.advertising) }}</div></div><div class="card"><div class="label">ДРР</div><div class="value">{{ percent(totals.drr) }}</div></div><div class="card"><div class="label">Себестоимость</div><div class="value">{{ money(totals.cogs) }}</div></div><div class="card"><div class="label">Внешние расходы</div><div class="value">{{ money(totals.external_expenses) }}</div></div></div>
<div class="section card"><div class="section-head"><div><h2>Экономика товаров</h2><div class="subtitle">Нажми на товар, чтобы открыть его график и историю по периодам</div></div><span class="muted">{{ type_label(selected_type) }}</span></div><div class="table-wrap"><table style="min-width:1430px"><thead><tr><th>Товар</th><th>Артикул</th><th>Периодов</th><th>Шт.</th><th>Доход</th><th>Выплата</th><th>Себес.</th><th>Внешние</th><th>Реклама</th><th>До налога</th><th>УСН</th><th>Чистая</th><th>Прибыль / шт.</th><th>Маржа</th><th>ДРР</th><th>Последний период</th></tr></thead><tbody>{% for row in rows %}<tr><td><a class="text-link" href="{{ url_for('product_detail', sku=row.sku, period_type=selected_type, date_from=date_from_value, date_to=date_to_value) }}">{{ row.name or row.sku }}</a></td><td class="muted">{{ row.sku }}</td><td>{{ row.periods }}</td><td>{{ units(row.units) }}</td><td>{{ money(row.revenue) }}</td><td>{{ money(row.payout) }}</td><td>{{ money(row.cogs) }}</td><td>{{ money(row.external_expenses) }}</td><td>{{ money(row.advertising) }}</td><td class="{{ 'profit-pos' if row.profit_before_tax >= 0 else 'profit-neg' }}">{{ money(row.profit_before_tax) }}</td><td>{{ money(row.tax) }}</td><td class="{{ 'profit-pos' if row.profit >= 0 else 'profit-neg' }}">{{ money(row.profit) }}</td><td>{{ money(row.profit / row.units if row.units else 0) }}</td><td>{{ percent(row.margin) }}</td><td>{{ percent(row.drr) }}</td><td class="muted">{{ date_display(row.last_period) }}</td></tr>{% endfor %}</tbody></table></div></div>{% else %}<div class="card empty section">По выбранным фильтрам товары не найдены. Сначала сохрани отчёты через /week или /backfill.</div>{% endif %}
<footer>WB Profit Dashboard · аналитика строится по сохранённым данным PostgreSQL</footer></div></body></html>
"""


UNIT_MATRIX_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>SKU-юнитка · WB Profit</title>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>Интерактивная юнит-экономика по SKU</h1><div class="subtitle">Excel-like таблица: факт WB отдельно от сценария, цена с СПП, схема FBS/FBW, ручные FBS-склады, тарифы, фото и ссылки WB прямо в строке</div></div><div class="badge">Факт + сценарии · v40</div></header>
<nav class="nav"><a href="/">Дашборд</a><a href="/products">Товары</a><a href="/sales">История продаж</a><a href="/reconcile">Сверка</a><a href="/unit-economics">Юнит-экономика</a><a class="active" href="/unit-matrix">SKU-юнитка</a><a href="/unit-calculator">Калькулятор юнитки</a><a href="/supply-planner">Поставки</a><a href="/logistics">Логистика</a><a href="/ai-analyst">AI-аналитик</a><a href="/funnel-ads">Воронка/реклама</a><a href="/plan-fact">План-факт</a><a href="/admin">Себестоимость и расходы</a></nav>
<form class="filters" method="get"><label>Период<select name="period_type"><option value="actual" {% if selected_type == 'actual' %}selected{% endif %}>Актуально без дублей</option><option value="weekly" {% if selected_type == 'weekly' %}selected{% endif %}>Недельные</option><option value="daily" {% if selected_type == 'daily' %}selected{% endif %}>Дневные</option><option value="xlsx" {% if selected_type == 'xlsx' %}selected{% endif %}>Excel</option></select></label><label>С даты<input type="date" name="date_from" value="{{ date_from_value }}"></label><label>По дату<input type="date" name="date_to" value="{{ date_to_value }}"></label><label>Поиск<input name="q" value="{{ query }}" placeholder="товар / артикул / nmID"></label><label>Целевая маржа, %<input id="targetMarginInput" name="target_margin" inputmode="decimal" value="{{ target_margin_input }}"></label><button type="submit">Показать</button></form>
<div class="hint">Фактический режим показывает базовую юнит-экономику из сохранённых отчётов. Изменения в таблице — это <b>сценарий</b>: они не затирают данные WB и не меняют себестоимость в базе, пока ты отдельно не сохранишь её в разделе «Себестоимость и расходы».</div>
{% if tariff_warning %}<div class="notice error">{{ tariff_warning }}</div>{% endif %}
<div class="section card"><div class="section-head"><div><h2>Управление сценарием</h2><div class="subtitle">Меняй ячейки — прибыль, маржа, ROI и минимальная цена пересчитаются сразу без перезагрузки</div></div><span id="scenarioStatus" class="matrix-mode-badge">Фактическая база</span></div>
<div class="matrix-toolbar"><div class="field"><label>Фильтр категории</label><select id="filterCategory"><option value="">Все</option>{% for c in categories %}<option value="{{ c }}">{{ c }}</option>{% endfor %}</select></div><div class="field"><label>Фильтр бренда</label><select id="filterBrand"><option value="">Все</option>{% for b in brands %}<option value="{{ b }}">{{ b }}</option>{% endfor %}</select></div><div class="field"><label>Фильтр склада</label><select id="filterWarehouse"><option value="">Все</option>{% for wh in warehouse_options %}<option value="{{ wh }}">{{ wh }}</option>{% endfor %}</select></div><div class="field"><label>Статус</label><select id="filterStatus"><option value="">Все</option><option value="good">Прибыльный</option><option value="warn">На грани</option><option value="bad">Убыточный</option></select></div><div class="field"><label>Мин. маржа, %</label><input id="filterMargin" inputmode="decimal" placeholder="например 10"></div></div>
<div class="matrix-toolbar" style="margin-top:10px"><div class="field"><label>Название сценария</label><input id="scenarioName" placeholder="Например: Краснодар · ДРР 15%"></div><button type="button" class="secondary" id="saveScenarioBtn">Сохранить сценарий</button><div class="field"><label>Загрузить сценарий</label><select id="scenarioSelect" class="scenario-select"><option value="">— сохранённые сценарии —</option></select></div><button type="button" class="secondary" id="loadScenarioBtn">Загрузить</button><button type="button" class="danger" id="deleteScenarioBtn">Удалить</button><div class="field"><label>Сравнить с другим</label><select id="compareScenarioSelect" class="scenario-select"><option value="">— выбрать сценарий —</option></select></div><button type="button" id="compareScenarioBtn">Сравнить сценарии</button><button type="button" class="secondary" id="resetScenarioBtn">Сбросить изменения</button><button type="button" class="secondary" id="restoreWbBtn">Вернуть данные из WB</button><button type="button" class="secondary" id="applyCalcScenariosBtn">Подтянуть из калькулятора</button><button type="button" class="secondary" id="fillMissingCalcScenariosBtn">Заполнить пустые из калькулятора</button><button type="button" id="compareBtn">Сравнить с базой</button></div><div id="scenarioCompare" class="compare-panel" style="display:none"></div><div class="hint" style="margin-top:10px"><b>Связка с калькулятором:</b> кнопка «Подтянуть из калькулятора» берёт сохранённые сценарии из раздела «Калькулятор юнитки» и заполняет строки SKU по совпадению артикула/nmID/названия. Это сценарный расчёт, фактические данные WB не затираются.</div></div>
<div class="grid section"><div class="card"><div class="label">SKU в таблице</div><div class="value" id="visibleCount">{{ rows|length }}</div></div><div class="card"><div class="label">Сценарная прибыль</div><div class="value good" id="totalProfit">0 ₽</div></div><div class="card"><div class="label">Влияние на прибыль</div><div class="value" id="totalImpact">0 ₽</div></div><div class="card"><div class="label">Средняя маржа</div><div class="value" id="avgMargin">0%</div></div><div class="card"><div class="label">Убыточных / на грани</div><div class="value" id="badCount">0 / 0</div></div></div>
<div class="section card"><div class="section-head"><div><h2>Таблица SKU</h2><div class="subtitle">Первая колонка закреплена. Цена считается как в калькуляторе юнитки: вводишь цену для покупателя с СПП и СПП %, система считает цену до СПП. Склад выбирается прямо в строке; для FBS можно выбрать ручной СЦ, например СЦ Владикавказ.</div></div><span class="muted">{{ period_label }}</span></div><div class="sku-matrix-wrap"><table class="sku-matrix" id="unitMatrix"><thead><tr><th class="sticky-product">Товар</th><th>Артикул WB / SKU</th><th>SKU / размер / цвет</th><th>Категория</th><th>Схема</th><th>Цена с СПП</th><th>СПП, %</th><th>Цена до СПП</th><th>Себестоимость</th><th>Комиссия WB, %</th><th>Комиссия WB, ₽</th><th>Склад</th><th>Длина, см</th><th>Ширина, см</th><th>Высота, см</th><th>Объём, л</th><th>Логистика, ₽</th><th>Хранение, ₽</th><th>Реклама / ДРР</th><th>Налог, %</th><th>Прочие расходы</th><th>Чистая прибыль, ₽</th><th>Маржа, %</th><th>ROI, %</th><th>Точка безуб.</th><th>Мин. цена</th><th>Цена для маржи</th><th>Влияние</th><th>Статус</th></tr></thead><tbody>
{% for row in rows %}<tr data-row='{{ row.json|safe }}' data-category="{{ row.category }}" data-brand="{{ row.brand }}"><td class="sticky-product"><div class="sku-product-cell"><a href="{{ row.product_url }}" target="_blank" rel="noopener"><img class="sku-img" src="{{ row.photo_url }}" onerror="this.style.display='none'"></a><div><div class="sku-name">{% if row.product_url %}<a class="text-link" href="{{ row.product_url }}" target="_blank" rel="noopener">{{ row.name }}</a>{% else %}{{ row.name }}{% endif %}</div><div class="sku-meta">nmID {{ row.nm_id or '—' }} · факт: {{ units(row.units) }} шт.{% if row.product_url %} · <a class="text-link" href="{{ row.product_url }}" target="_blank" rel="noopener">открыть WB</a>{% endif %}</div></div></div></td><td>{{ row.sku }}</td><td class="muted">{{ row.variant }}</td><td>{{ row.category }}</td><td><select class="js-field scheme-input" data-key="scheme"><option value="fbs">FBS</option><option value="fbw">FBW</option></select></td><td><input class="js-field" data-key="priceSpp" inputmode="decimal"></td><td><input class="js-field" data-key="sppPct" inputmode="decimal"></td><td class="js-priceBeforeSpp">0 ₽</td><td><input class="js-field" data-key="cost" inputmode="decimal"></td><td><input class="js-field" data-key="commissionPct" inputmode="decimal"></td><td class="js-commissionRub">0 ₽</td><td><select class="js-field warehouse-input" data-key="warehouse"><option value="">—</option>{% for wh in warehouse_options %}<option value="{{ wh }}">{{ wh }}</option>{% endfor %}<option value="__fbs_vladikavkaz__">СЦ Владикавказ · ручной FBS</option><option value="__fbs_manual__">Мой FBS-склад вручную</option></select></td><td><input class="js-field dimension-input" data-key="lengthCm" inputmode="decimal"></td><td><input class="js-field dimension-input" data-key="widthCm" inputmode="decimal"></td><td><input class="js-field dimension-input" data-key="heightCm" inputmode="decimal"></td><td class="js-volume volume-cell">0</td><td><input class="js-field" data-key="logistics" inputmode="decimal"></td><td><input class="js-field" data-key="storage" inputmode="decimal"></td><td><input class="js-field" data-key="drr" inputmode="decimal"></td><td><input class="js-field" data-key="taxPct" inputmode="decimal"></td><td><input class="js-field" data-key="other" inputmode="decimal"></td><td class="js-profit">0 ₽</td><td class="js-margin">0%</td><td class="js-roi">0%</td><td class="js-breakEven">0 ₽</td><td class="js-minPrice">0 ₽</td><td class="js-targetPrice">0 ₽</td><td class="js-impact matrix-impact">0 ₽</td><td><span class="sku-status js-status">—</span></td></tr>{% else %}<tr><td colspan="29" class="empty">Нет товаров за выбранный период.</td></tr>{% endfor %}
</tbody></table></div><div class="hint">Формула SKU-юнитки теперь синхронизирована с калькулятором юнитки: цена до СПП = цена для покупателя / (1 − СПП%). Комиссия WB, ДРР и налог считаются от цены до СПП. Прямая логистика считается отдельно, а процент выкупа влияет только на нагрузку обратной логистики/невыкупа, если SKU заполнен из сохранённого сценария калькулятора. Для FBS-СЦ, которых нет в тарифах WB, выбирай ручной склад и вводи тарифы в ячейках логистики/хранения. Габариты считаются как Д×Ш×В/1000; логистика и хранение по тарифу WB пересчитываются от фактического объёма без округления литража. Например 1,57 л считается как 1 первый литр + 0,57 дополнительного литра.</div></div>
<script>
const tariffMaps = {{ tariff_maps|tojson }};
const initialScenarios = {{ matrix_scenarios|tojson }};
const calcScenarios = {{ calc_scenarios|tojson }};
const moneyFmt = new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2});
const num = (v)=>{ if(v===null||v===undefined) return 0; if(typeof v==='number') return v; const s=String(v).replace(/\s/g,'').replace('%','').replace(',','.'); const n=parseFloat(s); return Number.isFinite(n)?n:0; };
const money = (v)=> moneyFmt.format(v).replace(',', '.') + ' ₽';
const pct = (v)=> (Math.round(v*10)/10).toString().replace('.', ',') + '%';
const rows = Array.from(document.querySelectorAll('#unitMatrix tbody tr[data-row]'));
let savedScenarios = initialScenarios || [];
const DEFAULT_EXTRAS = {buyoutPct:1, returnLogistics:0, acquiringPct:0, vatPct:0};
function cloneExtras(x){ return Object.assign({}, DEFAULT_EXTRAS, x||{}); }
function setInput(tr,key,value){ const el=tr.querySelector(`[data-key="${key}"]`); if(!el) return; el.value = (typeof value === 'number') ? (Math.round(value*100)/100).toString().replace('.', ',') : (value||''); el.dataset.base = el.value; }
function volumeLiters(v){ return Math.max(0,(v.lengthCm||0)*(v.widthCm||0)*(v.heightCm||0)/1000); }
function chargeLiters(vol){ return Math.max(1, vol || 0); }
function hydrate(){ rows.forEach(tr=>{ const d=JSON.parse(tr.dataset.row); tr._base=d; tr._extras=cloneExtras(d.extras||{}); setInput(tr,'scheme',d.scheme||'fbs'); setInput(tr,'priceSpp',d.priceSpp ?? d.price); setInput(tr,'sppPct',(d.sppPct||0)*100); setInput(tr,'cost',d.cost); setInput(tr,'commissionPct',d.commissionPct*100); setInput(tr,'warehouse',d.warehouse||''); setInput(tr,'lengthCm',d.lengthCm||0); setInput(tr,'widthCm',d.widthCm||0); setInput(tr,'heightCm',d.heightCm||0); setInput(tr,'logistics',d.logistics); setInput(tr,'storage',d.storage); setInput(tr,'drr',d.drr*100); setInput(tr,'taxPct',d.taxPct*100); setInput(tr,'other',d.other); recalcRow(tr); }); applyFilters(); renderScenarioSelects(); }
function values(tr){ const priceSpp=num(tr.querySelector('[data-key="priceSpp"]').value); const sppPct=num(tr.querySelector('[data-key="sppPct"]').value)/100; const denom=Math.max(0.0001,1-sppPct); const priceBeforeSpp=priceSpp/denom; const lengthCm=num(tr.querySelector('[data-key="lengthCm"]')?.value); const widthCm=num(tr.querySelector('[data-key="widthCm"]')?.value); const heightCm=num(tr.querySelector('[data-key="heightCm"]')?.value); return {scheme:tr.querySelector('[data-key="scheme"]').value || 'fbs', priceSpp, sppPct, price:priceBeforeSpp, cost:num(tr.querySelector('[data-key="cost"]').value), commissionPct:num(tr.querySelector('[data-key="commissionPct"]').value)/100, warehouse:tr.querySelector('[data-key="warehouse"]').value, lengthCm, widthCm, heightCm, volume: Math.max(0,lengthCm*widthCm*heightCm/1000), logistics:num(tr.querySelector('[data-key="logistics"]').value), storage:num(tr.querySelector('[data-key="storage"]').value), drr:num(tr.querySelector('[data-key="drr"]').value)/100, taxPct:num(tr.querySelector('[data-key="taxPct"]').value)/100, other:num(tr.querySelector('[data-key="other"]').value)}; }
function calc(v,targetMargin,extras){ const x=cloneExtras(extras); const buyout=Math.max(0.0001, Math.min(1, num(x.buyoutPct||1))); const commissionRub=v.price*v.commissionPct; const adRub=v.price*v.drr; const taxRub=v.price*v.taxPct; const acquiringRub=v.price*num(x.acquiringPct||0); const vatRate=Math.max(0, num(x.vatPct||0)); const vatRub=vatRate>0 ? v.price*(vatRate/(1+vatRate)) : 0; const returnBurden=num(x.returnLogistics||0)*(1-buyout)/buyout; const logisticsTotal=v.logistics+returnBurden; const wbExpenses=commissionRub+logisticsTotal+v.storage+acquiringRub; const directCosts=v.cost+v.other; const profitBeforeTax=v.price-wbExpenses-directCosts-adRub; const profit=profitBeforeTax-taxRub-vatRub; const margin=v.price?profit/v.price*100:0; const roiBase=Math.max(0.0001, v.cost); const roi=roiBase?profit/roiBase*100:0; const percentNoDrr=v.commissionPct+v.taxPct+num(x.acquiringPct||0)+(vatRate>0 ? vatRate/(1+vatRate) : 0); const fixed=v.cost+logisticsTotal+v.storage+v.other; const breakEven=(1-percentNoDrr-v.drr)>0.0001?fixed/(1-percentNoDrr-v.drr):0; const targetPrice=(1-percentNoDrr-v.drr-targetMargin)>0.0001?fixed/(1-percentNoDrr-v.drr-targetMargin):0; return {commissionRub,adRub,taxRub,acquiringRub,vatRub,returnBurden,logisticsTotal,profitBeforeTax,profit,margin,roi,breakEven,minPrice:breakEven,targetPrice}; }
function recalcRow(tr){ const targetMargin=num(document.getElementById('targetMarginInput').value)/100; const v=values(tr); const c=calc(v,targetMargin,tr._extras||DEFAULT_EXTRAS); const base=tr._base||{}; const impact=c.profit-(base.baseProfit??0); tr.querySelector('.js-volume').textContent=(Math.round(v.volume*1000)/1000).toString().replace('.', ','); tr.querySelector('.js-priceBeforeSpp').textContent=money(v.price); tr.querySelector('.js-commissionRub').textContent=money(c.commissionRub); tr.querySelector('.js-profit').textContent=money(c.profit); tr.querySelector('.js-profit').className='js-profit '+(c.profit>=0?'profit-pos':'profit-neg'); tr.querySelector('.js-margin').textContent=pct(c.margin); tr.querySelector('.js-margin').className='js-margin '+(c.margin>=10?'profit-pos':c.margin>=0?'value warn':'profit-neg'); tr.querySelector('.js-roi').textContent=pct(c.roi); tr.querySelector('.js-breakEven').textContent=money(c.breakEven); tr.querySelector('.js-minPrice').textContent=money(c.minPrice); tr.querySelector('.js-targetPrice').textContent=money(c.targetPrice); tr.querySelector('.js-impact').textContent=(impact>=0?'+':'')+money(impact); tr.querySelector('.js-impact').className='js-impact matrix-impact '+(impact>=0?'pos':'neg'); const st=tr.querySelector('.js-status'); let status='Прибыльный', cls='good'; if(c.profit<0){status='Убыточный';cls='bad'} else if(c.margin<5){status='На грани';cls='warn'} st.textContent=status; st.className='sku-status js-status '+cls; tr.dataset.status=cls; tr.dataset.margin=String(c.margin); tr.dataset.warehouse=v.warehouse; tr.dataset.scheme=v.scheme; tr._calc=c; tr.querySelectorAll('.js-field').forEach(el=>{el.classList.toggle('changed', String(el.value)!==String(el.dataset.base||''));}); }
function recalcAll(){ rows.forEach(recalcRow); updateTotals(); applyFilters(false); }
function aggregateTotals(sourceRows=rows){ let visible=0, profit=0, impact=0, revenue=0, bad=0, warn=0; sourceRows.forEach(tr=>{ if(tr.style.display==='none') return; visible++; const c=tr._calc||{}; const v=values(tr); profit+=c.profit||0; impact+=(c.profit-(tr._base?.baseProfit||0)); revenue+=v.price; if(tr.dataset.status==='bad') bad++; if(tr.dataset.status==='warn') warn++;}); return {visible,profit,impact,revenue,bad,warn,margin:revenue?profit/revenue*100:0}; }
function updateTotals(){ const t=aggregateTotals(); document.getElementById('visibleCount').textContent=t.visible; document.getElementById('totalProfit').textContent=money(t.profit); document.getElementById('totalImpact').textContent=(t.impact>=0?'+':'')+money(t.impact); document.getElementById('totalImpact').className='value '+(t.impact>=0?'good':'bad'); document.getElementById('avgMargin').textContent=pct(t.margin); document.getElementById('badCount').textContent=`${t.bad} / ${t.warn}`; }
function tariffFor(v){ if(v.warehouse==='__fbs_vladikavkaz__' || v.warehouse==='__fbs_manual__') return null; const map=tariffMaps[v.scheme] || {}; return map[v.warehouse] || null; }
function applyTariff(tr){ const v=values(tr); const t=tariffFor(v); if(!t) { recalcRow(tr); updateTotals(); return; } const liters=chargeLiters(v.volume); const logRub=(t.base||0) + Math.max(0,liters-1)*(t.liter||0); const storageRub=(t.storageBase||0) + Math.max(0,liters-1)*(t.storageLiter||0); const log=tr.querySelector('[data-key="logistics"]'); const st=tr.querySelector('[data-key="storage"]'); log.value=String(Math.round(logRub*100)/100).replace('.', ','); st.value=String(Math.round(storageRub*100)/100).replace('.', ','); recalcRow(tr); updateTotals(); }
function applyFilters(doTotals=true){ const fc=document.getElementById('filterCategory').value, fb=document.getElementById('filterBrand').value, fw=document.getElementById('filterWarehouse').value, fs=document.getElementById('filterStatus').value, fm=num(document.getElementById('filterMargin').value); rows.forEach(tr=>{let ok=true; if(fc&&tr.dataset.category!==fc) ok=false; if(fb&&tr.dataset.brand!==fb) ok=false; if(fw&&tr.dataset.warehouse!==fw) ok=false; if(fs&&tr.dataset.status!==fs) ok=false; if(fm&&num(tr.dataset.margin)<fm) ok=false; tr.style.display=ok?'':'none';}); if(doTotals) updateTotals(); }
function currentPayload(){ return {version:44, savedAt:new Date().toISOString(), targetMargin:num(document.getElementById('targetMarginInput').value), rows:rows.map(tr=>({sku:tr._base.sku, nmId:tr._base.nmId, name:tr.querySelector('.sku-name')?.textContent?.trim()||tr._base.sku, values:values(tr), extras:cloneExtras(tr._extras||{}), calc:tr._calc, baseProfit:tr._base.baseProfit||0}))}; }
function renderScenarioSelects(){ const selects=[document.getElementById('scenarioSelect'), document.getElementById('compareScenarioSelect')]; selects.forEach(sel=>{ if(!sel) return; const cur=sel.value; sel.innerHTML='<option value="">— выбрать сценарий —</option>' + savedScenarios.map(x=>`<option value="${x.id}">${escapeHtml(x.name)} · ${escapeHtml(x.updated_at||'')}</option>`).join(''); sel.value=cur; }); }
function escapeHtml(s){ return String(s||'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
async function api(url, options={}){ const resp=await fetch(url,{headers:{'Content-Type':'application/json'},...options}); if(!resp.ok){let msg='Ошибка запроса'; try{msg=(await resp.json()).error||msg}catch(e){} throw new Error(msg)} if(resp.status===204) return {}; return await resp.json(); }
async function loadScenarioList(){ try{ const data=await api('/api/unit-matrix-scenarios'); savedScenarios=data.items||[]; renderScenarioSelects(); }catch(e){ document.getElementById('scenarioStatus').textContent='Не удалось загрузить сценарии: '+e.message; } }
function applyScenarioPayload(payload){ if(!payload || !payload.rows) return; const bySku=new Map(payload.rows.map(r=>[String(r.sku||'').toLowerCase(), r])); rows.forEach(tr=>{ const row=bySku.get(String(tr._base.sku||'').toLowerCase()); if(!row) return; const v=row.values||{}; Object.entries(v).forEach(([k,val])=>{ if(k==='price' || k==='extras') return; const el=tr.querySelector(`[data-key="${k}"]`); if(el){ el.value=(typeof val==='number')?(Math.round(val*100)/100).toString().replace('.', ','):(val||''); } }); tr._extras = cloneExtras(row.extras || row.values?.extras || tr._extras || {}); recalcRow(tr); }); updateTotals(); applyFilters(false); }
async function getScenario(id){ return await api('/api/unit-matrix-scenarios/'+id); }
function scenarioTotals(payload){ if(!payload || !payload.rows) return {profit:0,revenue:0,margin:0,bad:0,warn:0}; let profit=0,revenue=0,bad=0,warn=0; payload.rows.forEach(r=>{ const c=r.calc||{}; const v=r.values||{}; profit+=num(c.profit); revenue+=num(v.price); if(num(c.profit)<0) bad++; else if(num(c.margin)<5) warn++; }); return {profit,revenue,margin:revenue?profit/revenue*100:0,bad,warn}; }
function showCompare(title, a, b){ const ta=scenarioTotals(a), tb=scenarioTotals(b); const diff=ta.profit-tb.profit; const el=document.getElementById('scenarioCompare'); el.style.display='block'; el.innerHTML=`<b>${escapeHtml(title)}</b><br>Сценарий: <b>${money(ta.profit)}</b>, маржа ${pct(ta.margin)} · База/сравнение: <b>${money(tb.profit)}</b>, маржа ${pct(tb.margin)} · Разница: <b class="${diff>=0?'profit-pos':'profit-neg'}">${diff>=0?'+':''}${money(diff)}</b>`; }

function calcScenarioValuesFromQuery(query){
  const p=new URLSearchParams(query||'');
  const get=(k,d='')=>p.get(k) ?? d;
  const n=(k,d=0)=>num(get(k,d));
  const rate=(k,d=0)=>{ const v=n(k,d); return v>1 ? v/100 : v; };
  const priceSpp=n('price_spp',0);
  const sppPct=rate('spp_pct',0);
  const buyout=Math.max(0.0001, Math.min(1, rate('buyout_pct',1)));
  const lengthCm=n('length_cm',0), widthCm=n('width_cm',0), heightCm=n('height_cm',0);
  const volume=Math.max(0,lengthCm*widthCm*heightCm/1000);
  const baseLog=n('base_logistics',0) + Math.max(0, volume-1)*n('extra_liter_cost',0);
  const locCoef = Math.max(0.0001, n('localization_pct',1) || 1);
  const outbound=baseLog * locCoef;
  const purchase=n('purchase_price',0);
  const other=n('acceptance',0)+n('mp_delivery',0)+n('packaging_cost',0)+n('tariff_options',0)+n('other_cost',0)+purchase*rate('defect_pct',0);
  const storage=n('turnover_days',0)*n('storage_per_day',0);
  const wh=(get('warehouse','')||'').trim();
  let warehouse=wh;
  const whMode=(get('warehouse_mode','api')||'api').toLowerCase();
  if(whMode==='manual'){
    if(wh.toLowerCase().includes('владикавказ')) warehouse='__fbs_vladikavkaz__';
    else if(wh) warehouse='__calc_manual__:'+wh;
    else warehouse='__fbs_manual__';
  }
  return {
    sku:(get('sku','')||'').trim(),
    productName:(get('product_name','')||'').trim(),
    scheme:(get('scheme','fbs')||'fbs').toLowerCase()==='fbw'?'fbw':'fbs',
    priceSpp: priceSpp,
    sppPct: sppPct*100,
    cost: purchase,
    commissionPct: rate('commission_pct',0)*100,
    warehouse: warehouse,
    manualWarehouseName: wh,
    lengthCm, widthCm, heightCm,
    logistics: outbound,
    storage: storage,
    drr: rate('drr_pct',0)*100,
    taxPct: rate('tax_pct',0.06)*100,
    other: other,
    extras: {
      buyoutPct: buyout,
      returnLogistics: n('return_logistics',0),
      acquiringPct: rate('acquiring_pct',0),
      vatPct: rate('vat_pct',0),
    },
  };
}
function normKey(s){ return String(s||'').toLowerCase().replace(/ё/g,'е').replace(/\s+/g,' ').trim(); }
function rowMatchScore(tr, sc){
  const b=tr._base||{};
  const rowSku=normKey(b.sku);
  const rowName=normKey(b.name || tr.querySelector('.sku-name')?.textContent || '');
  const rowNm=String(b.nmId||'');
  const scSku=normKey(sc.sku);
  const scName=normKey(sc.productName);
  if(scSku && rowSku && scSku===rowSku) return 100;
  if(scSku && rowNm && scSku===rowNm) return 95;
  if(scName && rowName && scName===rowName) return 90;
  if(scName && rowName && (rowName.includes(scName) || scName.includes(rowName))) return 60;
  if(scSku && rowSku && (rowSku.includes(scSku) || scSku.includes(rowSku))) return 55;
  return 0;
}
function ensureWarehouseOption(tr,value,label){
  const sel=tr.querySelector('[data-key="warehouse"]'); if(!sel || !value) return;
  if(!Array.from(sel.options).some(o=>o.value===value)){
    const opt=document.createElement('option'); opt.value=value; opt.textContent=label||value.replace('__calc_manual__:',''); sel.appendChild(opt);
  }
}
function applyCalcScenarioToRow(tr, sc, onlyMissing=false){
  const keys=['scheme','priceSpp','sppPct','cost','commissionPct','warehouse','lengthCm','widthCm','heightCm','logistics','storage','drr','taxPct','other'];
  if(sc.warehouse && String(sc.warehouse).startsWith('__calc_manual__:')) ensureWarehouseOption(tr, sc.warehouse, sc.manualWarehouseName||sc.warehouse.replace('__calc_manual__:',''));
  keys.forEach(key=>{
    const val=sc[key];
    if(val===undefined || val===null || val==='') return;
    const el=tr.querySelector(`[data-key="${key}"]`); if(!el) return;
    if(onlyMissing && num(el.value)!==0 && String(el.value||'').trim()!=='') return;
    el.value=(typeof val==='number') ? (Math.round(val*100)/100).toString().replace('.', ',') : val;
    el.classList.add('changed');
  });
  tr._extras = cloneExtras(Object.assign({}, tr._extras||{}, sc.extras||{}));
  recalcRow(tr);
}
function applyCalcScenarios(onlyMissing=false){
  if(!calcScenarios || !calcScenarios.length){ alert('В калькуляторе юнитки нет сохранённых сценариев. Сначала сохрани расчёты в разделе «Калькулятор юнитки».'); return; }
  const parsed=calcScenarios.map(x=>({id:x.id,name:x.name,query:x.query, values:calcScenarioValuesFromQuery(x.query)}));
  let applied=0, noMatch=0;
  rows.forEach(tr=>{
    let best=null, score=0;
    parsed.forEach(sc=>{ const s=rowMatchScore(tr, sc.values); if(s>score){score=s; best=sc;} });
    if(best && score>=55){ applyCalcScenarioToRow(tr,best.values,onlyMissing); applied++; }
    else noMatch++;
  });
  applyFilters();
  document.getElementById('scenarioStatus').textContent = (onlyMissing?'Заполнены пустые поля':'Подтянуты настройки') + ' из калькулятора: ' + applied + ' SKU' + (noMatch?(' · без совпадения: '+noMatch):'');
}

document.addEventListener('input',e=>{ if(e.target.classList.contains('js-field')){ const tr=e.target.closest('tr'); if(e.target.classList.contains('dimension-input')) applyTariff(tr); else {recalcRow(tr); updateTotals();} } if(['filterMargin'].includes(e.target.id)) applyFilters(); });
document.addEventListener('change',e=>{ if(e.target.classList.contains('warehouse-input') || e.target.classList.contains('scheme-input')) applyTariff(e.target.closest('tr')); if(e.target.id && e.target.id.startsWith('filter')) applyFilters(); });
document.getElementById('targetMarginInput').addEventListener('input', recalcAll);
document.getElementById('resetScenarioBtn').onclick=()=>{hydrate(); document.getElementById('scenarioStatus').textContent='Изменения сброшены'; document.getElementById('scenarioCompare').style.display='none';};
document.getElementById('restoreWbBtn').onclick=()=>{hydrate(); document.getElementById('scenarioStatus').textContent='Вернули фактическую базу WB'; document.getElementById('scenarioCompare').style.display='none';};
document.getElementById('compareBtn').onclick=()=>{updateTotals(); const base={rows:rows.map(tr=>({sku:tr._base.sku, values:{price:tr._base.price}, calc:{profit:tr._base.baseProfit, margin:0}}))}; showCompare('Сравнение с фактической базой WB', currentPayload(), base); document.getElementById('scenarioStatus').textContent='Сравнение с базой активно';};
document.getElementById('saveScenarioBtn').onclick=async()=>{ const name=(document.getElementById('scenarioName').value||prompt('Название сценария')||'').trim(); if(!name) return; try{ const data=await api('/api/unit-matrix-scenarios',{method:'POST',body:JSON.stringify({name,payload:currentPayload()})}); document.getElementById('scenarioStatus').textContent='Сценарий сохранён в PostgreSQL: '+data.name; await loadScenarioList(); document.getElementById('scenarioSelect').value=data.id; }catch(e){ alert('Не удалось сохранить сценарий: '+e.message); } };
document.getElementById('loadScenarioBtn').onclick=async()=>{ const id=document.getElementById('scenarioSelect').value; if(!id) return alert('Выбери сценарий.'); try{ const item=await getScenario(id); applyScenarioPayload(item.payload); document.getElementById('scenarioName').value=item.name; document.getElementById('scenarioStatus').textContent='Загружен сценарий: '+item.name; }catch(e){ alert('Не удалось загрузить: '+e.message); } };
document.getElementById('deleteScenarioBtn').onclick=async()=>{ const id=document.getElementById('scenarioSelect').value; if(!id) return alert('Выбери сценарий.'); if(!confirm('Удалить сценарий?')) return; try{ await api('/api/unit-matrix-scenarios/'+id,{method:'DELETE'}); document.getElementById('scenarioStatus').textContent='Сценарий удалён'; await loadScenarioList(); }catch(e){ alert('Не удалось удалить: '+e.message); } };
document.getElementById('compareScenarioBtn').onclick=async()=>{ const id=document.getElementById('compareScenarioSelect').value; if(!id) return alert('Выбери сценарий для сравнения.'); try{ const other=await getScenario(id); showCompare('Сравнение текущей таблицы с сохранённым сценарием: '+other.name, currentPayload(), other.payload); document.getElementById('scenarioStatus').textContent='Сравнение с сохранённым сценарием активно'; }catch(e){ alert('Не удалось сравнить: '+e.message); } };
document.getElementById('applyCalcScenariosBtn').onclick=()=>applyCalcScenarios(false);
document.getElementById('fillMissingCalcScenariosBtn').onclick=()=>applyCalcScenarios(true);
document.getElementById('applyCalcScenariosBtn').onclick=()=>applyCalcScenarios(false);
document.getElementById('fillMissingCalcScenariosBtn').onclick=()=>applyCalcScenarios(true);
hydrate();
loadScenarioList();
</script>
<footer>Интерактивная SKU-юнитка v44 · формула синхронизирована с калькулятором юнитки</footer></div></body></html>
"""


PRODUCT_DETAIL_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{{ summary.name }} · {{ title }}</title><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><div class="product-title"><h1>{{ summary.name or summary.sku }}</h1><span class="sku-chip">{{ summary.sku }}</span>{% if summary.nm_id %}<span class="sku-chip">nmID {{ summary.nm_id }}</span>{% endif %}</div><div class="subtitle">Юнит-экономика товара по сохранённым периодам</div></div><div class="badge">{{ period(summary.first_period, summary.last_period) }}</div></header>
<nav class="nav"><a href="/">Дашборд</a><a class="active" href="/products?period_type={{ selected_type }}">Товары</a><a href="/sales">История продаж</a><a href="/reconcile">Сверка</a><a href="/logistics">Логистика</a><a href="/ai-analyst">AI-аналитик</a><a href="/funnel-ads">Воронка/реклама</a><a href="/ai-chat">AI-чат</a><a href="/plan-fact">План-факт</a><a href="/admin">Себестоимость и расходы</a></nav>
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
<nav class="nav"><a href="/">Дашборд</a><a href="/products">Товары</a><a href="/sales">История продаж</a><a href="/reconcile">Сверка</a><a class="active" href="/unit-economics">Юнит-экономика</a><a href="/unit-matrix">SKU-юнитка</a><a href="/unit-calculator">Калькулятор юнитки</a><a href="/supply-planner">Поставки</a><a href="/logistics">Логистика</a><a href="/ai-analyst">AI-аналитик</a><a href="/funnel-ads">Воронка/реклама</a><a href="/ai-chat">AI-чат</a><a href="/plan-fact">План-факт</a><a href="/admin">Себестоимость и расходы</a></nav>
<form class="filters" method="get"><label>Периоды<select name="period_type"><option value="weekly" {% if selected_type == 'weekly' %}selected{% endif %}>Недельные</option><option value="daily" {% if selected_type == 'daily' %}selected{% endif %}>Дневные и произвольные</option><option value="xlsx" {% if selected_type == 'xlsx' %}selected{% endif %}>Excel</option></select></label><label>С даты<input type="date" name="date_from" value="{{ date_from_value }}"></label><label>По дату<input type="date" name="date_to" value="{{ date_to_value }}"></label><label>Поиск<input name="q" value="{{ query }}" placeholder="Название, артикул или nmID"></label><label>Целевая маржа, %<input inputmode="decimal" name="target_margin" value="{{ target_margin_input }}"></label><label>Сценарий ДРР, %<input inputmode="decimal" name="scenario_drr" value="{{ scenario_drr_input }}"></label><button type="submit">Пересчитать</button></form>
<div class="hint">Расчёт основан на сохранённых отчётах PostgreSQL. Для корректности используй один тип периодов, лучше <b>недельные</b>, чтобы не смешивать дневные и недельные отчёты. Точка безубыточности считается приблизительно: предполагается, что доля выплаты WB от цены остаётся такой же, как в выбранном периоде.</div>
{% if rows %}<div class="grid section"><div class="card"><div class="label">Продано</div><div class="value">{{ units(totals.units) }} шт.</div></div><div class="card"><div class="label">Средняя цена</div><div class="value">{{ money(totals.avg_price) }}</div></div><div class="card"><div class="label">Прибыль / шт.</div><div class="value {{ 'good' if totals.profit_per_unit >= 0 else 'bad' }}">{{ money(totals.profit_per_unit) }}</div></div><div class="card"><div class="label">Маржа</div><div class="value {{ margin_class(totals.margin) }}">{{ percent(totals.margin) }}</div></div><div class="card"><div class="label">ДРР</div><div class="value">{{ percent(totals.drr) }}</div></div><div class="card"><div class="label">Макс ДРР до нуля</div><div class="value {{ 'bad' if totals.max_drr_zero < totals.drr else 'good' }}">{{ percent(totals.max_drr_zero) }}</div></div><div class="card"><div class="label">Макс ДРР для цели</div><div class="value {{ 'bad' if totals.max_drr_target < totals.drr else 'good' }}">{{ percent(totals.max_drr_target) }}</div></div><div class="card"><div class="label">Сценарий прибыль</div><div class="value {{ 'good' if totals.scenario_profit >= 0 else 'bad' }}">{{ money(totals.scenario_profit) }}</div></div><div class="card"><div class="label">Сценарий маржа</div><div class="value {{ margin_class(totals.scenario_margin) }}">{{ percent(totals.scenario_margin) }}</div></div><div class="card"><div class="label">Товаров в минус</div><div class="value {{ 'bad' if totals.loss_products else 'good' }}">{{ totals.loss_products }}</div></div></div>
<div class="section card"><div class="section-head"><div><h2>Таблица юнит-экономики</h2><div class="subtitle">Нажми на товар, чтобы открыть его отдельную аналитику</div></div><span class="muted">Цель: {{ percent(target_margin) }} · Сценарий ДРР: {{ percent(scenario_drr) }}</span></div><div class="table-wrap"><table style="min-width:1820px"><thead><tr><th>Товар</th><th>Артикул</th><th>Шт.</th><th>Цена ср.</th><th>Выплата / шт.</th><th>Услуги WB / шт.</th><th>Себес. / шт.</th><th>Внешн. / шт.</th><th>Реклама / шт.</th><th>УСН / шт.</th><th>Прибыль / шт.</th><th>Маржа</th><th>ДРР</th><th>Макс ДРР 0</th><th>Макс ДРР цель</th><th>Безуб. цена</th><th>Цена для цели</th><th>Сценарий прибыль</th><th>Статус</th></tr></thead><tbody>{% for row in rows %}<tr><td><a class="text-link" href="{{ url_for('product_detail', sku=row.sku, period_type=selected_type, date_from=date_from_value, date_to=date_to_value) }}">{{ row.name or row.sku }}</a></td><td class="muted">{{ row.sku }}</td><td>{{ units(row.units) }}</td><td>{{ money(row.avg_price) }}</td><td>{{ money(row.payout_per_unit) }}</td><td>{{ money(row.wb_services_per_unit) }}</td><td>{{ money(row.cogs_per_unit) }}</td><td>{{ money(row.external_per_unit) }}</td><td>{{ money(row.ad_per_unit) }}</td><td>{{ money(row.tax_per_unit) }}</td><td class="{{ 'profit-pos' if row.profit_per_unit >= 0 else 'profit-neg' }}">{{ money(row.profit_per_unit) }}</td><td>{{ percent(row.margin) }}</td><td>{{ percent(row.drr) }}</td><td class="{{ 'profit-neg' if row.max_drr_zero < row.drr else 'profit-pos' }}">{{ percent(row.max_drr_zero) }}</td><td class="{{ 'profit-neg' if row.max_drr_target < row.drr else 'profit-pos' }}">{{ percent(row.max_drr_target) }}</td><td>{{ money(row.break_even_price) if row.break_even_price is not none else '—' }}</td><td>{{ money(row.target_price) if row.target_price is not none else '—' }}</td><td class="{{ 'profit-pos' if row.scenario_profit >= 0 else 'profit-neg' }}">{{ money(row.scenario_profit) }}</td><td class="{{ 'rank-good' if row.status_level == 'good' else 'rank-bad' if row.status_level == 'bad' else 'value warn' }}">{{ row.status }}</td></tr>{% endfor %}</tbody></table></div></div>
<div class="section card"><h2>Как читать показатели</h2><div class="hint"><b>Услуги WB / шт.</b> = доход покупателей минус расчётная выплата WB. Сюда попадает комиссия, логистика, удержания и прочие услуги из финансового отчёта. <b>Макс ДРР 0</b> — рекламный расход, при котором товар выходит примерно в ноль после УСН. <b>Макс ДРР цель</b> — максимальный ДРР, чтобы сохранить выбранную целевую маржу. <b>Цена для цели</b> — ориентировочная цена продажи при текущих расходах и выбранной марже.</div></div>
{% else %}<div class="card empty section">Нет данных для расчёта. Сначала сохрани недельные отчёты через /week или /backfill.</div>{% endif %}
<footer>WB Profit Dashboard · юнит-экономика рассчитывается по сохранённым отчётам</footer></div></body></html>
"""


UNIT_CALCULATOR_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Калькулятор юнитки · {{ title }}</title>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>Калькулятор юнит-экономики</h1><div class="subtitle">Плановый расчёт товара: цена, СПП, выкуп, комиссия, логистика, реклама, налоги и чистая прибыль</div></div><div class="badge">Ручной сценарий + тарифы WB · v42</div></header>
<nav class="nav"><a href="/">Дашборд</a><a href="/products">Товары</a><a href="/sales">История продаж</a><a href="/reconcile">Сверка</a><a href="/unit-economics">Юнит-экономика</a><a href="/unit-matrix">SKU-юнитка</a><a class="active" href="/unit-calculator">Калькулятор юнитки</a><a href="/supply-planner">Поставки</a><a href="/logistics">Логистика</a><a href="/ai-analyst">AI-аналитик</a><a href="/funnel-ads">Воронка/реклама</a><a href="/ai-chat">AI-чат</a><a href="/plan-fact">План-факт</a><a href="/admin">Себестоимость и расходы</a></nav>
<div class="hint" style="margin:0 0 16px">Это плановый калькулятор. Можно считать сценарии вручную, выбирать склад из тарифов WB или свой FBS-СЦ вручную, подтягивать фактическую логистику из продаж и сохранять расчёты в PostgreSQL. Комиссию WB пока оставь вручную или используй фактическую долю из отчётов, потому что для точной комиссии нужен предмет/категория товара.</div>
{% if tariff_notice %}<div class="notice {{ tariff_notice.kind }}">{{ tariff_notice.text }}</div>{% endif %}
<form method="get" class="section">
<div class="card"><div class="section-head"><div><h2>1. Основные расходы</h2><div class="subtitle">Товар, цена, СПП, выкуп, комиссия и закупка</div></div></div>
<div class="form-grid">
<div class="field"><label>Режим расчёта</label><select name="calc_mode"><option value="plan" {% if values.calc_mode == 'plan' %}selected{% endif %}>План по тарифам/ручным данным</option><option value="fact" {% if values.calc_mode == 'fact' %}selected{% endif %}>Факт WB из отчётов</option><option value="scenario" {% if values.calc_mode == 'scenario' %}selected{% endif %}>Сценарий от факта</option></select><div class="hint" style="margin-top:6px">План считает по твоим тарифам и габаритам. Факт лучше смотреть через кнопку фактической логистики из продаж. Сценарий не затирает данные WB.</div></div>
<div class="field"><label>Товар / артикул</label><select name="sku"><option value="">Новый товар / вручную</option>{% for p in products %}<option value="{{ p.sku }}" {% if values.sku == p.sku %}selected{% endif %}>{{ p.name or p.sku }} · {{ p.sku }}</option>{% endfor %}</select></div>
<div class="field"><label>Название товара</label><input name="product_name" value="{{ values.product_name }}" placeholder="Например, креатин 300 г"></div>
<div class="field"><label>Схема продажи</label><select name="scheme"><option value="fbs" {% if values.scheme == 'fbs' %}selected{% endif %}>FBS</option><option value="fbw" {% if values.scheme == 'fbw' %}selected{% endif %}>FBW</option></select></div>
<div class="field"><label>Цена для покупателя с СПП, ₽</label><input inputmode="decimal" name="price_spp" value="{{ number_input(values.price_spp) }}"><div class="hint" style="margin-top:6px">Вводи цену, которую видит покупатель после СПП. Калькулятор сам восстановит расчётную цену до СПП.</div></div>
<div class="field"><label>СПП, %</label><input inputmode="decimal" name="spp_pct" value="{{ percent_input(values.spp_pct) }}"></div>
<div class="field"><label>Процент выкупа, %</label><input inputmode="decimal" name="buyout_pct" value="{{ percent_input(values.buyout_pct) }}"></div>
<div class="field"><label>Комиссия WB, %</label><input inputmode="decimal" name="commission_pct" value="{{ percent_input(values.commission_pct) }}"></div>
<div class="field"><label>Закуп / количество, шт.</label><input inputmode="decimal" name="purchase_qty" value="{{ number_input(values.purchase_qty) }}"></div>
<div class="field"><label>Цена закупа 1 товара, ₽</label><input inputmode="decimal" name="purchase_price" value="{{ number_input(values.purchase_price) }}"></div>
</div></div>

<div class="card section"><div class="section-head"><div><h2>2. Логистика</h2><div class="subtitle">Габариты, упаковка, склад, тарифы, приёмка и хранение</div></div></div>
<div class="form-grid">
<div class="field"><label>Тип упаковки</label><select name="package_type"><option value="box" {% if values.package_type == 'box' %}selected{% endif %}>Короб</option><option value="mono" {% if values.package_type == 'mono' %}selected{% endif %}>Монопаллета</option></select></div>
<div class="field"><label>Режим склада</label><select name="warehouse_mode"><option value="api" {% if values.warehouse_mode == 'api' %}selected{% endif %}>Склад из тарифов WB</option><option value="manual" {% if values.warehouse_mode == 'manual' %}selected{% endif %}>Мой FBS-склад вручную</option></select><div class="hint" style="margin-top:6px">Если в тарифном API нет твоего СЦ, например СЦ Владикавказ, выбери ручной режим.</div></div>
<div class="field"><label>{% if values.warehouse_mode == 'manual' %}Мой FBS-склад{% else %}Склад WB{% endif %}</label>{% if values.warehouse_mode == 'manual' %}<input name="warehouse" list="manualFbsPoints" value="{{ values.warehouse }}" placeholder="Например, СЦ Владикавказ"><datalist id="manualFbsPoints">{% for wh in manual_warehouses %}<option value="{{ wh }}">{% endfor %}</datalist>{% elif warehouse_options %}<select name="warehouse" id="warehouseSelect"><option value="">Выбери склад WB</option>{% if values.warehouse and values.warehouse not in warehouse_options %}<option value="{{ values.warehouse }}" selected>{{ values.warehouse }}</option>{% endif %}{% for wh in warehouse_options %}<option value="{{ wh }}" {% if values.warehouse == wh %}selected{% endif %}>{{ wh }}</option>{% endfor %}</select>{% else %}<input name="warehouse" value="{{ values.warehouse }}" placeholder="Например, Коледино / Электросталь">{% endif %}<div class="hint" style="margin-top:6px">В режиме WB список берётся из тарифов. В ручном FBS-режиме можно указать любой СЦ и заполнить тарифы вручную или по фактическим продажам.</div></div>
<div class="field"><label>Дата тарифов WB</label><input type="date" name="tariff_date" value="{{ values.tariff_date }}"></div>
<div class="field"><label>Поправка к тарифу, %</label><input inputmode="decimal" name="localization_pct" value="{{ percent_input(values.localization_pct) }}"><div class="hint" style="margin-top:6px">Обычно оставляй 100%. Тариф WB уже подтягивается финальным значением; это поле нужно только для ручной корректировки.</div></div>
<div class="field"><label>Длина, см</label><input inputmode="decimal" name="length_cm" value="{{ number_input(values.length_cm) }}"></div>
<div class="field"><label>Ширина, см</label><input inputmode="decimal" name="width_cm" value="{{ number_input(values.width_cm) }}"></div>
<div class="field"><label>Высота, см</label><input inputmode="decimal" name="height_cm" value="{{ number_input(values.height_cm) }}"></div>
<div class="field"><label>Объём, л</label><input id="volumePreview" readonly value="{{ number(calc.volume_liters) if calculated else number(0) }}"><div class="hint" style="margin-top:6px">Считается автоматически без округления: Д×Ш×В / 1000.</div></div>
<div class="field"><label>Базовая логистика WB, ₽</label><input inputmode="decimal" name="base_logistics" value="{{ number_input(values.base_logistics) }}"></div>
<div class="field"><label>Доплата за литр, ₽</label><input inputmode="decimal" name="extra_liter_cost" value="{{ number_input(values.extra_liter_cost) }}"></div>
<div class="field"><label>Обратная логистика, ₽</label><input inputmode="decimal" name="return_logistics" value="{{ number_input(values.return_logistics) }}"></div>
<div class="field"><label>Оборочиваемость, дн.</label><input inputmode="decimal" name="turnover_days" value="{{ number_input(values.turnover_days) }}"></div>
<div class="field"><label>Хранение, ₽/день</label><input inputmode="decimal" name="storage_per_day" value="{{ number_input(values.storage_per_day) }}"></div>
<div class="field"><label>Приёмка, ₽</label><input inputmode="decimal" name="acceptance" value="{{ number_input(values.acceptance) }}"></div>
</div></div>

<div class="card section"><div class="section-head"><div><h2>3. Другие расходы</h2><div class="subtitle">Налоги, реклама, упаковка, доставка до МП, эквайринг, брак и прочие расходы</div></div></div>
<div class="form-grid">
<div class="field"><label>НДС, %</label><input inputmode="decimal" name="vat_pct" value="{{ percent_input(values.vat_pct) }}"></div>
<div class="field"><label>Налоговая ставка, %</label><input inputmode="decimal" name="tax_pct" value="{{ percent_input(values.tax_pct) }}"></div>
<div class="field"><label>Логистика до МП / шт., ₽</label><input inputmode="decimal" name="mp_delivery" value="{{ number_input(values.mp_delivery) }}"></div>
<div class="field"><label>Упаковка / шт., ₽</label><input inputmode="decimal" name="packaging_cost" value="{{ number_input(values.packaging_cost) }}"></div>
<div class="field"><label>ДРР, %</label><input inputmode="decimal" name="drr_pct" value="{{ percent_input(values.drr_pct) }}"></div>
<div class="field"><label>Доп. тарифные опции WB, ₽</label><input inputmode="decimal" name="tariff_options" value="{{ number_input(values.tariff_options) }}"></div>
<div class="field"><label>Эквайринг, %</label><input inputmode="decimal" name="acquiring_pct" value="{{ percent_input(values.acquiring_pct) }}"></div>
<div class="field"><label>Прочие расходы / шт., ₽</label><input inputmode="decimal" name="other_cost" value="{{ number_input(values.other_cost) }}"></div>
<div class="field"><label>Брак, % от закупа</label><input inputmode="decimal" name="defect_pct" value="{{ percent_input(values.defect_pct) }}"></div>
<div class="field"><label>Целевая маржа, %</label><input inputmode="decimal" name="target_margin" value="{{ percent_input(values.target_margin) }}"></div>
</div><div class="form-actions"><button type="submit">Рассчитать юнитку</button><button class="secondary" type="submit" name="autoload_tariffs" value="1">Подтянуть тарифы WB</button><button class="secondary" type="submit" name="actual_logistics" value="1">Подставить фактическую логистику из продаж</button><a class="button secondary" href="/unit-calculator">Сбросить</a></div></div>

</form>

<div class="section card" id="scenarioBox">
  <div class="section-head"><div><h2>Сценарии калькулятора</h2><div class="subtitle">Сохраняй разные варианты цены, ДРР, склада, габаритов и схемы FBS/FBW в PostgreSQL</div></div></div>
  <div class="form-grid">
    <div class="field"><label>Название сценария</label><input id="scenarioName" placeholder="Например: Креатин · FBS · ДРР 15%"></div>
    <div class="field"><label>&nbsp;</label><button type="button" id="saveScenarioBtn">Сохранить текущий расчёт</button></div>
    <div class="field"><label>&nbsp;</label><button type="button" class="secondary" id="copyScenarioBtn">Скопировать ссылку</button></div>
    <div class="field"><label>&nbsp;</label><button type="button" class="secondary" id="exportScenarioBtn">Экспорт JSON</button></div>
  </div>
  <div id="scenarioMessage" class="hint" style="display:none"></div>
  <div class="table-wrap" style="margin-top:12px"><table style="min-width:930px"><thead><tr><th>Название</th><th>Товар</th><th>Схема</th><th>Цена</th><th>ДРР</th><th>Дата</th><th>Действия</th></tr></thead><tbody id="scenarioRows"><tr><td class="empty" colspan="7">Сохранённых сценариев пока нет.</td></tr></tbody></table></div>
  <div class="hint">Сценарии сохраняются в PostgreSQL и доступны с любого устройства после входа в дашборд.</div>
</div>

{% if calculated %}
<div class="grid section">
<div class="card"><div class="label">Чистая прибыль / шт.</div><div class="value {{ 'good' if calc.net_profit >= 0 else 'bad' }}">{{ money(calc.net_profit) }}</div></div>
<div class="card"><div class="label">Прибыль до налогов / шт.</div><div class="value {{ 'good' if calc.profit_before_tax >= 0 else 'bad' }}">{{ money(calc.profit_before_tax) }}</div></div>
<div class="card"><div class="label">Маржа ROS</div><div class="value {{ margin_class(calc.margin) }}">{{ percent(calc.margin) }}</div></div>
<div class="card"><div class="label">ROI партии</div><div class="value {{ 'good' if calc.roi >= 0 else 'bad' }}">{{ percent(calc.roi) }}</div></div>
<div class="card"><div class="label">ROM</div><div class="value {{ 'good' if calc.rom >= 0 else 'bad' }}">{{ percent(calc.rom) }}</div></div>
<div class="card"><div class="label">Макс ДРР до нуля</div><div class="value {{ 'good' if calc.max_drr_zero >= values.drr_pct else 'bad' }}">{{ percent(calc.max_drr_zero) }}</div></div>
<div class="card"><div class="label">Цена безубыточности</div><div class="value">{{ money(calc.break_even_price) if calc.break_even_price is not none else '—' }}</div></div>
<div class="card"><div class="label">Цена для целевой маржи</div><div class="value">{{ money(calc.target_price) if calc.target_price is not none else '—' }}</div></div>
<div class="card"><div class="label">Объём, л</div><div class="value">{{ number(calc.volume_liters) }} л</div></div>
<div class="card"><div class="label">Статус</div><div class="value {{ 'good' if calc.net_profit >= 0 else 'bad' }}">{{ calc.status }}</div></div>
</div>

<div class="section two-col"><div class="card"><div class="section-head"><div><h2>Расчёт по строкам</h2><div class="subtitle">Как на калькуляторе: партия, юнитка и доля в выручке</div></div></div><div class="table-wrap"><table style="min-width:960px"><thead><tr><th>Показатель</th><th>Партия</th><th>Unit-экономика</th><th>Доля в выручке</th></tr></thead><tbody>{% for row in breakdown %}<tr><td>{{ row.label }}</td><td class="{{ row.cls }}">{{ row.batch }}</td><td class="{{ row.cls }}">{{ row.unit }}</td><td>{{ row.share }}</td></tr>{% endfor %}</tbody></table></div></div>
<div class="card"><h2>Пояснение</h2><div class="hint"><b>Цена с СПП</b> — это цена, которую видит покупатель. Если указан СПП %, калькулятор считает <b>расчётную цену до СПП</b> и уже от неё считает комиссию, ДРР, налог и прибыль. <b>Выкуп</b> теперь влияет только на обратную логистику/невыкупы: прямая логистика не делится на процент выкупа. <b>Объём</b> считается без округления: 1,57 л остаётся 1,57 л. Для FBS-СЦ, которого нет в тарифах WB, используй режим <b>Мой FBS-склад вручную</b> или кнопку фактической логистики из продаж. Колонка <b>Партия</b> считается по количеству закупа, поэтому при закупе 1 шт. она совпадает с unit-экономикой.</div><div class="summary" style="margin-top:14px"><span>Режим: {{ {'plan':'План','fact':'Факт WB','scenario':'Сценарий'}[values.calc_mode] if values.calc_mode in ['plan','fact','scenario'] else 'План' }}</span><span>Схема: {{ values.scheme|upper }}</span><span>Упаковка: {{ 'Монопаллета' if values.package_type == 'mono' else 'Короб' }}</span><span>Режим склада: {{ 'ручной FBS' if values.warehouse_mode == 'manual' else 'тарифы WB' }}</span><span>Склад: {{ values.warehouse or 'не указан' }}</span><span>Цена с СПП: {{ money(calc.price_spp) if calc.price_spp else '—' }}</span><span>Расчётная цена до СПП: {{ money(calc.price_before_spp) if calc.price_before_spp else '—' }}</span></div></div></div>

<div class="section card"><div class="section-head"><div><h2>Аудит формулы</h2><div class="subtitle">Проверка, что именно участвует в расчёте и откуда берётся логистика</div></div></div><div class="grid"><div class="card"><div class="label">Цена с СПП</div><div class="value">{{ money(calc.price_spp) }}</div></div><div class="card"><div class="label">Цена до СПП</div><div class="value">{{ money(calc.price_before_spp) }}</div></div><div class="card"><div class="label">Объём</div><div class="value">{{ number(calc.volume_liters) }} л</div></div><div class="card"><div class="label">Прямая логистика</div><div class="value">{{ money(calc.outbound_logistics) }}</div></div><div class="card"><div class="label">Невыкуп / возврат</div><div class="value">{{ money(calc.return_burden) }}</div></div><div class="card"><div class="label">Логистика всего</div><div class="value">{{ money(calc.logistics_per_buyout) }}</div></div></div><div class="hint" style="margin-top:12px">Формула плана: цена до СПП − комиссия WB − прямая логистика − нагрузка невыкупов − хранение − приёмка − реклама − налог − себестоимость − прочие расходы. Если хочешь сверяться с отчётами WB, используй фактическую логистику из продаж: отчёты WB могут списывать хранение, приёмку, возвраты и корректировки отдельными строками и не всегда в день конкретной продажи.</div></div>
{% endif %}

<script>
(function(){
  const form = document.querySelector('form.section');
  if(!form) return;
  const volumeInput = document.getElementById('volumePreview');
  function parseNum(v){ return parseFloat(String(v||'').replace(',', '.').replace(/\s/g,'')) || 0; }
  function fmt(v){ return (Math.round(v*1000)/1000).toString().replace('.', ','); }
  function updateVolume(){
    if(!volumeInput) return;
    const l=parseNum(form.querySelector('[name="length_cm"]')?.value);
    const w=parseNum(form.querySelector('[name="width_cm"]')?.value);
    const h=parseNum(form.querySelector('[name="height_cm"]')?.value);
    volumeInput.value = fmt(Math.max(0,l*w*h/1000));
  }
  ['length_cm','width_cm','height_cm'].forEach(n=>{
    const el=form.querySelector(`[name="${n}"]`);
    if(el) el.addEventListener('input', updateVolume);
  });
  updateVolume();
})();
</script>

<script>
(function(){
  const rows = document.getElementById('scenarioRows');
  const nameInput = document.getElementById('scenarioName');
  const msg = document.getElementById('scenarioMessage');
  const form = document.querySelector('form.section');
  let items = [];

  function show(text){ if(!msg) return; msg.style.display='block'; msg.textContent=text; }
  function escapeHtml(x){ return String(x || '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch])); }
  function currentParams(){ const params = new URLSearchParams(new FormData(form)); params.delete('autoload_tariffs'); params.delete('actual_logistics'); return params; }
  function titleFromParams(params){
    const name = (params.get('product_name') || params.get('sku') || 'Новый товар').trim();
    const scheme = (params.get('scheme') || 'fbs').toUpperCase();
    const price = params.get('price_spp') || '0';
    const drr = params.get('drr_pct') || '0';
    return `${name} · ${scheme} · ${price} ₽ · ДРР ${drr}%`;
  }
  async function api(path, opts){
    const res = await fetch(path, Object.assign({headers:{'Content-Type':'application/json'}}, opts || {}));
    if(!res.ok){
      let text = await res.text();
      try { const data = JSON.parse(text); text = data.error || text; } catch(e) {}
      throw new Error(text || ('HTTP ' + res.status));
    }
    if(res.status === 204) return null;
    return await res.json();
  }
  async function loadScenarios(){
    try { const data = await api('/api/unit-scenarios'); items = data.items || []; render(); }
    catch(e){ show('Не удалось загрузить сценарии: ' + e.message); render(); }
  }
  function render(){
    if(!rows) return;
    if(!items.length){ rows.innerHTML = '<tr><td class="empty" colspan="7">Сохранённых сценариев пока нет.</td></tr>'; return; }
    rows.innerHTML = items.map((it, idx) => {
      const p = new URLSearchParams(it.query || '');
      const price = p.get('price_spp') || '0';
      const drr = p.get('drr_pct') || '0';
      const product = p.get('product_name') || p.get('sku') || '—';
      const scheme = (p.get('scheme') || 'fbs').toUpperCase();
      const date = it.updated_at || it.created_at || '';
      return `<tr><td>${escapeHtml(it.name || 'Без названия')}</td><td>${escapeHtml(product)}</td><td>${scheme}</td><td>${escapeHtml(price)} ₽</td><td>${escapeHtml(drr)}%</td><td>${escapeHtml(date)}</td><td><div class="inline-actions"><button type="button" class="small secondary" data-load="${idx}">Открыть</button><button type="button" class="small secondary" data-dup="${idx}">Дубль</button><button type="button" class="small danger" data-del="${idx}">Удалить</button></div></td></tr>`;
    }).join('');
  }
  document.getElementById('saveScenarioBtn')?.addEventListener('click', async () => {
    const params = currentParams();
    const name = (nameInput?.value || '').trim() || titleFromParams(params);
    try {
      const item = await api('/api/unit-scenarios', {method:'POST', body: JSON.stringify({name, query: params.toString()})});
      show('Сценарий сохранён в PostgreSQL: ' + item.name);
      await loadScenarios();
    } catch(e){ show('Не удалось сохранить сценарий: ' + e.message); }
  });
  document.getElementById('copyScenarioBtn')?.addEventListener('click', async () => {
    const url = location.origin + location.pathname + '?' + currentParams().toString();
    try { await navigator.clipboard.writeText(url); show('Ссылка на текущий расчёт скопирована.'); }
    catch(e){ show('Ссылка: ' + url); }
  });
  document.getElementById('exportScenarioBtn')?.addEventListener('click', () => {
    const data = JSON.stringify(items, null, 2);
    const blob = new Blob([data], {type:'application/json;charset=utf-8'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'wb-unit-scenarios-postgres.json';
    a.click();
    URL.revokeObjectURL(a.href);
  });
  rows?.addEventListener('click', async ev => {
    const btn = ev.target.closest('button'); if(!btn) return;
    const idx = Number(btn.dataset.load ?? btn.dataset.del ?? btn.dataset.dup);
    const item = items[idx]; if(!item) return;
    if(btn.dataset.load !== undefined){ location.href = '/unit-calculator?' + item.query; return; }
    if(btn.dataset.del !== undefined){
      if(!confirm('Удалить сценарий из PostgreSQL?')) return;
      try { await api('/api/unit-scenarios/' + item.id, {method:'DELETE'}); show('Сценарий удалён.'); await loadScenarios(); }
      catch(e){ show('Не удалось удалить сценарий: ' + e.message); }
      return;
    }
    if(btn.dataset.dup !== undefined){
      try { const clone = await api('/api/unit-scenarios/' + item.id + '/duplicate', {method:'POST'}); show('Создан дубль для сравнения FBS/FBW: ' + clone.name); await loadScenarios(); }
      catch(e){ show('Не удалось создать дубль: ' + e.message); }
    }
  });
  loadScenarios();
  document.getElementById('warehouseSelect')?.addEventListener('change', () => {
    const params = currentParams();
    params.delete('actual_logistics');
    params.set('autoload_tariffs', '1');
    location.href = '/unit-calculator?' + params.toString();
  });
})();
</script>

<footer>WB Profit Dashboard · плановая юнит-экономика</footer></div></body></html>
"""




SUPPLY_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Поставки · WB Profit</title><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>Поставки</h1><div class="subtitle">Остатки FBW/FBS · скорость продаж · дата отправки поставки · рекомендуемое количество</div></div><div class="badge">Остатки: {{ as_of }}</div></header>
<nav class="nav"><a href="/">Дашборд</a><a href="/products">Товары</a><a href="/sales">История продаж</a><a href="/reconcile">Сверка</a><a href="/unit-economics">Юнит-экономика</a><a href="/unit-matrix">SKU-юнитка</a><a href="/unit-calculator">Калькулятор юнитки</a><a class="active" href="/supply-planner">Поставки</a><a href="/logistics">Логистика</a><a href="/ai-analyst">AI-аналитик</a><a href="/funnel-ads">Воронка/реклама</a><a href="/ai-chat">AI-чат</a><a href="/plan-fact">План-факт</a><a href="/admin">Себестоимость и расходы</a></nav>
<form class="filters" method="get">
<label>Продажи с <input type="date" name="date_from" value="{{ date_from_value }}"></label>
<label>по <input type="date" name="date_to" value="{{ date_to_value }}"></label>
<label>Срок пополнения, дн. <input inputmode="numeric" name="lead_days" value="{{ lead_days }}"></label>
<label>Страховой запас, дн. <input inputmode="numeric" name="safety_days" value="{{ safety_days }}"></label>
<label>Целевой запас, дн. <input inputmode="numeric" name="target_days" value="{{ target_days }}"></label>
<label>Предупреждать до, дн. <input inputmode="numeric" name="low_days" value="{{ low_days }}"></label>
<label>Поиск <input name="q" value="{{ query }}" placeholder="товар / артикул / nmID"></label>
<button type="submit">Обновить расчёт</button>
</form>
{% if error %}<div class="notice error">❌ {{ error }}</div>{% endif %}
{% if warning %}<div class="notice error">⚠️ {{ warning }}</div>{% endif %}
<div class="grid">
<div class="card"><div class="label">Товаров в расчёте</div><div class="value">{{ totals.products }}</div></div>
<div class="card"><div class="label">Критично</div><div class="value {{ 'bad' if totals.critical else 'good' }}">{{ totals.critical }}</div></div>
<div class="card"><div class="label">Скоро поставка</div><div class="value {{ 'warn' if totals.low else 'good' }}">{{ totals.low }}</div></div>
<div class="card"><div class="label">Остаток всего</div><div class="value">{{ units(totals.available) }} шт.</div></div>
<div class="card"><div class="label">Рекомендуемая поставка</div><div class="value">{{ units(totals.recommended) }} шт.</div></div>
<div class="card"><div class="label">Оценка закупа</div><div class="value">{{ money(totals.investment) }}</div></div>
<div class="card"><div class="label">Продажи / день</div><div class="value">{{ number(totals.daily_sales) }}</div></div>
<div class="card"><div class="label">FBW / FBS</div><div class="value">{{ units(totals.fbw) }} / {{ units(totals.fbs) }}</div></div>
<div class="card"><div class="label">В пути к клиентам</div><div class="value">{{ units(totals.to_client) }}</div></div>
<div class="card"><div class="label">Возвраты в пути</div><div class="value">{{ units(totals.from_client) }}</div></div>
</div>

<div class="section card"><div class="section-head"><div><h2>План поставки</h2><div class="subtitle">Расчёт использует текущие остатки WB API и продажи из сохранённых отчётов PostgreSQL</div></div></div><div class="table-wrap"><table style="min-width:1420px"><thead><tr><th>Статус</th><th>Товар</th><th>Артикул</th><th>nmID</th><th>Остаток</th><th>FBW</th><th>FBS</th><th>Продажи</th><th>Шт./день</th><th>Хватит</th><th>Отправить до</th><th>Мин. до пополнения</th><th>Реком. поставка</th><th>Закуп / шт.</th><th>Бюджет</th></tr></thead><tbody>{% for row in rows %}<tr><td class="{{ row.status_class }}">{{ row.status }}</td><td>{{ row.name }}</td><td class="muted">{{ row.sku }}</td><td>{{ row.nm_id or '—' }}</td><td>{{ units(row.available) }}</td><td>{{ units(row.fbw) }}</td><td>{{ units(row.fbs) }}</td><td>{{ units(row.sales_units) }}</td><td>{{ number(row.daily_sales) }}</td><td>{{ row.days_left_text }}</td><td>{{ row.send_by }}</td><td>{{ units(row.min_supply) }}</td><td><b>{{ units(row.recommended_supply) }}</b></td><td>{{ money(row.cogs_per_unit) if row.cogs_per_unit else '—' }}</td><td>{{ money(row.investment) if row.investment else '—' }}</td></tr>{% else %}<tr><td class="empty" colspan="15">Нет данных для расчёта. Сначала сохрани недельные отчёты через /backfill или проверь доступ WB API к остаткам.</td></tr>{% endfor %}</tbody></table></div>
<div class="hint">Формула: рекомендуемая поставка = продажи в день × (срок пополнения + страховой запас + целевой запас) − текущий доступный остаток. Если продаж за период нет, бот показывает остаток, но не рассчитывает поставку. Закупочный бюджет берётся из сохранённой себестоимости по фактическим продажам; если продаж ещё не было, бюджет может быть пустым.</div></div>

<footer>WB Profit Dashboard · планирование поставок</footer></div></body></html>
"""

LOGISTICS_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Логистика и услуги WB · {{ title }}</title><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>Логистика и услуги WB</h1><div class="subtitle">Контроль доли удержаний WB, внешней логистики, расходов на единицу и товаров с высокой нагрузкой</div></div><div class="badge">Период: {{ type_label(selected_type) }}</div></header>
<nav class="nav"><a href="/">Дашборд</a><a href="/products">Товары</a><a href="/sales">История продаж</a><a href="/reconcile">Сверка</a><a href="/unit-economics">Юнит-экономика</a><a href="/unit-matrix">SKU-юнитка</a><a href="/unit-calculator">Калькулятор юнитки</a><a href="/supply-planner">Поставки</a><a class="active" href="/logistics">Логистика</a><a href="/ai-analyst">AI-аналитик</a><a href="/funnel-ads">Воронка/реклама</a><a href="/ai-chat">AI-чат</a><a href="/plan-fact">План-факт</a><a href="/admin">Себестоимость и расходы</a></nav>
<form class="filters" method="get"><select name="period_type"><option value="weekly" {% if selected_type == 'weekly' %}selected{% endif %}>Недельные</option><option value="daily" {% if selected_type == 'daily' %}selected{% endif %}>Дневные и произвольные</option><option value="xlsx" {% if selected_type == 'xlsx' %}selected{% endif %}>Excel</option></select><label>С <input type="date" name="date_from" value="{{ date_from_value }}"></label><label>По <input type="date" name="date_to" value="{{ date_to_value }}"></label><label>Поиск <input name="q" placeholder="товар / артикул / nmID" value="{{ query }}"></label><label>Предупр. WB % <input inputmode="decimal" name="warn_wb" value="{{ warn_input }}"></label><label>Критично WB % <input inputmode="decimal" name="critical_wb" value="{{ critical_input }}"></label><button type="submit">Показать</button></form>
<div class="grid"><div class="card"><div class="label">Товаров</div><div class="value">{{ totals.products }}</div></div><div class="card"><div class="label">Доход покупателей</div><div class="value">{{ money(totals.revenue) }}</div></div><div class="card"><div class="label">WB удержания и услуги</div><div class="value {{ 'bad' if totals.wb_share >= critical_wb else 'warn' if totals.wb_share >= warn_wb else '' }}">{{ money(totals.wb_services) }}</div><div class="subtitle">{{ percent(totals.wb_share) }} от дохода</div></div><div class="card"><div class="label">Внешние расходы</div><div class="value">{{ money(totals.external) }}</div><div class="subtitle">{{ money(totals.external_per_unit) }} / шт.</div></div><div class="card"><div class="label">Общая нагрузка</div><div class="value {{ 'bad' if totals.burden_share >= critical_wb else 'warn' if totals.burden_share >= warn_wb else '' }}">{{ percent(totals.burden_share) }}</div><div class="subtitle">WB + внешние расходы</div></div></div>

<div class="section card"><div class="section-head"><div><h2>Детализация удержаний WB</h2><div class="subtitle">Разбор строк финансового отчёта: логистика, приёмка, хранение, штрафы и прочие удержания</div></div><span class="muted">Операций: {{ detail_totals.operations|int }}</span></div>
  <div class="grid" style="margin-bottom:14px"><div class="card"><div class="label">Логистика WB</div><div class="value">{{ money(detail_totals.logistics_total) }}</div></div><div class="card"><div class="label">Приёмка</div><div class="value">{{ money(detail_totals.handling) }}</div></div><div class="card"><div class="label">Хранение</div><div class="value">{{ money(detail_totals.storage) }}</div></div><div class="card"><div class="label">Штрафы</div><div class="value {{ 'bad' if detail_totals.fines else '' }}">{{ money(detail_totals.fines) }}</div></div><div class="card"><div class="label">Неразложенная часть</div><div class="value {{ 'warn' if abs(detail_totals.unallocated) > 1 else '' }}">{{ money(detail_totals.unallocated) }}</div></div></div>
  <div class="two-col"><div class="table-wrap"><table style="min-width:760px"><thead><tr><th>Статья</th><th>Сумма</th><th>Доля от дохода</th><th>Комментарий</th></tr></thead><tbody>{% for row in detail_breakdown %}<tr><td>{{ row.name }}</td><td class="{{ 'profit-neg' if row.amount < 0 else '' }}">{{ money(row.amount) }}</td><td>{{ percent(row.share) }}</td><td class="muted">{{ row.hint }}</td></tr>{% endfor %}</tbody></table></div><div><div class="hint"><b>Общие WB удержания</b> считаются как доход покупателей − расчётная выплата WB. Ниже мы раскладываем их по строкам фин. отчёта. Если есть “неразложенная часть”, значит WB отдал удержания, которые не удалось точно отнести к логистике, приёмке, хранению или штрафам.</div><div class="hint">Для максимально точной детализации обнови историю через <b>/backfill ДД.ММ.ГГГГ</b> после установки этого обновления.</div></div></div>
</div>
<div class="section two-col"><div class="card"><div class="section-head"><div><h2>Топ товаров по нагрузке WB</h2><div class="subtitle">Чем выше столбец, тем больше доля удержаний WB в доходе товара</div></div></div><div class="chart-box"><canvas id="logisticsChart"></canvas></div></div><div class="card"><h2>Как читать страницу</h2><div class="hint"><b>WB удержания и услуги</b> = доход покупателей − расчётная выплата WB. Внутри этой суммы могут быть комиссия, логистика, хранение, приёмка, штрафы и прочие удержания WB. Общая сумма нужна для сверки. Детальная расшифровка по строкам финансового отчёта показана ниже: логистика, приёмка, хранение, штрафы и прочие удержания.</div><div class="hint"><b>Внешние расходы</b> — упаковка, фулфилмент, доставка до склада и другие расходы, которые ты заносишь в разделе «Себестоимость и расходы».</div><div class="hint">Если часть расходов отображается как “неразложенная”, её лучше сверять в истории продаж и финансовом отчёте WB.</div></div></div>
<div class="section card"><div class="section-head"><div><h2>Товары</h2><div class="subtitle">Сортировка: сначала самая высокая нагрузка WB</div></div><span class="muted">{{ period_label }}</span></div><div class="table-wrap"><table><thead><tr><th>Товар</th><th>Артикул</th><th>Шт.</th><th>Доход</th><th>Выплата WB</th><th>WB удержания</th><th>WB / шт.</th><th>WB %</th><th>Внешние</th><th>Внешн. / шт.</th><th>Общая нагрузка</th><th>Прибыль</th><th>Маржа</th><th>Статус</th></tr></thead><tbody>{% for row in rows %}<tr><td>{{ row.name or row.sku }}</td><td class="muted">{{ row.sku }}</td><td>{{ units(row.units) }}</td><td>{{ money(row.revenue) }}</td><td>{{ money(row.payout) }}</td><td>{{ money(row.wb_services) }}</td><td>{{ money(row.wb_per_unit) }}</td><td class="{{ row.status_class }}">{{ percent(row.wb_share) }}</td><td>{{ money(row.external) }}</td><td>{{ money(row.external_per_unit) }}</td><td>{{ percent(row.burden_share) }}</td><td class="{{ 'profit-pos' if row.profit >= 0 else 'profit-neg' }}">{{ money(row.profit) }}</td><td>{{ percent(row.margin) }}</td><td class="{{ row.status_class }}">{{ row.status }}</td></tr>{% else %}<tr><td colspan="14" class="empty">Нет данных за выбранный период.</td></tr>{% endfor %}</tbody></table></div></div>

<div class="section card"><div class="section-head"><div><h2>Детализация по товарам</h2><div class="subtitle">По строкам истории продаж и расходов WB. Сортировка — по сумме удержаний WB.</div></div></div><div class="table-wrap"><table style="min-width:1350px"><thead><tr><th>Товар</th><th>Артикул</th><th>nmID</th><th>Шт.</th><th>Доход</th><th>WB удержания</th><th>Логистика</th><th>Лог. / шт.</th><th>Приёмка</th><th>Приёмка / шт.</th><th>Хранение</th><th>Штрафы</th><th>Прочие WB</th><th>Неразложено</th></tr></thead><tbody>{% for row in detail_rows %}<tr><td>{{ row.name }}</td><td class="muted">{{ row.sku }}</td><td class="muted">{{ row.nm_id or '—' }}</td><td>{{ units(row.quantity) }}</td><td>{{ money(row.revenue) }}</td><td>{{ money(row.wb_expenses) }}</td><td>{{ money(row.logistics_total) }}</td><td>{{ money(row.logistics_per_unit) }}</td><td>{{ money(row.handling) }}</td><td>{{ money(row.handling_per_unit) }}</td><td>{{ money(row.storage) }}</td><td class="{{ 'profit-neg' if row.fines else '' }}">{{ money(row.fines) }}</td><td>{{ money(row.other) }}</td><td class="{{ 'warn' if abs(row.unallocated) > 1 else '' }}">{{ money(row.unallocated) }}</td></tr>{% else %}<tr><td colspan="14" class="empty">Детализация по операциям пока не найдена. Запусти /backfill, чтобы сохранить историю продаж.</td></tr>{% endfor %}</tbody></table></div></div>
<footer>WB Profit Dashboard · v30 · детализация логистики и удержаний WB</footer></div>
<script>
const labels={{ chart_labels|tojson }}; const wb={{ chart_wb|tojson }}; const external={{ chart_external|tojson }};
new Chart(document.getElementById('logisticsChart'),{type:'bar',data:{labels,datasets:[{label:'WB %',data:wb},{label:'Внешние %',data:external}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#f5f7fb'}}},scales:{x:{ticks:{color:'#9aa3b5',maxRotation:35,minRotation:0},grid:{color:'rgba(255,255,255,.05)'}},y:{ticks:{color:'#9aa3b5',callback:v=>v+'%'},grid:{color:'rgba(255,255,255,.06)'}}}}});
</script></body></html>
"""


SALES_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>История продаж · WB Profit</title>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>История продаж</h1><div class="subtitle">Построчная детализация: продажи, возвраты и отдельные расходы WB из финансового отчёта</div></div><div class="badge">{{ summary.operations }} операций</div></header>
<nav class="nav"><a href="/">Дашборд</a><a href="/products">Товары</a><a class="active" href="/sales">История продаж</a><a href="/reconcile">Сверка</a><a href="/unit-economics">Юнит-экономика</a><a href="/unit-matrix">SKU-юнитка</a><a href="/unit-calculator">Калькулятор юнитки</a><a href="/supply-planner">Поставки</a><a href="/logistics">Логистика</a><a href="/ai-analyst">AI-аналитик</a><a href="/funnel-ads">Воронка/реклама</a><a href="/ai-chat">AI-чат</a><a href="/plan-fact">План-факт</a><a href="/admin">Себестоимость и расходы</a></nav>
<form class="filters" method="get"><label>Тип данных <select name="period_type"><option value="actual" {% if selected_type == 'actual' %}selected{% endif %}>Актуально без дублей</option><option value="weekly" {% if selected_type == 'weekly' %}selected{% endif %}>Только подтверждённые недели</option><option value="daily" {% if selected_type == 'daily' %}selected{% endif %}>Только ежедневные/оперативные</option><option value="" {% if selected_type == '' %}selected{% endif %}>Все типы, могут быть дубли</option></select></label><label>С даты <input type="date" name="date_from" value="{{ date_from_value }}"></label><label>По дату <input type="date" name="date_to" value="{{ date_to_value }}"></label><label>Операция <select name="operation_type"><option value="" {% if not operation_type %}selected{% endif %}>Все</option><option value="Продажа" {% if operation_type == 'Продажа' %}selected{% endif %}>Продажи</option><option value="Возврат" {% if operation_type == 'Возврат' %}selected{% endif %}>Возвраты</option><option value="Расход WB" {% if operation_type == 'Расход WB' %}selected{% endif %}>Расходы WB</option></select></label><input name="q" value="{{ query }}" placeholder="Поиск: артикул, товар, nmID"><label><input type="checkbox" name="negative" value="1" {% if only_negative %}checked{% endif %}> только минус</label><label><input type="checkbox" name="missing_cost" value="1" {% if only_missing_cost %}checked{% endif %}> без себеса</label><button type="submit">Показать</button><a class="button secondary" href="{{ export_url }}">Экспорт CSV</a></form>{% if selected_type == 'actual' %}<div class="hint section">Режим <b>«Актуально без дублей»</b>: закрытые недели берутся из недельных отчётов, а текущая незакрытая неделя — из ежедневных отчётов после {{ date_display(actual_cutoff) if actual_cutoff else 'начала доступной истории' }}. Чтобы обновить текущие дни, запусти в Telegram <b>/syncdaily</b>.</div>{% endif %}
{% if rows %}<div class="grid section"><div class="card"><div class="label">Операций</div><div class="value">{{ summary.operations }}</div></div><div class="card"><div class="label">Кол-во</div><div class="value">{{ units(summary.quantity) }} шт.</div></div><div class="card"><div class="label">Доход покупателей</div><div class="value">{{ money(summary.revenue) }}</div></div><div class="card"><div class="label">Расчётная выплата</div><div class="value">{{ money(summary.payout) }}</div></div><div class="card"><div class="label">WB удержания</div><div class="value">{{ money(summary.wb_expenses) }}</div></div><div class="card"><div class="label">Себестоимость</div><div class="value">{{ money(summary.cogs) }}</div></div><div class="card"><div class="label">Реклама</div><div class="value">{{ money(summary.advertising) }}</div></div><div class="card"><div class="label">До налога</div><div class="value {{ 'good' if summary.profit_before_tax >= 0 else 'bad' }}">{{ money(summary.profit_before_tax) }}</div></div><div class="card"><div class="label">Чистая прибыль</div><div class="value {{ 'good' if summary.profit >= 0 else 'bad' }}">{{ money(summary.profit) }}</div></div><div class="card"><div class="label">Маржа / ДРР</div><div class="value {{ margin_class(summary.margin) }}">{{ percent(summary.margin) }} / {{ percent(summary.drr) }}</div></div></div>
<div class="section card"><div class="section-head"><div><h2>Операции</h2><div class="subtitle">Дата берётся из продажи/возврата WB; если WB не отдал дату — используется период отчёта</div></div><span class="muted">Показано до {{ limit }} строк</span></div><div class="table-wrap"><table style="min-width:1900px"><thead><tr><th>Дата</th><th>Операция</th><th>Товар</th><th>Артикул</th><th>nmID</th><th>Шт.</th><th>Цена/доход</th><th>Выплата WB</th><th>WB удержания</th><th>Логистика</th><th>Приёмка</th><th>Хранение</th><th>Прочие WB</th><th>Себес.</th><th>Внешние</th><th>Реклама</th><th>До налога</th><th>УСН</th><th>Чистая</th><th>Маржа</th><th>Статус</th><th>Период отчёта</th></tr></thead><tbody>{% for row in rows %}<tr><td>{{ date_display(row.operation_date or row.period_start) }}</td><td class="{{ 'profit-neg' if row.operation_type == 'Возврат' else '' }}">{{ row.operation_type }}</td><td>{{ row.name or row.sku }}</td><td class="muted">{{ row.sku }}</td><td class="muted">{{ row.nm_id or '—' }}</td><td>{{ units(row.quantity) }}</td><td>{{ money(row.revenue) }}</td><td>{{ money(row.payout) }}</td><td>{{ money(row.wb_expenses) }}</td><td>{{ money(row.logistics + row.transport) }}</td><td>{{ money(row.handling) }}</td><td>{{ money(row.storage) }}</td><td>{{ money(row.other_withholdings + row.fines) }}</td><td class="{{ 'profit-neg' if row.missing_cost else '' }}">{{ money(row.cogs) }}{% if row.missing_cost %} ⚠{% endif %}</td><td>{{ money(row.external_expenses) }}</td><td>{{ money(row.advertising) }}</td><td class="{{ 'profit-pos' if row.profit_before_tax >= 0 else 'profit-neg' }}">{{ money(row.profit_before_tax) }}</td><td>{{ money(row.tax) }}</td><td class="{{ 'profit-pos' if row.profit >= 0 else 'profit-neg' }}">{{ money(row.profit) }}</td><td>{{ percent(row.margin) }}</td><td>{% if row.period_type == 'weekly' %}<span class="rank-good">Подтверждено</span>{% elif row.period_type == 'daily' %}<span class="value warn">Оперативно</span>{% else %}<span class="muted">{{ row.period_type }}</span>{% endif %}</td><td class="muted">{{ period(row.period_start,row.period_end) }}</td></tr>{% endfor %}</tbody></table></div><div class="hint">Чтобы текущая незакрытая неделя появилась в истории, запусти в Telegram <b>/syncdaily</b>. Для загрузки ежедневных отчётов с конкретной даты используй <b>/backfilldaily ДД.ММ.ГГГГ</b>. Закрытые недели остаются финальными, дневные строки помечены как оперативные.</div></div>{% else %}<div class="card empty section">Операций пока нет. Установи v24 и запусти в Telegram <b>/backfill 01.04.2026</b>, чтобы заполнить построчную историю.</div>{% endif %}
<footer>WB Profit Dashboard · история продаж строится из финансовых отчётов реализации</footer></div></body></html>
"""


ADMIN_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Управление данными · WB Profit</title>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>Себестоимость и расходы</h1><div class="subtitle">Изменения сохраняются в PostgreSQL и не пропадают после перезапуска Railway</div></div><div class="badge">{{ cost_count }} профилей · {{ expense_count }} расходов</div></header>
<nav class="nav"><a href="/">Дашборд</a><a href="/products">Товары</a><a href="/sales">История продаж</a><a href="/reconcile">Сверка</a><a href="/unit-economics">Юнит-экономика</a><a href="/ai-analyst">AI-аналитик</a><a href="/funnel-ads">Воронка/реклама</a><a href="/ai-chat">AI-чат</a><a href="/plan-fact">План-факт</a><a class="active" href="/admin">Себестоимость и расходы</a></nav>
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


RECONCILE_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Сверка · WB Profit</title>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>Сверка WB и дашборда</h1><div class="subtitle">Объясняет расхождения между балансом WB, недельными отчётами, ежедневными отчётами и историей продаж</div></div><div class="badge">{{ period_label }}</div></header>
<nav class="nav"><a href="/">Дашборд</a><a href="/products">Товары</a><a href="/sales">История продаж</a><a class="active" href="/reconcile">Сверка</a><a href="/unit-economics">Юнит-экономика</a><a href="/unit-matrix">SKU-юнитка</a><a href="/unit-calculator">Калькулятор юнитки</a><a href="/supply-planner">Поставки</a><a href="/logistics">Логистика</a><a href="/ai-analyst">AI-аналитик</a><a href="/funnel-ads">Воронка/реклама</a><a href="/ai-chat">AI-чат</a><a href="/plan-fact">План-факт</a><a href="/admin">Себестоимость и расходы</a></nav>
<form class="filters" method="get"><label>С даты <input type="date" name="date_from" value="{{ date_from_value }}"></label><label>По дату <input type="date" name="date_to" value="{{ date_to_value }}"></label><label>Стартовый баланс WB, ₽ <input inputmode="decimal" name="start_balance" value="{{ start_balance_input }}" placeholder="например -25000"></label><label>Текущий баланс WB, ₽ <input inputmode="decimal" name="end_balance" value="{{ end_balance_input }}" placeholder="например 81000"></label><button type="submit">Сверить</button><a class="button secondary" href="/reconcile">Сбросить</a></form>

<div class="grid section">
  <div class="card"><div class="label">Актуальные продажи без дублей</div><div class="value">{{ units(actual_sales.quantity) }} шт.</div></div>
  <div class="card"><div class="label">Доход покупателей</div><div class="value">{{ money(actual_summary.revenue) }}</div></div>
  <div class="card"><div class="label">Расчётная выплата WB</div><div class="value">{{ money(actual_summary.payout) }}</div></div>
  <div class="card"><div class="label">WB удержания</div><div class="value">{{ money(actual_summary.wb_expenses) }}</div></div>
  <div class="card"><div class="label">Чистая прибыль</div><div class="value {{ 'good' if actual_summary.profit >= 0 else 'bad' }}">{{ money(actual_summary.profit) }}</div></div>
</div>

<div class="section two-col">
  <div class="card"><div class="section-head"><div><h2>Почему цифры могут расходиться</h2><div class="subtitle">Разные разделы WB считают разные события</div></div></div>
    <div class="table-wrap"><table style="min-width:860px"><thead><tr><th>Источник</th><th>Количество</th><th>Доход</th><th>Выплата</th><th>Комментарий</th></tr></thead><tbody>
      <tr><td>Актуально без дублей</td><td>{{ units(actual_sales.quantity) }} шт.</td><td>{{ money(actual_summary.revenue) }}</td><td>{{ money(actual_summary.payout) }}</td><td class="muted">Закрытые недели + ежедневные отчёты после последней закрытой недели</td></tr>
      <tr><td>Только закрытые недели</td><td>{{ units(weekly_sales.quantity) }} шт.</td><td>{{ money(weekly_summary.revenue) }}</td><td>{{ money(weekly_summary.payout) }}</td><td class="muted">Финальная база для чистой прибыли</td></tr>
      <tr><td>Только ежедневные отчёты</td><td>{{ units(daily_sales.quantity) }} шт.</td><td>{{ money(daily_summary.revenue) }}</td><td>{{ money(daily_summary.payout) }}</td><td class="muted">Оперативные данные. Могут пересекаться с закрытыми неделями</td></tr>
      <tr><td>Оперативный хвост</td><td>{{ units(operative_tail.quantity) }} шт.</td><td>{{ money(operative_tail.revenue) }}</td><td>{{ money(operative_tail.payout) }}</td><td class="muted">Дневные данные после {{ date_display(actual_cutoff) if actual_cutoff else 'последней закрытой недели' }}</td></tr>
    </tbody></table></div>
    <div class="hint">Если в WB воронка показывает больше выкупов, чем дашборд, чаще всего причина — текущая неделя ещё не закрыта. Запусти в Telegram <b>/syncdaily</b> или <b>/backfilldaily ДД.ММ.ГГГГ</b>, затем выбери в истории продаж режим <b>Актуально без дублей</b>.</div>
  </div>

  <div class="card"><div class="section-head"><div><h2>От баланса к прибыли</h2><div class="subtitle">Баланс WB ≠ выручка ≠ чистая прибыль</div></div></div>
    {% if balance_ready %}
      <div class="summary"><span>Стартовый баланс: {{ money(start_balance) }}</span><span>Текущий баланс: {{ money(end_balance) }}</span><span>Изменение: {{ money(balance_change) }}</span></div>
      <div class="table-wrap"><table style="min-width:650px"><tbody>
        <tr><td>Изменение баланса WB</td><td>{{ money(balance_change) }}</td></tr>
        <tr><td>Доход покупателей по дашборду</td><td>{{ money(actual_summary.revenue) }}</td></tr>
        <tr><td>Расчётная выплата WB</td><td>{{ money(actual_summary.payout) }}</td></tr>
        <tr><td>Разница “баланс − выплата”</td><td class="{{ 'profit-pos' if balance_minus_payout >= 0 else 'profit-neg' }}">{{ money(balance_minus_payout) }}</td></tr>
        <tr><td>Чистая прибыль по дашборду</td><td class="{{ 'profit-pos' if actual_summary.profit >= 0 else 'profit-neg' }}">{{ money(actual_summary.profit) }}</td></tr>
      </tbody></table></div>
      <div class="hint">Разница может включать старый минус, удержания без артикула, рекламу, компенсации, корректировки, возвраты, платные услуги и операции, которые не являются продажами.</div>
    {% else %}
      <div class="empty">Введи стартовый и текущий баланс WB сверху, чтобы увидеть мост от изменения баланса к выплатам и прибыли.</div>
    {% endif %}
  </div>
</div>

<div class="section grid">
  <div class="card"><div class="label">Возвраты</div><div class="value {{ 'bad' if abs(returns.quantity) > 0 else '' }}">{{ units(abs(returns.quantity)) }} шт.</div><div class="hint">Сумма возвратов: {{ money(returns.revenue) }}</div></div>
  <div class="card"><div class="label">Расходы WB отдельными строками</div><div class="value">{{ money(wb_cost_rows.wb_expenses) }}</div><div class="hint">Операций: {{ wb_cost_rows.operations }}</div></div>
  <div class="card"><div class="label">Нераспределённые расходы WB</div><div class="value {{ 'warn' if abs(unallocated.wb_expenses) > 0 else '' }}">{{ money(unallocated.wb_expenses) }}</div><div class="hint">Без артикула или с артикулом “НЕРАСПРЕДЕЛЕНО”</div></div>
  <div class="card"><div class="label">Без себестоимости</div><div class="value {{ 'bad' if actual_summary.missing_cost_count else '' }}">{{ actual_summary.missing_cost_count }}</div><div class="hint">Проверь раздел “Себестоимость и расходы”</div></div>
  <div class="card"><div class="label">Оперативных продаж</div><div class="value">{{ units(operative_tail.quantity) }} шт.</div><div class="hint">Ещё могут измениться после закрытия недели</div></div>
</div>

<div class="section card"><div class="section-head"><div><h2>Проверка расхождений</h2><div class="subtitle">Что проверить, если WB и дашборд не совпадают</div></div></div>
  <div class="table-wrap"><table style="min-width:900px"><thead><tr><th>Проверка</th><th>Статус</th><th>Что значит</th></tr></thead><tbody>
    {% for item in checks %}<tr><td>{{ item.name }}</td><td class="{{ item.cls }}">{{ item.status }}</td><td class="muted">{{ item.note }}</td></tr>{% endfor %}
  </tbody></table></div>
  <div class="hint">Сверку лучше делать за одинаковый период. Для финальной прибыли используй закрытые недели; для текущего месяца — “Актуально без дублей”.</div>
</div>
<footer>WB Profit Dashboard · v29 · сверка источников данных</footer></div></body></html>
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


def _query_bool(name: str) -> bool:
    return request.args.get(name, "").strip().lower() in {"1", "true", "yes", "on", "да"}


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



def _query_float(name: str, default: float = 0) -> float:
    raw = request.args.get(name, "").strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if raw == "":
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _query_rate(name: str, default: float = 0, *, min_value: float = -1, max_value: float = 10) -> float:
    value = _query_float(name, default * 100 if abs(default) <= 1 else default)
    if abs(value) > 1:
        value = value / 100
    return max(min_value, min(float(value), max_value))


def _safe_rate(value: float, default: float = 0) -> float:
    value = float(value if value is not None else default)
    if value > 1:
        value = value / 100
    return max(-1.0, min(value, 10.0))

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


def _logistics_row(row, *, warn_wb: float, critical_wb: float) -> dict[str, Any]:
    units = float(row.units or 0)
    revenue = float(row.revenue or 0)
    payout = float(row.payout or 0)
    wb_services = revenue - payout
    external = float(row.external_expenses or 0)
    burden = wb_services + external
    wb_share = _div(wb_services, revenue)
    external_share = _div(external, revenue)
    burden_share = _div(burden, revenue)
    if wb_share >= critical_wb or burden_share >= critical_wb:
        status = "🔴 высокая нагрузка"
        status_class = "profit-neg"
    elif wb_share >= warn_wb or burden_share >= warn_wb:
        status = "🟠 проверить"
        status_class = "rank-bad"
    else:
        status = "🟢 норма"
        status_class = "profit-pos"
    return {
        "sku": row.sku,
        "name": row.name,
        "units": units,
        "revenue": revenue,
        "payout": payout,
        "wb_services": wb_services,
        "wb_per_unit": _div(wb_services, units),
        "wb_share": wb_share,
        "external": external,
        "external_per_unit": _div(external, units),
        "external_share": external_share,
        "burden": burden,
        "burden_per_unit": _div(burden, units),
        "burden_share": burden_share,
        "profit": float(row.profit or 0),
        "margin": float(row.margin or 0),
        "status": status,
        "status_class": status_class,
    }





def _sale_op_amount(row: Any, field: str) -> float:
    try:
        return float(getattr(row, field, 0) or 0)
    except Exception:
        return 0.0


def _operation_logistics_details(rows: list[Any]) -> tuple[dict[str, float], list[dict[str, Any]], list[dict[str, Any]]]:
    """Aggregate detailed WB deductions from saved sale operations.

    Weekly product summaries only contain total payout/revenue. Sale operations keep a
    more granular split: logistics, transport, handling, storage, fines and other WB
    withholdings. This helper makes the logistics page explain what exactly sits inside
    the aggregated WB deductions.
    """
    totals = {
        "operations": float(len(rows)),
        "quantity": 0.0,
        "revenue": 0.0,
        "payout": 0.0,
        "wb_expenses": 0.0,
        "logistics": 0.0,
        "transport": 0.0,
        "logistics_total": 0.0,
        "handling": 0.0,
        "storage": 0.0,
        "fines": 0.0,
        "other": 0.0,
        "detailed_total": 0.0,
        "unallocated": 0.0,
        "expense_rows": 0.0,
        "sale_rows": 0.0,
        "return_rows": 0.0,
    }
    by_sku: dict[str, dict[str, Any]] = {}
    for row in rows:
        op = str(getattr(row, "operation_type", "") or "")
        sku = str(getattr(row, "sku", "") or "НЕРАСПРЕДЕЛЕНО")
        name = str(getattr(row, "name", "") or sku)
        nm_id = getattr(row, "nm_id", None)
        qty = _sale_op_amount(row, "quantity")
        revenue = _sale_op_amount(row, "revenue")
        payout = _sale_op_amount(row, "payout")
        wb_expenses = _sale_op_amount(row, "wb_expenses")
        logistics = _sale_op_amount(row, "logistics")
        transport = _sale_op_amount(row, "transport")
        logistics_total = logistics + transport
        handling = _sale_op_amount(row, "handling")
        storage = _sale_op_amount(row, "storage")
        fines = _sale_op_amount(row, "fines")
        other = _sale_op_amount(row, "other_withholdings")
        detailed = logistics_total + handling + storage + fines + other

        totals["quantity"] += qty
        totals["revenue"] += revenue
        totals["payout"] += payout
        totals["wb_expenses"] += wb_expenses
        totals["logistics"] += logistics
        totals["transport"] += transport
        totals["logistics_total"] += logistics_total
        totals["handling"] += handling
        totals["storage"] += storage
        totals["fines"] += fines
        totals["other"] += other
        totals["detailed_total"] += detailed
        if op == "Расход WB":
            totals["expense_rows"] += 1
        elif op == "Продажа":
            totals["sale_rows"] += 1
        elif op == "Возврат":
            totals["return_rows"] += 1

        item = by_sku.setdefault(sku, {
            "sku": sku,
            "name": name,
            "nm_id": nm_id,
            "quantity": 0.0,
            "revenue": 0.0,
            "wb_expenses": 0.0,
            "logistics_total": 0.0,
            "handling": 0.0,
            "storage": 0.0,
            "fines": 0.0,
            "other": 0.0,
            "detailed_total": 0.0,
            "unallocated": 0.0,
        })
        item["quantity"] += qty
        item["revenue"] += revenue
        item["wb_expenses"] += wb_expenses
        item["logistics_total"] += logistics_total
        item["handling"] += handling
        item["storage"] += storage
        item["fines"] += fines
        item["other"] += other
        item["detailed_total"] += detailed
        item["name"] = name or item["name"]
        item["nm_id"] = nm_id or item["nm_id"]

    totals["unallocated"] = totals["wb_expenses"] - totals["detailed_total"]
    breakdown = [
        {"name": "Логистика WB", "amount": totals["logistics_total"], "share": _div(totals["logistics_total"], totals["revenue"]), "hint": "Доставка, обратная логистика и транспортные строки из фин. отчёта"},
        {"name": "Приёмка", "amount": totals["handling"], "share": _div(totals["handling"], totals["revenue"]), "hint": "Платная приёмка и обработка"},
        {"name": "Хранение", "amount": totals["storage"], "share": _div(totals["storage"], totals["revenue"]), "hint": "Хранение на складах WB"},
        {"name": "Штрафы", "amount": totals["fines"], "share": _div(totals["fines"], totals["revenue"]), "hint": "Штрафы и санкции WB"},
        {"name": "Прочие WB", "amount": totals["other"], "share": _div(totals["other"], totals["revenue"]), "hint": "Удержания, которые не попали в логистику/приёмку/хранение"},
        {"name": "Неразложенная часть", "amount": totals["unallocated"], "share": _div(totals["unallocated"], totals["revenue"]), "hint": "Разница между общими WB удержаниями и распознанными статьями"},
    ]
    for item in by_sku.values():
        item["unallocated"] = item["wb_expenses"] - item["detailed_total"]
        item["wb_share"] = _div(item["wb_expenses"], item["revenue"])
        item["logistics_per_unit"] = _div(item["logistics_total"], item["quantity"])
        item["handling_per_unit"] = _div(item["handling"], item["quantity"])
        item["storage_per_unit"] = _div(item["storage"], item["quantity"])
    sku_rows = sorted(by_sku.values(), key=lambda x: (abs(float(x.get("wb_expenses") or 0)), abs(float(x.get("logistics_total") or 0))), reverse=True)[:50]
    return totals, breakdown, sku_rows


def _safe_abs(value: Any) -> float:
    try:
        return abs(float(value or 0.0))
    except Exception:
        return 0.0


def _build_expense_structure(report: Any | None, rows: list[Any] | None = None, *, base: str = "revenue") -> SimpleNamespace:
    """Prepare expense cards for dashboard.

    Important: commission/primary WB withholding is calculated as revenue minus payout.
    It is shown as an explanatory category and is NOT subtracted again from profit.
    Separate WB expenses come from sale operation rows: logistics, storage, handling,
    fines and other withholdings.
    """
    if report is None:
        return SimpleNamespace(rows=[], total_expenses=0.0, revenue=0.0, base=base, base_label="—")

    revenue = float(getattr(report, "revenue", 0.0) or 0.0)
    payout = float(getattr(report, "payout", 0.0) or 0.0)
    op_totals = None
    if rows:
        try:
            op_totals, _breakdown, _sku_rows = _operation_logistics_details(rows)
        except Exception:
            op_totals = None

    def op_amount(key: str) -> float:
        if not op_totals:
            return 0.0
        return _safe_abs(op_totals.get(key, 0.0))

    main_wb = max(revenue - payout, 0.0)
    other_wb = op_amount("other") + op_amount("unallocated")
    categories = [
        ("Себестоимость", _safe_abs(getattr(report, "cogs", 0.0)), "Закупочная стоимость проданных товаров"),
        ("Основное удержание WB", main_wb, "Разница между доходом и суммой к перечислению: здесь обычно сидит комиссия/вознаграждение WB"),
        ("Логистика WB", op_amount("logistics_total"), "Доставка, обратная логистика и транспортные строки из фин. отчёта"),
        ("Хранение", op_amount("storage"), "Хранение на складах WB"),
        ("Приёмка", op_amount("handling"), "Платная приёмка и обработка"),
        ("Реклама", _safe_abs(getattr(report, "advertising", 0.0)), "Расходы WB Продвижение"),
        ("УСН / налоги", _safe_abs(getattr(report, "tax", 0.0)), "Налог с дохода по твоей ставке"),
        ("Штрафы", op_amount("fines"), "Штрафы и санкции WB"),
        ("Прочие WB", other_wb, "Прочие и неразложенные удержания WB"),
        ("Внешние расходы", _safe_abs(getattr(report, "external_expenses", 0.0)), "Упаковка, доставка до МП, фулфилмент и другие расходы вне WB"),
    ]
    rows_out = []
    total_expenses = sum(amount for _name, amount, _hint in categories if amount > 0)
    for name, amount, hint in categories:
        share_revenue = _div(amount, revenue)
        share_expenses = _div(amount, total_expenses)
        rows_out.append(SimpleNamespace(
            name=name,
            amount=amount,
            share_revenue=share_revenue,
            share_expenses=share_expenses,
            display_share=share_expenses if base == "expenses" else share_revenue,
            bar_width=max(0, min(100, (share_expenses if base == "expenses" else share_revenue) * 100)),
            hint=hint,
            cls="bad" if name in {"Штрафы"} and amount > 0 else "",
        ))
    rows_out.sort(key=lambda item: item.amount, reverse=True)
    return SimpleNamespace(
        rows=rows_out,
        total_expenses=total_expenses,
        revenue=revenue,
        base=base,
        base_label="от всех расходов" if base == "expenses" else "от выручки",
    )


def _query_int(name: str, default: int, *, minimum: int = 1, maximum: int = 365) -> int:
    raw = request.args.get(name, "").strip()
    if not raw:
        return int(default)
    try:
        value = int(float(raw.replace(",", ".")))
    except ValueError:
        return int(default)
    return max(minimum, min(value, maximum))


def _norm_key(value: str) -> str:
    return " ".join((value or "").strip().casefold().split())


def _as_int_stock(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


async def _load_current_stock_records() -> tuple[dict[tuple[str, Any], dict[str, Any]], str, list[str]]:
    token = os.getenv("WB_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("WB_API_TOKEN не задан в Railway Variables.")

    cards, wb_rows = await asyncio.gather(get_product_cards(token), get_wb_warehouse_stocks(token))
    card_by_nm: dict[int, Any] = {int(card.nm_id): card for card in cards if int(card.nm_id) > 0}
    nm_by_chrt: dict[int, int] = {}
    for card in cards:
        for chrt_id in getattr(card, "chrt_ids", ()):
            nm_by_chrt[int(chrt_id)] = int(card.nm_id)

    records_by_nm: dict[int, dict[str, Any]] = {}

    def get_record(nm_id: int) -> dict[str, Any]:
        nm_id = int(nm_id)
        if nm_id not in records_by_nm:
            card = card_by_nm.get(nm_id)
            records_by_nm[nm_id] = {
                "nm_id": nm_id,
                "sku": (getattr(card, "vendor_code", "") or f"WB-{nm_id}").strip(),
                "name": (getattr(card, "title", "") or f"Товар WB {nm_id}").strip(),
                "fbw": 0,
                "fbs": 0,
                "to_client": 0,
                "from_client": 0,
            }
        return records_by_nm[nm_id]

    for row in wb_rows:
        if not isinstance(row, dict):
            continue
        try:
            nm_id = int(row.get("nmId") or row.get("nmID") or 0)
        except (TypeError, ValueError):
            continue
        if nm_id <= 0:
            continue
        rec = get_record(nm_id)
        rec["fbw"] += max(0, _as_int_stock(row.get("quantity")))
        rec["to_client"] += max(0, _as_int_stock(row.get("inWayToClient")))
        rec["from_client"] += max(0, _as_int_stock(row.get("inWayFromClient")))

    warning = ""
    seller_names: list[str] = []
    try:
        warehouses = await get_seller_warehouses(token)
        seller_names = [w.name for w in warehouses]
        all_chrt_ids = sorted(nm_by_chrt.keys())
        for warehouse in warehouses:
            stock_by_chrt = await get_seller_warehouse_stocks(token, warehouse.warehouse_id, all_chrt_ids)
            for chrt_id, amount in stock_by_chrt.items():
                nm_id = nm_by_chrt.get(int(chrt_id))
                if not nm_id:
                    continue
                get_record(nm_id)["fbs"] += max(0, int(amount or 0))
    except Exception as exc:
        warning = f"FBS-остатки не загружены полностью: {exc}"

    result: dict[tuple[str, Any], dict[str, Any]] = {}
    for nm_id, rec in records_by_nm.items():
        result[("nm", nm_id)] = rec
        if rec.get("sku"):
            result[("sku", _norm_key(rec["sku"]))] = rec
    return result, warning, seller_names


def _stock_key(nm_id: int | None, sku: str) -> tuple[str, Any]:
    if nm_id:
        return ("nm", int(nm_id))
    return ("sku", _norm_key(sku))


def _fmt_days(value: float | None) -> str:
    if value is None:
        return "—"
    if value < 1:
        return "< 1 дн."
    return f"{value:.1f} дн.".replace(".", ",")


def _safe_today() -> date:
    return date.today()



TARIFF_BOX_URL = "https://common-api.wildberries.ru/api/v1/tariffs/box"
TARIFF_PALLET_URL = "https://common-api.wildberries.ru/api/v1/tariffs/pallet"
TARIFF_RETURN_URL = "https://common-api.wildberries.ru/api/v1/tariffs/return"

COMMON_FBS_POINTS = [
    "СЦ Владикавказ",
    "СЦ Ногир",
    "СЦ Грозный",
    "ПВЗ / пункт выдачи",
    "Другой FBS-СЦ",
]


def _query_tariff_date() -> str:
    raw = request.args.get("tariff_date", "").strip()
    try:
        if raw:
            return date.fromisoformat(raw).isoformat()
    except ValueError:
        pass
    return date.today().isoformat()


def _ru_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\u00a0", " ")
    text = text.replace("руб.", "").replace("₽", "").replace("%", "").strip()
    text = text.replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _wb_tariff_headers() -> dict[str, str]:
    token = os.getenv("WB_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("WB_API_TOKEN не задан в Railway Variables.")
    return {
        "Authorization": token,
        "Accept": "application/json",
        "User-Agent": "WB-Profit-Dashboard/27.0",
    }


def _wb_get_json(url: str, params: dict[str, str]) -> Any:
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{query}", headers=_wb_tariff_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        retry = exc.headers.get("X-Ratelimit-Retry") or exc.headers.get("Retry-After")
        if exc.code == 401:
            raise RuntimeError("WB отклонил токен. Проверь WB_API_TOKEN.") from exc
        if exc.code == 429:
            raise RuntimeError(f"Слишком частый запрос к тарифам WB. Повтори позже{f' через {retry} сек.' if retry else '.'}") from exc
        raise RuntimeError(f"WB вернул ошибку {exc.code} при загрузке тарифов.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Не удалось соединиться с WB API тарифов.") from exc
    except TimeoutError as exc:
        raise RuntimeError("WB API тарифов не ответил вовремя.") from exc


def _warehouse_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    response = payload.get("response")
    data = response.get("data") if isinstance(response, dict) else payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("warehouseList"), list):
        return [r for r in data.get("warehouseList") if isinstance(r, dict)]
    if isinstance(payload.get("warehouseList"), list):
        return [r for r in payload.get("warehouseList") if isinstance(r, dict)]
    return []


def _find_warehouse(rows: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    q = (query or "").strip().casefold()
    if not q:
        return None
    for row in rows:
        name = str(row.get("warehouseName") or row.get("name") or "").casefold()
        if name == q:
            return row
    for row in rows:
        name = str(row.get("warehouseName") or row.get("name") or "").casefold()
        if q in name or name in q:
            return row
    return None


def _load_tariff_warehouse_names(package_type: str, tariff_date: str) -> tuple[list[str], DotDict | None]:
    """Return warehouse names from WB tariff API for selector in unit calculator."""
    try:
        url = TARIFF_PALLET_URL if package_type == "mono" else TARIFF_BOX_URL
        payload = _wb_get_json(url, {"date": tariff_date})
        names = []
        seen = set()
        for row in _warehouse_rows(payload):
            name = str(row.get("warehouseName") or row.get("name") or "").strip()
            if name and name.casefold() not in seen:
                names.append(name)
                seen.add(name.casefold())
        names.sort(key=lambda x: x.casefold())
        return names, None
    except RuntimeError as exc:
        return [], DotDict({"kind": "error", "text": f"Не удалось загрузить список складов WB: {exc}"})


def _apply_wb_tariffs(values: DotDict) -> tuple[DotDict, DotDict]:
    if str(values.get("warehouse_mode") or "api") == "manual":
        return values, DotDict({"kind": "ok", "text": "Выбран ручной FBS-склад. Тарифный API WB для него не применяется: укажи логистику вручную или нажми «Подставить фактическую логистику из продаж»."})
    warehouse = str(values.warehouse or "").strip()
    if not warehouse:
        return values, DotDict({"kind": "error", "text": "Выбери склад WB из списка, затем нажми «Подтянуть тарифы WB». Для СЦ Владикавказ используй режим «Мой FBS-склад вручную»."})
    tariff_date = str(values.tariff_date or date.today().isoformat())
    package_type = str(values.package_type or "box")
    try:
        if package_type == "mono":
            payload = _wb_get_json(TARIFF_PALLET_URL, {"date": tariff_date})
            rows = _warehouse_rows(payload)
            row = _find_warehouse(rows, warehouse)
            if row is None:
                names = ", ".join(str(r.get("warehouseName")) for r in rows[:8] if r.get("warehouseName"))
                return values, DotDict({"kind": "error", "text": f"Склад «{warehouse}» не найден в тарифах паллет. Примеры складов: {names or 'нет данных'}."})
            raw_base = _ru_float(row.get("palletDeliveryValueBase"))
            raw_liter = _ru_float(row.get("palletDeliveryValueLiter"))
            coef = _ru_float(row.get("palletDeliveryExpr")) or 100.0
            coef_mult = coef / 100.0 if coef > 10 else coef
            if raw_base:
                values.base_logistics = raw_base * coef_mult
            if raw_liter:
                values.extra_liter_cost = raw_liter * coef_mult
            values.localization_pct = 1.0
            storage_value = _ru_float(row.get("palletStorageValueExpr"))
            if storage_value:
                values.storage_per_day = storage_value
        else:
            payload = _wb_get_json(TARIFF_BOX_URL, {"date": tariff_date})
            rows = _warehouse_rows(payload)
            row = _find_warehouse(rows, warehouse)
            if row is None:
                names = ", ".join(str(r.get("warehouseName")) for r in rows[:8] if r.get("warehouseName"))
                return values, DotDict({"kind": "error", "text": f"Склад «{warehouse}» не найден в тарифах коробов. Примеры складов: {names or 'нет данных'}."})
            if str(values.scheme or "fbs") == "fbs":
                base_key, liter_key, coef_key = "boxDeliveryMarketplaceBase", "boxDeliveryMarketplaceLiter", "boxDeliveryMarketplaceCoefExpr"
            else:
                base_key, liter_key, coef_key = "boxDeliveryBase", "boxDeliveryLiter", "boxDeliveryCoefExpr"
            raw_base = _ru_float(row.get(base_key)) or _ru_float(row.get("boxDeliveryBase"))
            raw_liter = _ru_float(row.get(liter_key)) or _ru_float(row.get("boxDeliveryLiter"))
            coef = _ru_float(row.get(coef_key)) or _ru_float(row.get("boxDeliveryCoefExpr")) or 100.0
            coef_mult = coef / 100.0 if coef > 10 else coef
            if raw_base:
                values.base_logistics = raw_base * coef_mult
            if raw_liter:
                values.extra_liter_cost = raw_liter * coef_mult
            # Тариф уже умножен на коэффициент склада, поэтому поле локализации оставляем 100%.
            values.localization_pct = 1.0
            storage_base = _ru_float(row.get("boxStorageBase"))
            storage_liter = _ru_float(row.get("boxStorageLiter"))
            volume = max(0.0, float(values.length_cm or 0) * float(values.width_cm or 0) * float(values.height_cm or 0) / 1000.0)
            if storage_base or storage_liter:
                values.storage_per_day = storage_base + max(0.0, volume - 1.0) * storage_liter

        try:
            ret_payload = _wb_get_json(TARIFF_RETURN_URL, {"date": tariff_date})
            ret_row = _find_warehouse(_warehouse_rows(ret_payload), warehouse)
            if ret_row:
                values.return_logistics = (
                    _ru_float(ret_row.get("deliveryDumpSrgReturnExpr"))
                    or _ru_float(ret_row.get("deliveryDumpSupReturnExpr"))
                    or _ru_float(ret_row.get("deliveryDumpKgtReturnExpr"))
                    or values.return_logistics
                )
        except RuntimeError:
            # Возврат не критичен для расчёта, оставляем прежнее значение.
            pass
        msg = (
            f"Подтянул тарифы WB для склада «{warehouse}» на {tariff_date}: "
            f"база логистики {values.base_logistics:g} ₽, доп. литр {values.extra_liter_cost:g} ₽, "
            f"хранение {values.storage_per_day:g} ₽/день, "
            f"обратная логистика {values.return_logistics:g} ₽. Тариф уже подтянут как итоговый для склада; поправку к тарифу обычно оставляй 100%. Проверь комиссию и приёмку вручную."
        )
        return values, DotDict({"kind": "ok", "text": msg})
    except RuntimeError as exc:
        return values, DotDict({"kind": "error", "text": str(exc)})



def _apply_actual_logistics_from_sales(values: "DotDict") -> tuple["DotDict", "DotDict"]:
    sku = str(values.sku or "").strip()
    query = sku or str(values.product_name or "").strip()
    if not query:
        return values, DotDict({"kind": "error", "text": "Выбери товар или укажи артикул/название, чтобы взять фактическую логистику из истории продаж."})
    try:
        rows = list_sale_operations(limit=5000, period_type="weekly", query=query, operation_type="sale")
    except Exception as exc:
        logger.warning("failed to load sale operations for actual logistics", exc_info=True)
        return values, DotDict({"kind": "error", "text": f"Не удалось загрузить историю продаж из PostgreSQL: {exc}"})
    if sku:
        key = sku.casefold()
        rows = [r for r in rows if str(getattr(r, "sku", "") or "").casefold() == key]
    rows = [r for r in rows if float(getattr(r, "quantity", 0) or 0) > 0]
    if not rows:
        return values, DotDict({"kind": "error", "text": "По выбранному товару нет сохранённых продаж. Сначала загрузи историю через /backfill или выбери другой товар."})
    qty = sum(abs(float(getattr(r, "quantity", 0) or 0)) for r in rows) or 1.0
    logistics = sum(abs(float(getattr(r, "logistics", 0) or 0)) for r in rows)
    handling = sum(abs(float(getattr(r, "handling", 0) or 0)) for r in rows)
    storage = sum(abs(float(getattr(r, "storage", 0) or 0)) for r in rows)
    other = sum(abs(float(getattr(r, "other_withholdings", 0) or 0)) + abs(float(getattr(r, "fines", 0) or 0)) for r in rows)
    wb_exp = sum(abs(float(getattr(r, "wb_expenses", 0) or 0)) for r in rows)
    # If detailed logistics has not been split yet, do not invent it from total WB expenses.
    # Keep commission separate and only fill fields that are actually detailed in sales history.
    if logistics > 0:
        values.base_logistics = logistics / qty
        values.extra_liter_cost = 0.0
        values.return_logistics = 0.0
    if handling > 0:
        values.acceptance = handling / qty
    if storage > 0:
        values.turnover_days = 1.0
        values.storage_per_day = storage / qty
    if other > 0:
        values.tariff_options = float(values.tariff_options or 0) + other / qty
    if logistics <= 0 and handling <= 0 and storage <= 0 and other <= 0:
        return values, DotDict({"kind": "error", "text": "В истории продаж есть товар, но детальная логистика/приёмка/хранение пока равны 0. Запусти /backfill после обновления v25 или укажи тарифы вручную."})
    return values, DotDict({"kind": "ok", "text": f"Подставил фактические расходы из {len(rows)} операций: логистика {logistics/qty:.2f} ₽/шт., приёмка {handling/qty:.2f} ₽/шт., хранение {storage/qty:.2f} ₽/шт., прочие WB {other/qty:.2f} ₽/шт. Общие WB-удержания в истории: {wb_exp/qty:.2f} ₽/шт."})


class DotDict(dict):
    __getattr__ = dict.get


def _unit_calculator_values(product_summary=None) -> DotDict:
    units = float(getattr(product_summary, "units", 0) or 0)
    revenue = float(getattr(product_summary, "revenue", 0) or 0)
    payout = float(getattr(product_summary, "payout", 0) or 0)
    cogs = float(getattr(product_summary, "cogs", 0) or 0)
    drr = float(getattr(product_summary, "drr", 0) or 0)
    avg_price = _div(revenue, units)
    approx_commission = max(0.0, _div(revenue - payout, revenue)) if revenue else 0.0
    selected_sku = request.args.get("sku", "").strip()
    selected_name = request.args.get("product_name", "").strip()
    if not selected_name and product_summary is not None:
        selected_name = getattr(product_summary, "name", "") or getattr(product_summary, "sku", "")
    return DotDict({
        "sku": selected_sku,
        "product_name": selected_name,
        "scheme": request.args.get("scheme", "fbs").strip().lower() if request.args.get("scheme", "fbs").strip().lower() in {"fbs", "fbw"} else "fbs",
        "calc_mode": request.args.get("calc_mode", "plan").strip().lower() if request.args.get("calc_mode", "plan").strip().lower() in {"plan", "fact", "scenario"} else "plan",
        "price_spp": _query_float("price_spp", avg_price),
        "spp_pct": _query_rate("spp_pct", 0),
        "buyout_pct": _query_rate("buyout_pct", 1, min_value=0.0001, max_value=1),
        "commission_pct": _query_rate("commission_pct", approx_commission),
        "purchase_qty": max(0.0, _query_float("purchase_qty", 1)),
        "purchase_price": _query_float("purchase_price", _div(cogs, units)),
        "package_type": request.args.get("package_type", "box").strip().lower() if request.args.get("package_type", "box").strip().lower() in {"box", "mono"} else "box",
        "warehouse_mode": request.args.get("warehouse_mode", "api").strip().lower() if request.args.get("warehouse_mode", "api").strip().lower() in {"api", "manual"} else "api",
        "warehouse": request.args.get("warehouse", "").strip()[:100],
        "tariff_date": _query_tariff_date(),
        "localization_pct": max(0.0001, _query_rate("localization_pct", 1, min_value=0, max_value=10) or 1),
        "irp": 1.0,
        "length_cm": max(0.0, _query_float("length_cm", 0)),
        "width_cm": max(0.0, _query_float("width_cm", 0)),
        "height_cm": max(0.0, _query_float("height_cm", 0)),
        "base_logistics": max(0.0, _query_float("base_logistics", 0)),
        "extra_liter_cost": max(0.0, _query_float("extra_liter_cost", 0)),
        "return_logistics": max(0.0, _query_float("return_logistics", 0)),
        "turnover_days": max(0.0, _query_float("turnover_days", 0)),
        "storage_per_day": max(0.0, _query_float("storage_per_day", 0)),
        "acceptance": max(0.0, _query_float("acceptance", 0)),
        "vat_pct": _query_rate("vat_pct", 0, min_value=0, max_value=1),
        "tax_pct": _query_rate("tax_pct", 0.06, min_value=0, max_value=1),
        "mp_delivery": max(0.0, _query_float("mp_delivery", 0)),
        "packaging_cost": max(0.0, _query_float("packaging_cost", 0)),
        "drr_pct": _query_rate("drr_pct", drr, min_value=0, max_value=1),
        "tariff_options": max(0.0, _query_float("tariff_options", 0)),
        "acquiring_pct": _query_rate("acquiring_pct", 0, min_value=0, max_value=1),
        "other_cost": max(0.0, _query_float("other_cost", 0)),
        "defect_pct": _query_rate("defect_pct", 0, min_value=0, max_value=1),
        "target_margin": _query_rate("target_margin", 0.15, min_value=0, max_value=1),
    })


def _calculate_unit_plan(values: DotDict) -> tuple[DotDict, list[dict[str, str]]]:
    price_spp = max(0.0, float(values.price_spp or 0))
    spp_rate = max(0.0, min(0.999999, float(values.spp_pct or 0)))
    # Buyer-facing price after WB SPP is restored to seller's calculation price before SPP.
    # This avoids subtracting WB commissions/taxes from an already discounted buyer price.
    price = price_spp / (1.0 - spp_rate) if spp_rate > 0 else price_spp
    buyout = max(0.0001, min(1.0, float(values.buyout_pct or 0)))
    volume_liters = max(0.0, float(values.length_cm or 0) * float(values.width_cm or 0) * float(values.height_cm or 0) / 1000.0)
    base_logistics = float(values.base_logistics or 0) + max(0.0, volume_liters - 1.0) * float(values.extra_liter_cost or 0)
    # Тариф уже подтянут как итоговый для склада. Поправка к тарифу нужна только для ручной корректировки.
    localization_coef = float(values.localization_pct or 1.0)
    if localization_coef <= 0:
        localization_coef = 1.0
    outbound_logistics = base_logistics * localization_coef
    # Плановая юнитка: прямая логистика к покупателю не делится на процент выкупа.
    # Процент выкупа распределяет только обратную логистику/невыкупы на одну выкупленную единицу.
    return_burden = float(values.return_logistics or 0) * (1.0 - buyout) / buyout
    logistics_per_buyout = outbound_logistics + return_burden
    storage = float(values.turnover_days or 0) * float(values.storage_per_day or 0)
    purchase = float(values.purchase_price or 0)
    commission = price * float(values.commission_pct or 0)
    advertising = price * float(values.drr_pct or 0)
    acquiring = price * float(values.acquiring_pct or 0)
    vat_effective_rate = (float(values.vat_pct or 0) / (1.0 + float(values.vat_pct or 0))) if float(values.vat_pct or 0) > 0 else 0.0
    vat = price * vat_effective_rate
    tax = price * float(values.tax_pct or 0)
    defect = purchase * float(values.defect_pct or 0)
    wb_expenses = commission + logistics_per_buyout + storage + float(values.acceptance or 0) + float(values.tariff_options or 0) + acquiring
    direct_costs = purchase + float(values.mp_delivery or 0) + float(values.packaging_cost or 0) + float(values.other_cost or 0) + defect
    profit_before_ads_and_tax = price - wb_expenses - direct_costs
    profit_before_tax = profit_before_ads_and_tax - advertising
    net_profit = profit_before_tax - tax - vat
    total_costs = price - net_profit
    margin = _div(net_profit, price)
    roi_base = max(0.0001, purchase + float(values.mp_delivery or 0) + float(values.packaging_cost or 0))
    roi = _div(net_profit, roi_base)
    rom = _div(net_profit, total_costs)
    sold_units = float(values.purchase_qty or 0)
    batch_revenue = price * sold_units
    batch_net_profit = net_profit * sold_units
    investment = roi_base * float(values.purchase_qty or 0)
    price_before_spp = price
    spp_discount_amount = max(0.0, price_before_spp - price_spp)
    fixed_costs = logistics_per_buyout + storage + float(values.acceptance or 0) + purchase + float(values.mp_delivery or 0) + float(values.packaging_cost or 0) + float(values.tariff_options or 0) + float(values.other_cost or 0) + defect
    percent_costs_no_drr = float(values.commission_pct or 0) + float(values.acquiring_pct or 0) + float(values.tax_pct or 0) + vat_effective_rate
    max_drr_zero = 1.0 - percent_costs_no_drr - _div(fixed_costs, price)
    max_drr_target = max_drr_zero - float(values.target_margin or 0)
    denom_zero = 1.0 - percent_costs_no_drr - float(values.drr_pct or 0)
    break_even_price = fixed_costs / denom_zero if denom_zero > 0.000001 else None
    denom_target = denom_zero - float(values.target_margin or 0)
    target_price = fixed_costs / denom_target if denom_target > 0.000001 else None
    calc = DotDict({
        "price": price,
        "price_spp": price_spp,
        "price_before_spp": price_before_spp,
        "spp_discount_amount": spp_discount_amount,
        "volume_liters": volume_liters,
        "outbound_logistics": outbound_logistics,
        "return_burden": return_burden,
        "logistics_per_buyout": logistics_per_buyout,
        "storage": storage,
        "commission": commission,
        "advertising": advertising,
        "acquiring": acquiring,
        "vat": vat,
        "tax": tax,
        "defect": defect,
        "wb_expenses": wb_expenses,
        "direct_costs": direct_costs,
        "profit_before_ads_and_tax": profit_before_ads_and_tax,
        "profit_before_tax": profit_before_tax,
        "net_profit": net_profit,
        "total_costs": total_costs,
        "margin": margin,
        "roi": _div(batch_net_profit, investment) if investment else roi,
        "rom": rom,
        "max_drr_zero": max_drr_zero,
        "max_drr_target": max_drr_target,
        "break_even_price": break_even_price,
        "target_price": target_price,
        "sold_units": sold_units,
        "batch_revenue": batch_revenue,
        "batch_net_profit": batch_net_profit,
        "investment": investment,
        "status": "Выгодно" if net_profit >= 0 else "В минусе",
    })
    def row(label: str, unit_value: float, *, negative: bool = True, units_label: str = "money") -> dict[str, str]:
        cls = "profit-neg" if negative and unit_value > 0 else "profit-pos" if (not negative and unit_value >= 0) else ""
        batch_value = unit_value * sold_units
        if units_label == "qty":
            unit_text = _units(unit_value)
            batch_text = _units(batch_value)
            share = "—"
        elif units_label == "percent":
            unit_text = _percent(unit_value)
            batch_text = "—"
            share = "—"
        else:
            unit_text = _money(unit_value if not negative else -unit_value)
            batch_text = _money(batch_value if not negative else -batch_value)
            share = _percent(_div(unit_value, price)) if price else "—"
        return {"label": label, "unit": unit_text, "batch": batch_text, "share": share, "cls": cls}
    breakdown = [
        row("Объём продаж, шт.", 1, negative=False, units_label="qty"),
        row("Выручка в ценах реализации до СПП", price, negative=False),
        row("Цена покупателя с СПП", price_spp, negative=False),
        row("СПП / скидка WB", spp_discount_amount),
        row("Расходы всего", total_costs),
        row("Вложения в товар", purchase),
        row("Комиссия WB", commission),
        row("Прямая логистика WB", outbound_logistics),
        row("Невыкуп / обратная логистика", return_burden),
        row("Логистика WB всего", logistics_per_buyout),
        row("Хранение", storage),
        row("Приёмка", float(values.acceptance or 0)),
        row("Реклама", advertising),
        row("Эквайринг", acquiring),
        row("Логистика до МП", float(values.mp_delivery or 0)),
        row("Упаковка", float(values.packaging_cost or 0)),
        row("Доп. тарифные опции WB", float(values.tariff_options or 0)),
        row("Прочие расходы", float(values.other_cost or 0)),
        row("Брак", defect),
        row("Прибыль до налогов", profit_before_tax, negative=False),
        row("НДС", vat),
        row("Налог", tax),
        row("Чистая прибыль", net_profit, negative=False),
        {"label": "ROI", "unit": _percent(calc.roi), "batch": "—", "share": "—", "cls": "profit-pos" if calc.roi >= 0 else "profit-neg"},
        {"label": "ROM", "unit": _percent(calc.rom), "batch": "—", "share": "—", "cls": "profit-pos" if calc.rom >= 0 else "profit-neg"},
        {"label": "ROS", "unit": _percent(calc.margin), "batch": "—", "share": "—", "cls": "profit-pos" if calc.margin >= 0 else "profit-neg"},
    ]
    return calc, breakdown





AI_ANALYST_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>AI-аналитик · WB Profit</title><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>AI-аналитик</h1><div class="subtitle">Автоматический разбор прибыли, выручки, ДРР, маржи, расходов WB и проблемных SKU</div></div><div class="badge">Период: {{ date_display(date_from) }} — {{ date_display(date_to) }}</div></header>
<nav class="nav"><a href="/">Дашборд</a><a href="/products">Товары</a><a href="/sales">История продаж</a><a href="/reconcile">Сверка</a><a href="/unit-economics">Юнит-экономика</a><a href="/unit-matrix">SKU-юнитка</a><a href="/unit-calculator">Калькулятор юнитки</a><a href="/supply-planner">Поставки</a><a href="/logistics">Логистика</a><a class="active" href="/ai-analyst">AI-аналитик</a><a href="/ai-chat">AI-чат</a><a href="/plan-fact">План-факт</a><a href="/admin">Себестоимость и расходы</a></nav>
<form class="filters" method="get">
<label>С даты <input type="date" name="date_from" value="{{ date_from.isoformat() }}"></label><label>По дату <input type="date" name="date_to" value="{{ date_to.isoformat() }}"></label>
<label>Мин. маржа, % <input inputmode="decimal" name="min_margin" value="{{ percent_input(min_margin) }}"></label><label>Макс. ДРР, % <input inputmode="decimal" name="max_drr" value="{{ percent_input(max_drr) }}"></label>
<button type="submit">Проанализировать</button><a class="button secondary" href="/ai-analyst">Сбросить</a></form>
<div class="grid">
<div class="card"><div class="label">Выручка</div><div class="value">{{ money(current.revenue) }}</div><div class="subtitle">{{ delta_text(current.revenue, previous.revenue, true) }}</div></div>
<div class="card"><div class="label">Чистая прибыль</div><div class="value {{ 'good' if current.profit >= 0 else 'bad' }}">{{ money(current.profit) }}</div><div class="subtitle">{{ delta_text(current.profit, previous.profit, true) }}</div></div>
<div class="card"><div class="label">Маржа</div><div class="value {{ margin_class(current.margin) }}">{{ percent(current.margin) }}</div><div class="subtitle">Предыдущий период: {{ percent(previous.margin) }}</div></div>
<div class="card"><div class="label">ДРР</div><div class="value {{ 'bad' if current.drr > max_drr and current.drr > 0 else 'good' }}">{{ percent(current.drr) }}</div><div class="subtitle">Реклама: {{ money(current.advertising) }}</div></div>
<div class="card"><div class="label">Продажи</div><div class="value">{{ units(current.quantity) }} шт.</div><div class="subtitle">Операций: {{ current.operations }}</div></div>
</div>
<div class="two-col section">
<div class="card"><h2>Главные выводы</h2>{% if insights %}<div style="display:grid;gap:10px;margin-top:12px">{% for i in insights %}<div class="notice {{ i.kind }}"><b>{{ i.title }}</b><br>{{ i.text }}</div>{% endfor %}</div>{% else %}<div class="empty">Критичных сигналов нет. Данные выглядят стабильно.</div>{% endif %}</div>
<div class="card"><h2>Что проверить в первую очередь</h2><div style="display:grid;gap:10px;margin-top:12px">{% for a in actions %}<div class="hint" style="margin:0"><b>{{ loop.index }}. {{ a.title }}</b><br>{{ a.text }}</div>{% endfor %}</div></div>
</div>
<div class="section card"><div class="section-head"><div><h2>Динамика по периодам</h2><div class="subtitle">Сохранённые отчёты внутри выбранного периода</div></div></div><div class="chart-box"><canvas id="aiChart"></canvas></div></div>
<div class="two-col section">
<div class="card"><h2>Проблемные товары</h2><div class="table-wrap"><table><thead><tr><th>Товар</th><th>Артикул</th><th>Шт.</th><th>Доход</th><th>Реклама</th><th>ДРР</th><th>Себес</th><th>Прибыль</th><th>Маржа</th><th>Сигнал</th></tr></thead><tbody>{% for row in problem_skus %}<tr><td>{{ row.name }}</td><td>{{ row.sku }}</td><td>{{ units(row.units) }}</td><td>{{ money(row.revenue) }}</td><td>{{ money(row.advertising) }}</td><td>{{ percent(row.drr) }}</td><td>{{ money(row.cogs) }}</td><td class="{{ 'profit-pos' if row.profit >= 0 else 'profit-neg' }}">{{ money(row.profit) }}</td><td class="{{ margin_class(row.margin) }}">{{ percent(row.margin) }}</td><td>{{ row.signal }}</td></tr>{% endfor %}{% if not problem_skus %}<tr><td colspan="10" class="empty">Нет проблемных SKU по заданным порогам.</td></tr>{% endif %}</tbody></table></div></div>
<div class="card"><h2>Топ товаров по прибыли</h2><div class="table-wrap"><table style="min-width:720px"><thead><tr><th>Товар</th><th>Шт.</th><th>Доход</th><th>Прибыль</th><th>Маржа</th></tr></thead><tbody>{% for row in top_skus %}<tr><td>{{ row.name }}<br><span class="muted">{{ row.sku }}</span></td><td>{{ units(row.units) }}</td><td>{{ money(row.revenue) }}</td><td class="profit-pos">{{ money(row.profit) }}</td><td class="{{ margin_class(row.margin) }}">{{ percent(row.margin) }}</td></tr>{% endfor %}{% if not top_skus %}<tr><td colspan="5" class="empty">Нет продаж за период.</td></tr>{% endif %}</tbody></table></div></div>
</div>
<div class="section card"><h2>Расшифровка расходов</h2><div class="grid" style="margin-top:12px"><div class="card"><div class="label">Расчётная выплата</div><div class="value">{{ money(current.payout) }}</div></div><div class="card"><div class="label">WB удержания</div><div class="value">{{ money(current.wb_expenses) }}</div></div><div class="card"><div class="label">Себестоимость</div><div class="value">{{ money(current.cogs) }}</div></div><div class="card"><div class="label">Внешние расходы</div><div class="value">{{ money(current.external_expenses) }}</div></div><div class="card"><div class="label">УСН</div><div class="value">{{ money(current.tax) }}</div></div></div><div class="hint">Это пока rule-based аналитик: он не отправляет данные во внешние AI API, а делает выводы по твоей PostgreSQL-истории. На следующем этапе можно подключить настоящий AI-чат по данным.</div></div>
<script>
const labels = {{ chart_labels|tojson }};
const profit = {{ chart_profit|tojson }};
const revenue = {{ chart_revenue|tojson }};
new Chart(document.getElementById('aiChart'), {type:'bar', data:{labels, datasets:[{label:'Чистая прибыль', data:profit, yAxisID:'y'}, {label:'Доход покупателей', data:revenue, type:'line', yAxisID:'y1'}]}, options:{responsive:true, maintainAspectRatio:false, interaction:{mode:'index', intersect:false}, scales:{y:{beginAtZero:true, grid:{color:'rgba(255,255,255,.08)'}}, y1:{position:'right', beginAtZero:true, grid:{drawOnChartArea:false}}}, plugins:{legend:{labels:{color:'#f5f7fb'}}}}});
</script>
<footer>AI-аналитик использует сохранённые недельные и дневные данные. Для свежей недели запусти /syncdaily.</footer></div></body></html>
"""

AI_CHAT_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>AI-чат · WB Profit</title>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>AI-чат</h1><div class="subtitle">Настоящий AI-анализ по данным PostgreSQL: продажи, прибыль, ДРР, маржа, расходы, проблемные SKU</div></div><div class="badge">{{ model_label }}</div></header>
<nav class="nav"><a href="/">Дашборд</a><a href="/products">Товары</a><a href="/sales">История продаж</a><a href="/reconcile">Сверка</a><a href="/unit-economics">Юнит-экономика</a><a href="/unit-matrix">SKU-юнитка</a><a href="/unit-calculator">Калькулятор юнитки</a><a href="/supply-planner">Поставки</a><a href="/logistics">Логистика</a><a href="/ai-analyst">AI-аналитик</a><a class="active" href="/ai-chat">AI-чат</a><a href="/plan-fact">План-факт</a><a href="/admin">Себестоимость и расходы</a></nav>

<div class="section card">
  <form method="post" action="/ai-chat" class="filters">
    <label>С даты<input name="date_from" type="date" value="{{ date_from.isoformat() }}"></label>
    <label>По дату<input name="date_to" type="date" value="{{ date_to.isoformat() }}"></label>
    <label>Вопрос<textarea name="question" rows="4" style="min-width:520px;max-width:100%;resize:vertical" placeholder="Например: почему упала прибыль и какие товары проверить в первую очередь?">{{ question }}</textarea></label>
    <button type="submit">Спросить AI</button>
  </form>
  <div class="hint">AI получает только агрегированные данные выбранного периода: итоги, топ/антитоп SKU, расходы и динамику. Токены WB/Telegram/OpenAI в запрос не передаются.</div>
</div>

<div class="grid section">
  <div class="card"><div class="label">Выручка</div><div class="value">{{ money(summary.revenue) }}</div></div>
  <div class="card"><div class="label">Чистая прибыль</div><div class="value {{ 'bad' if summary.profit < 0 else 'good' }}">{{ money(summary.profit) }}</div></div>
  <div class="card"><div class="label">Маржа</div><div class="value {{ margin_class(summary.margin) }}">{{ percent(summary.margin) }}</div></div>
  <div class="card"><div class="label">ДРР</div><div class="value">{{ percent(summary.drr) }}</div><div class="hint">Реклама: {{ money(summary.advertising) }}</div></div>
  <div class="card"><div class="label">Продано</div><div class="value">{{ units(summary.quantity) }} шт.</div></div>
</div>

{% if not api_ready %}
<div class="section card" style="border-color:rgba(255,211,106,.35)">
  <h2>Нужно добавить OpenAI API-ключ</h2>
  <div class="hint">Добавь в Railway Variables переменную <b>OPENAI_API_KEY</b>. Опционально можно добавить <b>OPENAI_MODEL</b>, например <b>gpt-5.4-mini</b>.</div>
</div>
{% endif %}

{% if error %}<div class="section card" style="border-color:rgba(255,79,109,.45)"><h2>Ошибка AI</h2><div class="hint">{{ error }}</div></div>{% endif %}

{% if answer %}
<div class="section card"><h2>Ответ AI</h2><pre class="ai-answer" style="white-space:pre-wrap;line-height:1.55;font-family:inherit;font-size:15px;margin:0">{{ answer }}</pre></div>
{% else %}
<div class="section card"><h2>Примеры вопросов</h2><div class="table-wrap"><table style="min-width:800px"><tbody>
  <tr><td><a href="/ai-chat?date_from={{ date_from.isoformat() }}&date_to={{ date_to.isoformat() }}&question={{ q1 }}">Почему изменилась прибыль за период?</a></td></tr>
  <tr><td><a href="/ai-chat?date_from={{ date_from.isoformat() }}&date_to={{ date_to.isoformat() }}&question={{ q2 }}">Какие SKU проверить в первую очередь?</a></td></tr>
  <tr><td><a href="/ai-chat?date_from={{ date_from.isoformat() }}&date_to={{ date_to.isoformat() }}&question={{ q3 }}">Что сделать, чтобы поднять маржу?</a></td></tr>
</tbody></table></div></div>
{% endif %}

<div class="section card"><h2>Данные, которые передаются AI</h2><div class="hint">Период: {{ date_display(date_from) }} — {{ date_display(date_to) }}. SKU в контексте: {{ context_stats.sku_count }}, операций: {{ context_stats.operations }}. Данные обрезаются до топов, чтобы не раздувать стоимость запроса.</div></div>
<footer>WB Profit Dashboard · v33 · настоящий AI-чат через OpenAI API</footer></div></body></html>
"""

PLAN_FACT_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>План-факт · WB Profit</title><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>План-факт</h1><div class="subtitle">Сравнение фактической выручки, прибыли, продаж, маржи и ДРР с целями</div></div><div class="badge">Период: {{ date_display(date_from) }} — {{ date_display(date_to) }}</div></header>
<nav class="nav"><a href="/">Дашборд</a><a href="/products">Товары</a><a href="/sales">История продаж</a><a href="/reconcile">Сверка</a><a href="/unit-economics">Юнит-экономика</a><a href="/unit-matrix">SKU-юнитка</a><a href="/unit-calculator">Калькулятор юнитки</a><a href="/supply-planner">Поставки</a><a href="/logistics">Логистика</a><a href="/ai-analyst">AI-аналитик</a><a class="active" href="/plan-fact">План-факт</a><a href="/admin">Себестоимость и расходы</a></nav>
<form class="filters" method="get">
<label>С даты <input type="date" name="date_from" value="{{ date_from.isoformat() }}"></label>
<label>По дату <input type="date" name="date_to" value="{{ date_to.isoformat() }}"></label>
<label>План выручки, ₽ <input inputmode="decimal" name="revenue_target" value="{{ number_input(targets.revenue) }}"></label>
<label>План чистой прибыли, ₽ <input inputmode="decimal" name="profit_target" value="{{ number_input(targets.profit) }}"></label>
<label>План продаж, шт. <input inputmode="decimal" name="units_target" value="{{ number_input(targets.units) }}"></label>
<label>Целевая маржа, % <input inputmode="decimal" name="margin_target" value="{{ percent_input(targets.margin) }}"></label>
<label>Макс. ДРР, % <input inputmode="decimal" name="drr_target" value="{{ percent_input(targets.drr) }}"></label>
<button type="submit">Показать</button><a class="button secondary" href="/plan-fact">Сбросить</a>
</form>
<div class="hint">По умолчанию берётся текущий месяц: закрытые недельные отчёты + ежедневные отчёты текущей незакрытой недели без дублей. Если дневные отчёты ещё не догружены, отправь боту <b>/syncdaily</b>.</div>

<div class="grid section">
<div class="card"><div class="label">Факт: доход покупателей</div><div class="value">{{ money(actual.revenue) }}</div><div class="hint">План: {{ money(targets.revenue) if targets.revenue else 'не задан' }} · Выполнено: {{ percent(progress.revenue) if targets.revenue else '—' }}</div></div>
<div class="card"><div class="label">Факт: чистая прибыль</div><div class="value {{ 'good' if actual.profit >= 0 else 'bad' }}">{{ money(actual.profit) }}</div><div class="hint">План: {{ money(targets.profit) if targets.profit else 'не задан' }} · Выполнено: {{ percent(progress.profit) if targets.profit else '—' }}</div></div>
<div class="card"><div class="label">Факт: продажи</div><div class="value">{{ units(actual.quantity) }} шт.</div><div class="hint">План: {{ units(targets.units) + ' шт.' if targets.units else 'не задан' }} · Выполнено: {{ percent(progress.units) if targets.units else '—' }}</div></div>
<div class="card"><div class="label">Факт: маржа</div><div class="value {{ margin_class(actual.margin) }}">{{ percent(actual.margin) }}</div><div class="hint">Цель: {{ percent(targets.margin) if targets.margin else 'не задана' }} · {{ margin_status }}</div></div>
<div class="card"><div class="label">Факт: ДРР</div><div class="value {{ 'good' if not targets.drr or actual.drr <= targets.drr else 'bad' }}">{{ percent(actual.drr) }}</div><div class="hint">Лимит: {{ percent(targets.drr) if targets.drr else 'не задан' }} · {{ drr_status }}</div></div>
</div>

<div class="section two-col">
<div class="card"><div class="section-head"><div><h2>Прогноз до конца периода</h2><div class="subtitle">Расчёт по текущему среднему темпу за {{ elapsed_days }} дн. из {{ total_days }} дн.</div></div></div>
<div class="table-wrap"><table style="min-width:900px"><thead><tr><th>Показатель</th><th>Факт</th><th>Прогноз</th><th>План</th><th>Нужно в день</th><th>Статус</th></tr></thead><tbody>
{% for row in forecast_rows %}<tr><td>{{ row.name }}</td><td>{{ row.actual }}</td><td class="{{ row.forecast_class }}">{{ row.forecast }}</td><td>{{ row.target }}</td><td>{{ row.need_per_day }}</td><td class="{{ row.status_class }}">{{ row.status }}</td></tr>{% endfor %}
</tbody></table></div></div>
<div class="card"><h2>Структура факта</h2><div class="table-wrap"><table style="min-width:520px"><tbody>
<tr><td>Доход покупателей</td><td>{{ money(actual.revenue) }}</td></tr>
<tr><td>Расчётная выплата WB</td><td>{{ money(actual.payout) }}</td></tr>
<tr><td>WB удержания</td><td>{{ money(actual.wb_expenses) }}</td></tr>
<tr><td>Себестоимость</td><td>{{ money(actual.cogs) }}</td></tr>
<tr><td>Внешние расходы</td><td>{{ money(actual.external_expenses) }}</td></tr>
<tr><td>Реклама</td><td>{{ money(actual.advertising) }}</td></tr>
<tr><td>Прибыль до налога</td><td class="{{ 'profit-pos' if actual.profit_before_tax >= 0 else 'profit-neg' }}">{{ money(actual.profit_before_tax) }}</td></tr>
<tr><td>УСН</td><td>{{ money(actual.tax) }}</td></tr>
<tr><td>Чистая прибыль</td><td class="{{ 'profit-pos' if actual.profit >= 0 else 'profit-neg' }}">{{ money(actual.profit) }}</td></tr>
</tbody></table></div><div class="hint">Операций в истории продаж: {{ actual.operations }}. Товаров без себестоимости: {{ actual.missing_cost_count }}.</div></div>
</div>

<div class="section card"><div class="section-head"><div><h2>Цели по дням</h2><div class="subtitle">Сколько нужно делать в день, чтобы успеть к плану</div></div></div>
<div class="grid"><div class="card"><div class="label">Осталось дней</div><div class="value">{{ remaining_days }}</div></div><div class="card"><div class="label">Нужно выручки / день</div><div class="value">{{ money(need.revenue_per_day) }}</div></div><div class="card"><div class="label">Нужно прибыли / день</div><div class="value">{{ money(need.profit_per_day) }}</div></div><div class="card"><div class="label">Нужно продаж / день</div><div class="value">{{ number_input(need.units_per_day) }}</div></div><div class="card"><div class="label">Средняя прибыль / день</div><div class="value {{ 'good' if avg.profit_per_day >= 0 else 'bad' }}">{{ money(avg.profit_per_day) }}</div></div></div></div>

<footer>План-факт использует сохранённую PostgreSQL-историю. Для актуальной текущей недели сначала запусти /syncdaily.</footer>
</div></body></html>
"""



def _wb_image_url(nm_id: Any) -> str:
    try:
        nm = int(nm_id or 0)
    except (TypeError, ValueError):
        nm = 0
    if nm <= 0:
        return ""
    vol = nm // 100000
    part = nm // 1000
    # Approximate current WB basket routing. If it changes, image simply hides via onerror.
    basket = 1
    thresholds = [(143,1),(287,2),(431,3),(719,4),(1007,5),(1061,6),(1115,7),(1169,8),(1313,9),(1601,10),(1655,11),(1919,12),(2045,13),(2189,14),(2405,15),(2621,16),(2837,17),(3053,18),(3269,19)]
    for max_vol, b in thresholds:
        if vol <= max_vol:
            basket = b
            break
    else:
        basket = 20
    return f"https://basket-{basket:02d}.wbbasket.ru/vol{vol}/part{part}/{nm}/images/c246x328/1.webp"


def _wb_product_url(nm_id: Any) -> str:
    try:
        nm = int(nm_id or 0)
    except (TypeError, ValueError):
        nm = 0
    if nm <= 0:
        return ""
    return f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"


def _load_box_tariff_map(tariff_date: str, scheme: str = "fbs") -> tuple[list[str], dict[str, dict[str, float]], str]:
    """Load simple warehouse tariff map for interactive SKU matrix."""
    try:
        payload = _wb_get_json(TARIFF_BOX_URL, {"date": tariff_date})
        rows = _warehouse_rows(payload)
    except Exception as exc:
        return [], {}, f"Тарифы WB не загружены: {exc}"
    names: list[str] = []
    seen: set[str] = set()
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        name = str(row.get("warehouseName") or row.get("name") or "").strip()
        if not name:
            continue
        if name.casefold() not in seen:
            names.append(name)
            seen.add(name.casefold())
        if scheme == "fbs":
            base = _ru_float(row.get("boxDeliveryMarketplaceBase")) or _ru_float(row.get("boxDeliveryBase"))
            liter = _ru_float(row.get("boxDeliveryMarketplaceLiter")) or _ru_float(row.get("boxDeliveryLiter"))
        else:
            base = _ru_float(row.get("boxDeliveryBase"))
            liter = _ru_float(row.get("boxDeliveryLiter"))
        storage_base = _ru_float(row.get("boxStorageBase"))
        storage_liter = _ru_float(row.get("boxStorageLiter"))
        coef = _ru_float(row.get("boxDeliveryMarketplaceCoefExpr" if scheme == "fbs" else "boxDeliveryCoefExpr")) or _ru_float(row.get("boxDeliveryCoefExpr"))
        out[name] = {"base": float(base or 0.0), "liter": float(liter or 0.0), "coef": float(coef or 0.0), "storageBase": float(storage_base or 0.0), "storageLiter": float(storage_liter or 0.0)}
    names.sort(key=lambda x: x.casefold())
    return names, out, ""


def _unit_matrix_row_from_summary(row: Any, detail: dict[str, Any] | None = None) -> SimpleNamespace:
    units = abs(float(getattr(row, "units", 0.0) or 0.0))
    if units <= 0.000001:
        units = 1.0
    revenue = float(getattr(row, "revenue", 0.0) or 0.0)
    payout = float(getattr(row, "payout", 0.0) or 0.0)
    cogs = float(getattr(row, "cogs", 0.0) or 0.0)
    advertising = float(getattr(row, "advertising", 0.0) or 0.0)
    tax = float(getattr(row, "tax", 0.0) or 0.0)
    external = float(getattr(row, "external_expenses", 0.0) or 0.0)
    profit = float(getattr(row, "profit", 0.0) or 0.0)
    price = _div(revenue, units)
    cost = _div(abs(cogs), units)
    primary_wb = max(revenue - payout, 0.0)
    commission_pct = _div(primary_wb, revenue)
    logistics = _div(abs(float((detail or {}).get("logistics_total", 0.0) or 0.0)), abs(float((detail or {}).get("quantity", units) or units))) if detail else 0.0
    storage = _div(abs(float((detail or {}).get("storage", 0.0) or 0.0)), abs(float((detail or {}).get("quantity", units) or units))) if detail else 0.0
    other_wb = 0.0
    if detail:
        other_wb = abs(float((detail or {}).get("handling", 0.0) or 0.0)) + abs(float((detail or {}).get("fines", 0.0) or 0.0)) + abs(float((detail or {}).get("other", 0.0) or 0.0)) + abs(float((detail or {}).get("unallocated", 0.0) or 0.0))
        other_wb = _div(other_wb, abs(float((detail or {}).get("quantity", units) or units)))
    other = _div(abs(external), units) + other_wb
    drr = _div(abs(advertising), revenue)
    tax_pct = _div(abs(tax), revenue)
    base_profit = price - cost - price * commission_pct - logistics - storage - price * drr - price * tax_pct - other
    # If saved profit exists, use it as comparison base; scenario formula remains transparent.
    base_profit = _div(profit, units) if abs(profit) > 0.000001 else base_profit
    nm_id = getattr(row, "nm_id", None)
    sku = str(getattr(row, "sku", "") or "")
    name = str(getattr(row, "name", "") or sku or "Товар")
    data = {
        "sku": sku,
        "name": name,
        "nmId": int(nm_id) if nm_id else 0,
        "scheme": "fbs",
        "priceSpp": round(price, 2),
        "sppPct": 0.0,
        "price": round(price, 2),
        "cost": round(cost, 2),
        "commissionPct": round(commission_pct, 6),
        "warehouse": "",
        "lengthCm": 0.0,
        "widthCm": 0.0,
        "heightCm": 0.0,
        "logistics": round(logistics, 2),
        "storage": round(storage, 2),
        "drr": round(drr, 6),
        "taxPct": round(tax_pct, 6),
        "other": round(other, 2),
        "baseProfit": round(base_profit, 2),
        "extras": {"buyoutPct": 1.0, "returnLogistics": 0.0, "acquiringPct": 0.0, "vatPct": 0.0},
    }
    return SimpleNamespace(
        sku=sku,
        name=name,
        nm_id=nm_id,
        units=units,
        variant=sku,
        category="—",
        brand="—",
        photo_url=_wb_image_url(nm_id),
        product_url=_wb_product_url(nm_id),
        json=json.dumps(data, ensure_ascii=False).replace("'", "&#39;"),
    )


def _report_from_sales_summary(summary: Any, *, date_from: date | None, date_to: date | None) -> SimpleNamespace:
    """Build a report-like object from sale operation rows for the main dashboard."""
    return SimpleNamespace(
        report_id=0,
        period_type="actual",
        period_start=date_from or date(1900, 1, 1),
        period_end=date_to or date.today(),
        revenue=float(getattr(summary, "revenue", 0.0) or 0.0),
        payout=float(getattr(summary, "payout", 0.0) or 0.0),
        wb_expenses=float(getattr(summary, "wb_expenses", 0.0) or 0.0),
        cogs=float(getattr(summary, "cogs", 0.0) or 0.0),
        external_expenses=float(getattr(summary, "external_expenses", 0.0) or 0.0),
        advertising=float(getattr(summary, "advertising", 0.0) or 0.0),
        profit_before_tax=float(getattr(summary, "profit_before_tax", 0.0) or 0.0),
        tax=float(getattr(summary, "tax", 0.0) or 0.0),
        profit=float(getattr(summary, "profit", 0.0) or 0.0),
        margin=float(getattr(summary, "margin", 0.0) or 0.0),
        drr=float(getattr(summary, "drr", 0.0) or 0.0),
        units=float(getattr(summary, "quantity", 0.0) or 0.0),
        created_at=datetime.now(),
    )


def _sku_summary_from_sales(rows: list[Any]) -> list[SimpleNamespace]:
    by_sku: dict[str, dict[str, Any]] = {}
    for row in rows:
        sku = str(getattr(row, "sku", "") or "НЕРАСПРЕДЕЛЕНО")
        item = by_sku.setdefault(sku, {
            "sku": sku,
            "name": getattr(row, "name", "") or sku,
            "units": 0.0,
            "revenue": 0.0,
            "cogs": 0.0,
            "external_expenses": 0.0,
            "advertising": 0.0,
            "profit_before_tax": 0.0,
            "tax": 0.0,
            "profit": 0.0,
            "nm_id": 0,
        })
        if not item.get("name") or item.get("name") == sku:
            item["name"] = getattr(row, "name", "") or sku
        if not item.get("nm_id"):
            try:
                item["nm_id"] = int(getattr(row, "nm_id", 0) or 0)
            except (TypeError, ValueError):
                item["nm_id"] = 0
        item["units"] += float(getattr(row, "quantity", 0.0) or 0.0)
        item["revenue"] += float(getattr(row, "revenue", 0.0) or 0.0)
        item["cogs"] += float(getattr(row, "cogs", 0.0) or 0.0)
        item["external_expenses"] += float(getattr(row, "external_expenses", 0.0) or 0.0)
        item["advertising"] += float(getattr(row, "advertising", 0.0) or 0.0)
        item["profit_before_tax"] += float(getattr(row, "profit_before_tax", 0.0) or 0.0)
        item["tax"] += float(getattr(row, "tax", 0.0) or 0.0)
        item["profit"] += float(getattr(row, "profit", 0.0) or 0.0)
    out: list[SimpleNamespace] = []
    for item in by_sku.values():
        revenue = float(item["revenue"] or 0.0)
        item["margin"] = float(item["profit"] or 0.0) / revenue if abs(revenue) > 0.000001 else 0.0
        out.append(SimpleNamespace(**item))
    out.sort(key=lambda x: float(x.profit or 0.0), reverse=True)
    return out


def _chart_from_sale_rows(rows: list[Any]) -> tuple[list[str], list[float], list[float], list[float]]:
    grouped: dict[tuple[date, date], dict[str, float]] = {}
    for row in rows:
        start = getattr(row, "period_start", None) or getattr(row, "operation_date", None) or date.today()
        end = getattr(row, "period_end", None) or start
        key = (start, end)
        item = grouped.setdefault(key, {"revenue": 0.0, "profit": 0.0, "pre_tax": 0.0})
        item["revenue"] += float(getattr(row, "revenue", 0.0) or 0.0)
        item["profit"] += float(getattr(row, "profit", 0.0) or 0.0)
        item["pre_tax"] += float(getattr(row, "profit_before_tax", 0.0) or 0.0)
    items = sorted(grouped.items(), key=lambda kv: kv[0])
    return (
        [_period(start, end) for (start, end), _v in items],
        [round(v["profit"], 2) for _k, v in items],
        [round(v["pre_tax"], 2) for _k, v in items],
        [round(v["revenue"], 2) for _k, v in items],
    )

@app.get("/")
def dashboard():
    if not database_enabled():
        return Response("DATABASE_URL не задан. Сначала подключи PostgreSQL к сервису бота.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    selected_type = request.args.get("period_type", "actual").strip()
    if selected_type not in {"actual", "", "daily", "weekly", "xlsx"}:
        selected_type = "actual"
    expense_base = request.args.get("expense_base", "revenue").strip()
    if expense_base not in {"revenue", "expenses"}:
        expense_base = "revenue"
    date_from = _query_date("date_from")
    date_to = _query_date("date_to") or date.today()
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    reports_desc = list_dashboard_reports(200, period_type=None if selected_type == "actual" else (selected_type or None))
    latest = None
    skus: list[Any] = []
    expense_rows: list[Any] = []
    chart_labels: list[str] = []
    chart_profit: list[float] = []
    chart_pre_tax: list[float] = []
    chart_revenue: list[float] = []
    latest_period = ""
    latest_created = "ещё нет"

    if selected_type == "actual":
        actual_rows, summary, _cutoff = _actual_sales_rows(
            limit=20000,
            date_from=date_from,
            date_to=date_to,
            query="",
            operation_type="",
            only_negative=False,
            only_missing_cost=False,
        )
        latest = _report_from_sales_summary(summary, date_from=date_from, date_to=date_to)
        skus = _sku_summary_from_sales(actual_rows)
        expense_rows = actual_rows
        chart_labels, chart_profit, chart_pre_tax, chart_revenue = _chart_from_sale_rows(actual_rows)
        latest_period = (f"с первой продажи по {_date_display(date_to)}" if not date_from else _period(date_from, date_to))
        latest_created = "актуально без дублей"
    else:
        latest = reports_desc[0] if reports_desc else None
        selected_report_id = request.args.get("report_id", "").strip()
        if selected_report_id.isdigit():
            candidate = get_dashboard_report(int(selected_report_id))
            if candidate is not None and (not selected_type or candidate.period_type == selected_type):
                latest = candidate
        skus = list_dashboard_skus(latest.report_id) if latest else []
        if latest:
            try:
                expense_rows = list_sale_operations(
                    limit=5000,
                    period_type=latest.period_type,
                    date_from=latest.period_start,
                    date_to=latest.period_end,
                    query="",
                    operation_type="",
                )
            except Exception:
                logger.exception("Could not load operations for expense structure")
                expense_rows = []
        reports_asc = list(reversed(reports_desc))
        chart_labels = [_period(r.period_start, r.period_end) for r in reports_asc]
        chart_profit = [round(r.profit, 2) for r in reports_asc]
        chart_pre_tax = [round(r.profit_before_tax, 2) for r in reports_asc]
        chart_revenue = [round(r.revenue, 2) for r in reports_asc]
        latest_created = latest.created_at.strftime("%d.%m.%Y %H:%M") if latest else "ещё нет"
        latest_period = _period(latest.period_start, latest.period_end) if latest else ""

    expense_structure = _build_expense_structure(latest, expense_rows, base=expense_base)
    context: dict[str, Any] = {
        "title": os.getenv("DASHBOARD_TITLE", "WB Profit Dashboard").strip() or "WB Profit Dashboard",
        "latest": latest,
        "skus": skus,
        "reports_desc": reports_desc,
        "selected_type": selected_type,
        "date_from_value": date_from.isoformat() if date_from else "",
        "date_to_value": date_to.isoformat() if date_to else "",
        "expense_base": expense_base,
        "expense_structure": expense_structure,
        "chart_labels": chart_labels,
        "chart_profit": chart_profit,
        "chart_pre_tax": chart_pre_tax,
        "chart_revenue": chart_revenue,
        "latest_created": latest_created,
        "latest_period": latest_period,
        "money": _money,
        "percent": _percent,
        "units": _units,
        "period": _period,
        "type_label": _type_label,
        "margin_class": _margin_class,
    }
    return render_template_string(DASHBOARD_TEMPLATE, **context)




def _latest_weekly_end_for_actual() -> date | None:
    try:
        rows = list_dashboard_reports(1, period_type="weekly")
    except Exception:
        return None
    return rows[0].period_end if rows else None


def _summarize_sale_rows(rows: list[Any]):
    operations = len(rows)
    quantity = sum(float(getattr(row, "quantity", 0.0) or 0.0) for row in rows)
    revenue = sum(float(getattr(row, "revenue", 0.0) or 0.0) for row in rows)
    payout = sum(float(getattr(row, "payout", 0.0) or 0.0) for row in rows)
    wb_expenses = sum(float(getattr(row, "wb_expenses", 0.0) or 0.0) for row in rows)
    advertising = sum(float(getattr(row, "advertising", 0.0) or 0.0) for row in rows)
    cogs = sum(float(getattr(row, "cogs", 0.0) or 0.0) for row in rows)
    external_expenses = sum(float(getattr(row, "external_expenses", 0.0) or 0.0) for row in rows)
    profit_before_tax = sum(float(getattr(row, "profit_before_tax", 0.0) or 0.0) for row in rows)
    tax = sum(float(getattr(row, "tax", 0.0) or 0.0) for row in rows)
    profit = sum(float(getattr(row, "profit", 0.0) or 0.0) for row in rows)
    missing_cost_count = sum(1 for row in rows if getattr(row, "missing_cost", False))
    return SimpleNamespace(
        operations=operations,
        quantity=quantity,
        revenue=revenue,
        payout=payout,
        wb_expenses=wb_expenses,
        advertising=advertising,
        cogs=cogs,
        external_expenses=external_expenses,
        profit_before_tax=profit_before_tax,
        tax=tax,
        profit=profit,
        margin=profit / revenue if abs(revenue) > 0.000001 else 0.0,
        drr=advertising / revenue if abs(revenue) > 0.000001 else 0.0,
        missing_cost_count=missing_cost_count,
    )


def _operation_sort_key(row: Any):
    return (
        getattr(row, "operation_date", None) or getattr(row, "period_start", None) or date.min,
        getattr(row, "period_end", None) or date.min,
        str(getattr(row, "operation_id", "")),
    )


def _actual_sales_rows(
    *,
    limit: int,
    date_from: date | None,
    date_to: date | None,
    query: str,
    operation_type: str,
    only_negative: bool,
    only_missing_cost: bool,
) -> tuple[list[Any], Any, date | None]:
    """Берёт закрытые weekly + daily только после последней закрытой недели, чтобы не было дублей."""
    latest_weekly_end = _latest_weekly_end_for_actual()
    combined: list[Any] = []

    weekly_to = date_to
    if latest_weekly_end is not None:
        weekly_to = min(date_to, latest_weekly_end) if date_to else latest_weekly_end
    if latest_weekly_end is not None and (date_from is None or date_from <= latest_weekly_end):
        combined.extend(
            list_sale_operations(
                5000,
                period_type="weekly",
                date_from=date_from,
                date_to=weekly_to,
                query=query,
                operation_type=operation_type,
                only_negative=only_negative,
                only_missing_cost=only_missing_cost,
            )
        )

    daily_from = date_from
    if latest_weekly_end is not None:
        cutoff = latest_weekly_end + timedelta(days=1)
        daily_from = max(date_from, cutoff) if date_from else cutoff
    if date_to is None or daily_from is None or daily_from <= date_to:
        combined.extend(
            list_sale_operations(
                5000,
                period_type="daily",
                date_from=daily_from,
                date_to=date_to,
                query=query,
                operation_type=operation_type,
                only_negative=only_negative,
                only_missing_cost=only_missing_cost,
            )
        )

    combined.sort(key=_operation_sort_key, reverse=True)
    summary = _summarize_sale_rows(combined)
    return combined[:limit], summary, latest_weekly_end


@app.get("/sales")
def sales_history():
    if not database_enabled():
        return Response("DATABASE_URL не задан.", 503, {"Content-Type": "text/plain; charset=utf-8"})

    selected_type = request.args.get("period_type", "actual").strip()
    if selected_type not in {"actual", "", "daily", "weekly"}:
        selected_type = "actual"
    date_from = _query_date("date_from")
    date_to = _query_date("date_to")
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    query = request.args.get("q", "").strip()[:120]
    operation_type = request.args.get("operation_type", "").strip()
    if operation_type not in {"", "Продажа", "Возврат", "Расход WB"}:
        operation_type = ""
    only_negative = _query_bool("negative")
    only_missing_cost = _query_bool("missing_cost")
    limit = _query_int("limit", 500, minimum=50, maximum=5000)

    actual_cutoff = None
    if selected_type == "actual":
        rows, summary, actual_cutoff = _actual_sales_rows(
            limit=limit,
            date_from=date_from,
            date_to=date_to,
            query=query,
            operation_type=operation_type,
            only_negative=only_negative,
            only_missing_cost=only_missing_cost,
        )
    else:
        rows = list_sale_operations(
            limit,
            period_type=selected_type or None,
            date_from=date_from,
            date_to=date_to,
            query=query,
            operation_type=operation_type,
            only_negative=only_negative,
            only_missing_cost=only_missing_cost,
        )
        summary = sale_operations_summary(
            period_type=selected_type or None,
            date_from=date_from,
            date_to=date_to,
            query=query,
            operation_type=operation_type,
            only_negative=only_negative,
            only_missing_cost=only_missing_cost,
        )
    args = request.args.to_dict(flat=True)
    args["export"] = "csv"
    export_url = url_for("sales_export") + "?" + urllib.parse.urlencode(args)
    return render_template_string(
        SALES_TEMPLATE,
        rows=rows,
        summary=summary,
        selected_type=selected_type,
        actual_cutoff=actual_cutoff,
        date_from_value=date_from.isoformat() if date_from else "",
        date_to_value=date_to.isoformat() if date_to else "",
        query=query,
        operation_type=operation_type,
        only_negative=only_negative,
        only_missing_cost=only_missing_cost,
        limit=limit,
        export_url=export_url,
        money=_money,
        percent=_percent,
        units=_units,
        period=_period,
        date_display=_date_display,
        margin_class=_margin_class,
    )


@app.get("/sales/export")
def sales_export():
    if not database_enabled():
        return Response("DATABASE_URL не задан.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    selected_type = request.args.get("period_type", "actual").strip()
    if selected_type not in {"actual", "", "daily", "weekly"}:
        selected_type = "actual"
    date_from = _query_date("date_from")
    date_to = _query_date("date_to")
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    query = request.args.get("q", "").strip()[:120]
    operation_type = request.args.get("operation_type", "").strip()
    if operation_type not in {"", "Продажа", "Возврат", "Расход WB"}:
        operation_type = ""
    if selected_type == "actual":
        rows, _, _ = _actual_sales_rows(
            limit=5000,
            date_from=date_from,
            date_to=date_to,
            query=query,
            operation_type=operation_type,
            only_negative=_query_bool("negative"),
            only_missing_cost=_query_bool("missing_cost"),
        )
    else:
        rows = list_sale_operations(
            5000,
            period_type=selected_type or None,
            date_from=date_from,
            date_to=date_to,
            query=query,
            operation_type=operation_type,
            only_negative=_query_bool("negative"),
            only_missing_cost=_query_bool("missing_cost"),
        )
    header = [
        "date", "operation", "sku", "nm_id", "name", "quantity", "revenue", "payout",
        "wb_expenses", "logistics", "handling", "storage", "other_wb", "cogs",
        "external_expenses", "advertising", "profit_before_tax", "tax", "profit", "margin", "report_period",
    ]
    lines = [";".join(header)]
    def cell(value: Any) -> str:
        text = "" if value is None else str(value).replace(";", ",").replace("\n", " ")
        return '"' + text.replace('"', '""') + '"'
    for row in rows:
        lines.append(";".join(cell(value) for value in [
            row.operation_date or row.period_start,
            row.operation_type,
            row.sku,
            row.nm_id or "",
            row.name,
            row.quantity,
            row.revenue,
            row.payout,
            row.wb_expenses,
            row.logistics + row.transport,
            row.handling,
            row.storage,
            row.other_withholdings + row.fines,
            row.cogs,
            row.external_expenses,
            row.advertising,
            row.profit_before_tax,
            row.tax,
            row.profit,
            row.margin,
            _period(row.period_start, row.period_end),
        ]))
    csv_data = "\ufeff" + "\n".join(lines)
    return Response(
        csv_data,
        200,
        {
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": 'attachment; filename="wb_sales_history.csv"',
        },
    )


@app.get("/reconcile")
def reconcile_dashboard():
    if not database_enabled():
        return Response("DATABASE_URL не задан.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    date_from = _query_date("date_from")
    date_to = _query_date("date_to")
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    actual_rows, actual_summary, actual_cutoff = _actual_sales_rows(
        limit=5000,
        date_from=date_from,
        date_to=date_to,
        query="",
        operation_type="",
        only_negative=False,
        only_missing_cost=False,
    )
    actual_sale_rows, actual_sales, _ = _actual_sales_rows(
        limit=5000,
        date_from=date_from,
        date_to=date_to,
        query="",
        operation_type="Продажа",
        only_negative=False,
        only_missing_cost=False,
    )
    weekly_sales = sale_operations_summary(period_type="weekly", date_from=date_from, date_to=date_to, operation_type="Продажа")
    weekly_summary = sale_operations_summary(period_type="weekly", date_from=date_from, date_to=date_to)
    daily_sales = sale_operations_summary(period_type="daily", date_from=date_from, date_to=date_to, operation_type="Продажа")
    daily_summary = sale_operations_summary(period_type="daily", date_from=date_from, date_to=date_to)
    returns = sale_operations_summary(period_type=None, date_from=date_from, date_to=date_to, operation_type="Возврат")
    wb_cost_rows = sale_operations_summary(period_type=None, date_from=date_from, date_to=date_to, operation_type="Расход WB")

    operative_tail = SimpleNamespace(quantity=0.0, revenue=0.0, payout=0.0)
    if actual_cutoff is not None:
        tail = sale_operations_summary(period_type="daily", date_from=actual_cutoff + timedelta(days=1), date_to=date_to, operation_type="Продажа")
        operative_tail = tail

    unallocated_rows = []
    for row in actual_rows:
        sku = str(getattr(row, "sku", "") or "").casefold()
        name = str(getattr(row, "name", "") or "").casefold()
        nm_id = getattr(row, "nm_id", None)
        if "нераспредел" in sku or "нераспредел" in name or not nm_id:
            unallocated_rows.append(row)
    unallocated = _summarize_sale_rows(unallocated_rows)

    start_balance_raw = request.args.get("start_balance", "").strip()
    end_balance_raw = request.args.get("end_balance", "").strip()
    start_balance = _query_float("start_balance", 0.0) if start_balance_raw else None
    end_balance = _query_float("end_balance", 0.0) if end_balance_raw else None
    balance_ready = start_balance is not None and end_balance is not None
    balance_change = (float(end_balance) - float(start_balance)) if balance_ready else 0.0
    balance_minus_payout = balance_change - float(actual_summary.payout or 0.0)

    checks = []
    def add_check(name: str, ok: bool, warning: bool, note: str):
        if ok:
            status, cls = "Ок", "profit-pos"
        elif warning:
            status, cls = "Проверить", "value warn"
        else:
            status, cls = "Проблема", "profit-neg"
        checks.append({"name": name, "status": status, "cls": cls, "note": note})

    add_check(
        "Текущая неделя догружена ежедневными отчётами",
        abs(float(operative_tail.quantity or 0.0)) > 0 or actual_cutoff is None,
        True,
        "Если в WB есть свежие выкупы, а тут 0 — запусти /syncdaily.",
    )
    add_check(
        "Есть расходы WB без артикула",
        abs(float(unallocated.wb_expenses or 0.0)) < 0.01,
        True,
        "Нераспределённые расходы не всегда можно разнести по SKU автоматически.",
    )
    add_check(
        "Все продажи имеют себестоимость",
        int(actual_summary.missing_cost_count or 0) == 0,
        False,
        "Если есть пропуски, прибыль завышена. Добавь себестоимость товара.",
    )
    add_check(
        "Возвраты учтены",
        True,
        False,
        f"Возвраты за период: {abs(float(returns.quantity or 0.0)):.0f} шт. Они уменьшают итоговую прибыль.",
    )
    if balance_ready:
        add_check(
            "Баланс WB сравнен с выплатами",
            abs(balance_minus_payout) < max(1000.0, abs(float(actual_summary.payout or 0.0)) * 0.15),
            True,
            "Большая разница не всегда ошибка: баланс включает старый минус, удержания, рекламу, корректировки и переносы.",
        )

    period_label = "Период: " + ((date_from.strftime("%d.%m.%Y") if date_from else "начало") + " — " + (date_to.strftime("%d.%m.%Y") if date_to else "сегодня"))
    return render_template_string(
        RECONCILE_TEMPLATE,
        period_label=period_label,
        date_from_value=date_from.isoformat() if date_from else "",
        date_to_value=date_to.isoformat() if date_to else "",
        start_balance_input=start_balance_raw,
        end_balance_input=end_balance_raw,
        start_balance=start_balance or 0.0,
        end_balance=end_balance or 0.0,
        balance_ready=balance_ready,
        balance_change=balance_change,
        balance_minus_payout=balance_minus_payout,
        actual_summary=actual_summary,
        actual_sales=actual_sales,
        weekly_summary=weekly_summary,
        weekly_sales=weekly_sales,
        daily_summary=daily_summary,
        daily_sales=daily_sales,
        operative_tail=operative_tail,
        returns=returns,
        wb_cost_rows=wb_cost_rows,
        unallocated=unallocated,
        actual_cutoff=actual_cutoff,
        checks=checks,
        money=_money,
        percent=_percent,
        units=_units,
        date_display=_date_display,
        abs=abs,
    )





def _group_sales_by_sku(rows: list[Any]) -> list[SimpleNamespace]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        sku = str(getattr(row, "sku", "") or "НЕРAСПРЕДЕЛЕНО")
        item = grouped.setdefault(sku, {
            "sku": sku,
            "name": getattr(row, "name", "") or sku,
            "units": 0.0,
            "revenue": 0.0,
            "payout": 0.0,
            "wb_expenses": 0.0,
            "advertising": 0.0,
            "cogs": 0.0,
            "external_expenses": 0.0,
            "tax": 0.0,
            "profit": 0.0,
            "missing_cost": 0,
        })
        if getattr(row, "name", None):
            item["name"] = getattr(row, "name")
        item["units"] += float(getattr(row, "quantity", 0.0) or 0.0)
        item["revenue"] += float(getattr(row, "revenue", 0.0) or 0.0)
        item["payout"] += float(getattr(row, "payout", 0.0) or 0.0)
        item["wb_expenses"] += float(getattr(row, "wb_expenses", 0.0) or 0.0)
        item["advertising"] += float(getattr(row, "advertising", 0.0) or 0.0)
        item["cogs"] += float(getattr(row, "cogs", 0.0) or 0.0)
        item["external_expenses"] += float(getattr(row, "external_expenses", 0.0) or 0.0)
        item["tax"] += float(getattr(row, "tax", 0.0) or 0.0)
        item["profit"] += float(getattr(row, "profit", 0.0) or 0.0)
        if getattr(row, "missing_cost", False):
            item["missing_cost"] += 1
    result = []
    for item in grouped.values():
        revenue = float(item["revenue"] or 0.0)
        item["margin"] = _div(float(item["profit"] or 0.0), revenue)
        item["drr"] = _div(float(item["advertising"] or 0.0), revenue)
        result.append(SimpleNamespace(**item))
    result.sort(key=lambda x: float(getattr(x, "profit", 0.0) or 0.0), reverse=True)
    return result


def _delta_text(current: float, previous: float, money_kind: bool = False) -> str:
    current = float(current or 0.0)
    previous = float(previous or 0.0)
    diff = current - previous
    if abs(previous) < 0.000001:
        return "предыдущий период: нет базы"
    pct = diff / abs(previous)
    sign = "+" if diff >= 0 else "−"
    amount = _money(abs(diff)) if money_kind else _number_input(abs(diff))
    return f"{sign}{amount} ({sign}{abs(pct)*100:.1f}%) к предыдущему"


@app.get("/ai-analyst")
def ai_analyst():
    if not database_enabled():
        return Response("DATABASE_URL не задан.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    today = _safe_today()
    default_to = today - timedelta(days=1)
    default_from = default_to - timedelta(days=29)
    date_from = _query_date("date_from") or default_from
    date_to = _query_date("date_to") or default_to
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    min_margin = max(0.0, _query_percent("min_margin", 0.10))
    max_drr = max(0.0, _query_percent("max_drr", 0.20))

    rows, current, cutoff = _actual_sales_rows(
        limit=5000,
        date_from=date_from,
        date_to=date_to,
        query="",
        operation_type="",
        only_negative=False,
        only_missing_cost=False,
    )
    days = max(1, (date_to - date_from).days + 1)
    prev_to = date_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=days - 1)
    prev_rows, previous, _ = _actual_sales_rows(
        limit=5000,
        date_from=prev_from,
        date_to=prev_to,
        query="",
        operation_type="",
        only_negative=False,
        only_missing_cost=False,
    )
    current.margin = _div(float(current.profit or 0.0), float(current.revenue or 0.0))
    current.drr = _div(float(current.advertising or 0.0), float(current.revenue or 0.0))
    previous.margin = _div(float(previous.profit or 0.0), float(previous.revenue or 0.0))
    previous.drr = _div(float(previous.advertising or 0.0), float(previous.revenue or 0.0))

    sku_rows = _group_sales_by_sku(rows)
    problem_skus = []
    for row in sku_rows:
        signals = []
        if float(row.profit or 0.0) < 0:
            signals.append("минус")
        if float(row.margin or 0.0) < min_margin and float(row.revenue or 0.0) > 0:
            signals.append("низкая маржа")
        if float(row.drr or 0.0) > max_drr and float(row.revenue or 0.0) > 0:
            signals.append("высокий ДРР")
        if int(row.missing_cost or 0) > 0:
            signals.append("нет себеса")
        if signals:
            row.signal = ", ".join(signals)
            problem_skus.append(row)
    problem_skus.sort(key=lambda x: (float(getattr(x, "profit", 0.0) or 0.0), float(getattr(x, "margin", 0.0) or 0.0)))
    top_skus = [r for r in sku_rows if float(getattr(r, "profit", 0.0) or 0.0) > 0][:8]

    insights = []
    def add(kind: str, title: str, text: str):
        insights.append({"kind": kind, "title": title, "text": text})
    if float(current.revenue or 0.0) <= 0:
        add("error", "Нет продаж за выбранный период", "Проверь дату или догрузи данные через /syncdaily и /backfill.")
    if float(current.profit or 0.0) < 0:
        add("error", "Период в минусе", f"Чистая прибыль { _money(current.profit) }. Проверь товары в минус, рекламу и расходы WB.")
    elif float(current.margin or 0.0) < min_margin and float(current.revenue or 0.0) > 0:
        add("error", "Маржа ниже целевой", f"Фактическая маржа { _percent(current.margin) }, целевой минимум { _percent(min_margin) }.")
    else:
        add("ok", "Маржа выглядит нормально", f"Маржа за период { _percent(current.margin) }, чистая прибыль { _money(current.profit) }.")
    if float(current.drr or 0.0) > max_drr and float(current.revenue or 0.0) > 0:
        add("error", "ДРР выше лимита", f"ДРР { _percent(current.drr) } при лимите { _percent(max_drr) }. Проверь кампании и товары с рекламой.")
    elif float(current.advertising or 0.0) > 0:
        add("ok", "Реклама в пределах лимита", f"ДРР { _percent(current.drr) }, расход на рекламу { _money(current.advertising) }.")
    if int(current.missing_cost_count or 0) > 0:
        add("error", "Есть продажи без себестоимости", f"Найдено {int(current.missing_cost_count)} операций без себеса. Прибыль может быть завышена.")
    if float(previous.profit or 0.0) and float(current.profit or 0.0) < float(previous.profit or 0.0) * 0.85:
        add("error", "Прибыль снизилась", _delta_text(current.profit, previous.profit, True))
    if float(previous.revenue or 0.0) and float(current.revenue or 0.0) < float(previous.revenue or 0.0) * 0.80:
        add("error", "Выручка заметно просела", _delta_text(current.revenue, previous.revenue, True))
    if problem_skus:
        add("error", "Есть проблемные SKU", f"Найдено {len(problem_skus)} товаров/артикулов с минусом, низкой маржей, высоким ДРР или отсутствующей себестоимостью.")

    actions = []
    if problem_skus:
        first = problem_skus[0]
        actions.append({"title": "Разобрать худший SKU", "text": f"Начни с {first.name} ({first.sku}): прибыль { _money(first.profit) }, маржа { _percent(first.margin) }, ДРР { _percent(first.drr) }."})
    if int(current.missing_cost_count or 0) > 0:
        actions.append({"title": "Закрыть пропуски себестоимости", "text": "Открой 'Себестоимость и расходы' и добавь себес по товарам без себестоимости, затем запусти /backfill."})
    if float(current.drr or 0.0) > max_drr:
        actions.append({"title": "Проверить рекламу", "text": "Отфильтруй товары с высоким ДРР и сравни прибыль до рекламы и после рекламы."})
    if float(current.wb_expenses or 0.0) > float(current.revenue or 0.0) * 0.25 and float(current.revenue or 0.0) > 0:
        actions.append({"title": "Проверить расходы WB", "text": "Доля удержаний WB выше 25% от дохода. Открой раздел 'Логистика' и посмотри детализацию."})
    actions.append({"title": "Обновить свежую неделю", "text": "Перед анализом текущего месяца запускай /syncdaily, чтобы в дашборд попали оперативные ежедневные отчёты."})

    reports = []
    try:
        reports = list_dashboard_reports(80, period_type=None)
    except Exception:
        reports = []
    reports = [r for r in reports if (not date_from or r.period_end >= date_from) and (not date_to or r.period_start <= date_to)]
    reports.sort(key=lambda r: (r.period_start, r.period_end))

    return render_template_string(
        AI_ANALYST_TEMPLATE,
        date_from=date_from,
        date_to=date_to,
        min_margin=min_margin,
        max_drr=max_drr,
        current=current,
        previous=previous,
        insights=insights,
        actions=actions,
        problem_skus=problem_skus[:25],
        top_skus=top_skus,
        chart_labels=[_period(r.period_start, r.period_end) for r in reports],
        chart_profit=[round(float(r.profit or 0), 2) for r in reports],
        chart_revenue=[round(float(r.revenue or 0), 2) for r in reports],
        money=_money,
        percent=_percent,
        units=_units,
        percent_input=_input_percent,
        margin_class=_margin_class,
        date_display=_date_display,
        delta_text=_delta_text,
    )




FUNNEL_ADS_TEMPLATE = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Воронка и реклама · WB Profit</title><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>""" + BASE_STYLE + r"""</head><body><div class="wrap">
<header><div><h1>Воронка и реклама</h1><div class="subtitle">Клики, корзины, заказы, выкупы и рекламная статистика WB по товарам</div></div><div class="badge">LIVE из WB API + факт из PostgreSQL</div></header>
<nav class="nav"><a href="/">Дашборд</a><a href="/products">Товары</a><a href="/sales">История продаж</a><a href="/reconcile">Сверка</a><a href="/unit-economics">Юнит-экономика</a><a href="/unit-matrix">SKU-юнитка</a><a href="/unit-calculator">Калькулятор юнитки</a><a href="/supply-planner">Поставки</a><a href="/logistics">Логистика</a><a href="/ai-analyst">AI-аналитик</a><a class="active" href="/funnel-ads">Воронка/реклама</a><a href="/ai-chat">AI-чат</a><a href="/plan-fact">План-факт</a><a href="/admin">Себестоимость и расходы</a></nav>
<form class="filters" method="get"><label>С даты <input type="date" name="date_from" value="{{ date_from_value }}"></label><label>По дату <input type="date" name="date_to" value="{{ date_to_value }}"></label><label>Поиск <input name="q" placeholder="товар / артикул / nmID" value="{{ query }}"></label><label>Мин. падение, % <input inputmode="decimal" name="drop" value="{{ drop_input }}"></label><button type="submit">Обновить</button></form>
<div class="hint">Воронка подтягивается из WB Analytics: переходы в карточку, корзины, заказы, выкупы и конверсии. Реклама подтягивается из WB Продвижение: показы, клики, CTR, CPC, корзины, заказы, расход. Данные оперативные и могут отличаться от финального финансового отчёта.</div>
{% for warning in warnings %}<div class="notice error">{{ warning }}</div>{% endfor %}
<div class="grid section"><div class="card"><div class="label">Товаров в воронке</div><div class="value">{{ totals.products }}</div></div><div class="card"><div class="label">Переходы в карточку</div><div class="value">{{ units(totals.open_count) }}</div></div><div class="card"><div class="label">Корзины</div><div class="value">{{ units(totals.cart_count) }}</div><div class="subtitle">CR в корзину: {{ percent(totals.cart_cr) }}</div></div><div class="card"><div class="label">Заказы / выкупы</div><div class="value">{{ units(totals.order_count) }} / {{ units(totals.buyout_count) }}</div><div class="subtitle">Выкуп: {{ percent(totals.buyout_rate) }}</div></div><div class="card"><div class="label">Реклама WB</div><div class="value">{{ money(totals.ad_sum) }}</div><div class="subtitle">Клики: {{ units(totals.ad_clicks) }} · CTR: {{ percent(totals.ad_ctr) }}</div></div></div>
<div class="two-col section"><div class="card"><div class="section-head"><div><h2>Где просадка</h2><div class="subtitle">Сравнение выбранного периода с предыдущим таким же периодом</div></div><span class="muted">Порог: {{ percent(drop_threshold) }}</span></div>{% if insights %}<div class="table-wrap"><table><thead><tr><th>Что проверить</th><th>Товар</th><th>Артикул</th><th>Причина</th></tr></thead><tbody>{% for i in insights %}<tr><td class="{{ 'profit-neg' if i.level == 'bad' else 'value warn' }}">{{ i.title }}</td><td>{{ i.name }}</td><td class="muted">{{ i.sku }}</td><td style="text-align:left;white-space:normal">{{ i.reason }}</td></tr>{% endfor %}</tbody></table></div>{% else %}<div class="empty">Критичных просадок по выбранному порогу не найдено.</div>{% endif %}</div>
<div class="card"><h2>Как читать</h2><div class="hint">Если упали переходы — проверь позицию, ставку, наличие товара, цену и видимость карточки. Если переходы есть, но корзины упали — проблема чаще в цене, первом экране, инфографике, отзывах или оффере. Если корзины есть, но заказы/выкупы падают — проверь цену, срок доставки, остатки, рейтинг и конкурентов.</div><div class="hint">Рекламные расходы из WB Продвижение не всегда равны финальной рекламе из финансового отчёта, потому что это оперативная статистика кампаний.</div></div></div>
<div class="section card"><div class="section-head"><div><h2>Товары: воронка + реклама + прибыль</h2><div class="subtitle">Сортировка: сначала проблемные товары и большие просадки</div></div><span class="muted">{{ period_label }}</span></div><div class="table-wrap"><table style="min-width:2100px"><thead><tr><th>Товар</th><th>Артикул</th><th>nmID</th><th>Переходы</th><th>Δ перех.</th><th>Корзины</th><th>Δ корз.</th><th>Заказы</th><th>Выкупы</th><th>CR корзина</th><th>CR заказ</th><th>Выкуп %</th><th>Рекл. показы</th><th>Клики</th><th>CTR</th><th>CPC</th><th>Рекл. корзины</th><th>Рекл. заказы</th><th>Расход</th><th>ДРР факт</th><th>Факт доход</th><th>Факт прибыль</th><th>Статус</th></tr></thead><tbody>{% for row in rows %}<tr><td>{{ row.name }}</td><td class="muted">{{ row.sku }}</td><td class="muted">{{ row.nm_id }}</td><td>{{ units(row.open_count) }}</td><td class="{{ 'profit-neg' if row.open_dyn < -drop_threshold else 'profit-pos' if row.open_dyn > 0 else '' }}">{{ percent(row.open_dyn) }}</td><td>{{ units(row.cart_count) }}</td><td class="{{ 'profit-neg' if row.cart_dyn < -drop_threshold else 'profit-pos' if row.cart_dyn > 0 else '' }}">{{ percent(row.cart_dyn) }}</td><td>{{ units(row.order_count) }}</td><td>{{ units(row.buyout_count) }}</td><td>{{ percent(row.add_to_cart_cr) }}</td><td>{{ percent(row.cart_to_order_cr) }}</td><td>{{ percent(row.buyout_rate) }}</td><td>{{ units(row.ad_views) }}</td><td>{{ units(row.ad_clicks) }}</td><td>{{ percent(row.ad_ctr) }}</td><td>{{ money(row.ad_cpc) }}</td><td>{{ units(row.ad_atbs) }}</td><td>{{ units(row.ad_orders) }}</td><td>{{ money(row.ad_sum) }}</td><td>{{ percent(row.fact_drr) }}</td><td>{{ money(row.fact_revenue) }}</td><td class="{{ 'profit-pos' if row.fact_profit >= 0 else 'profit-neg' }}">{{ money(row.fact_profit) }}</td><td class="{{ 'rank-bad' if row.status_level == 'bad' else 'value warn' if row.status_level == 'warn' else 'rank-good' }}">{{ row.status }}</td></tr>{% else %}<tr><td colspan="23" class="empty">Нет данных. Проверь токен WB с категориями “Аналитика” и “Продвижение” или расширь период.</td></tr>{% endfor %}</tbody></table></div></div>
<footer>WB Profit Bot · Воронка и реклама</footer></div></body></html>
"""


def _wb_token() -> str:
    return os.getenv("WB_API_TOKEN", "").strip()


def _wb_request_json(method: str, url: str, *, params: dict[str, Any] | None = None, payload: Any = None, timeout: int = 80) -> Any:
    token = _wb_token()
    if not token:
        raise RuntimeError("WB_API_TOKEN не задан в Railway Variables.")
    final_url = url
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        final_url = f"{url}?{query}"
    body = None
    headers = {"Authorization": token, "Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(final_url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return None
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:700]
        raise RuntimeError(f"WB API {exc.code}: {detail}") from exc


def _same_len_previous_period(date_from: date, date_to: date) -> tuple[date, date]:
    days = (date_to - date_from).days + 1
    prev_to = date_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=days - 1)
    return prev_from, prev_to


def _period_payload(date_from: date, date_to: date) -> dict[str, str]:
    return {"start": date_from.isoformat(), "end": date_to.isoformat()}


def _load_sales_funnel(date_from: date, date_to: date, *, limit_pages: int = 5) -> tuple[list[dict[str, Any]], list[str]]:
    prev_from, prev_to = _same_len_previous_period(date_from, date_to)
    warnings: list[str] = []
    products: list[dict[str, Any]] = []
    offset = 0
    limit = 1000
    for _ in range(limit_pages):
        payload = {
            "selectedPeriod": _period_payload(date_from, date_to),
            "pastPeriod": _period_payload(prev_from, prev_to),
            "nmIds": [],
            "brandNames": [],
            "subjectIds": [],
            "tagIds": [],
            "skipDeletedNm": False,
            "orderBy": {"field": "openCard", "mode": "desc"},
            "limit": limit,
            "offset": offset,
        }
        data = _wb_request_json("POST", "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products", payload=payload)
        batch = (((data or {}).get("data") or {}).get("products") or []) if isinstance(data, dict) else []
        products.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    if len(products) >= limit * limit_pages:
        warnings.append("Воронка загружена частично: достигнут внутренний лимит страниц. Уточни период или поиск.")
    return products, warnings


def _extract_campaign_ids(payload: Any) -> list[int]:
    result: list[int] = []
    if not isinstance(payload, dict):
        return result
    for group in payload.get("adverts", []) or []:
        status = int(group.get("status") or 0)
        if status not in {7, 9, 11}:
            continue
        for item in group.get("advert_list", []) or []:
            try:
                result.append(int(item.get("advertId")))
            except Exception:
                pass
    return sorted(set(result))


def _flatten_ad_stats(payload: Any) -> dict[int, dict[str, Any]]:
    by_nm: dict[int, dict[str, Any]] = {}
    if not isinstance(payload, list):
        return by_nm

    def add_nm(nm_id: int, name: str, views=0, clicks=0, atbs=0, orders=0, shks=0, spend=0, sum_price=0):
        if not nm_id:
            return
        row = by_nm.setdefault(int(nm_id), {"nm_id": int(nm_id), "name": name or "", "views": 0.0, "clicks": 0.0, "atbs": 0.0, "orders": 0.0, "shks": 0.0, "sum": 0.0, "sum_price": 0.0})
        if name and not row.get("name"):
            row["name"] = name
        row["views"] += float(views or 0)
        row["clicks"] += float(clicks or 0)
        row["atbs"] += float(atbs or 0)
        row["orders"] += float(orders or 0)
        row["shks"] += float(shks or 0)
        row["sum"] += float(spend or 0)
        row["sum_price"] += float(sum_price or 0)

    for camp in payload:
        for day in camp.get("days", []) or []:
            for app_item in day.get("apps", []) or []:
                for nm in app_item.get("nms") or []:
                    add_nm(
                        int(nm.get("nmId") or nm.get("nm") or 0),
                        str(nm.get("name") or ""),
                        nm.get("views"), nm.get("clicks"), nm.get("atbs"), nm.get("orders"), nm.get("shks"), nm.get("sum"), nm.get("sum_price"),
                    )
    return by_nm


def _load_ad_stats(date_from: date, date_to: date) -> tuple[dict[int, dict[str, Any]], list[str]]:
    warnings: list[str] = []
    max_days = 31
    if (date_to - date_from).days + 1 > max_days:
        warnings.append("Рекламная статистика WB запрошена только за первые 31 день периода: ограничение метода /adv/v3/fullstats.")
        date_to = date_from + timedelta(days=max_days - 1)
    try:
        count = _wb_request_json("GET", "https://advert-api.wildberries.ru/adv/v1/promotion/count")
        ids = _extract_campaign_ids(count)
    except Exception as exc:
        return {}, [f"Не удалось получить список рекламных кампаний WB: {exc}"]
    if not ids:
        return {}, []
    if len(ids) > 50:
        warnings.append(f"Найдено {len(ids)} рекламных кампаний, загружаем первые 50: лимит метода fullstats.")
        ids = ids[:50]
    try:
        stats = _wb_request_json(
            "GET",
            "https://advert-api.wildberries.ru/adv/v3/fullstats",
            params={"ids": ",".join(str(x) for x in ids), "beginDate": date_from.isoformat(), "endDate": date_to.isoformat()},
            timeout=100,
        )
        return _flatten_ad_stats(stats), warnings
    except Exception as exc:
        warnings.append(f"Не удалось получить рекламную статистику WB: {exc}")
        return {}, warnings


def _funnel_product_row(item: dict[str, Any], ad_row: dict[str, Any] | None, actual_by_nm: dict[int, dict[str, float]], *, drop_threshold: float) -> SimpleNamespace:
    product = item.get("product") or {}
    stat = item.get("statistic") or {}
    selected = stat.get("selected") or {}
    comparison = stat.get("comparison") or {}
    conv = selected.get("conversions") or {}
    nm_id = int(product.get("nmId") or 0)
    actual = actual_by_nm.get(nm_id, {})
    ad = ad_row or {}
    open_count = float(selected.get("openCount") or 0)
    cart_count = float(selected.get("cartCount") or 0)
    order_count = float(selected.get("orderCount") or 0)
    buyout_count = float(selected.get("buyoutCount") or 0)
    ad_views = float(ad.get("views") or 0)
    ad_clicks = float(ad.get("clicks") or 0)
    ad_sum = float(ad.get("sum") or 0)
    open_dyn = float(comparison.get("openCountDynamic") or 0) / 100.0
    cart_dyn = float(comparison.get("cartCountDynamic") or 0) / 100.0
    order_dyn = float(comparison.get("orderCountDynamic") or 0) / 100.0
    raw_add = float(conv.get("addToCartPercent") or 0)
    raw_order = float(conv.get("cartToOrderPercent") or 0)
    raw_buyout = float(conv.get("buyoutPercent") or selected.get("buyoutPercent") or 0)
    status = "Норма"
    status_level = "good"
    if open_dyn <= -drop_threshold:
        status, status_level = "Просадка переходов", "bad"
    elif cart_dyn <= -drop_threshold:
        status, status_level = "Просадка корзин", "bad"
    elif order_dyn <= -drop_threshold:
        status, status_level = "Просадка заказов", "bad"
    elif ad_clicks > 0 and float(ad.get("orders") or 0) == 0:
        status, status_level = "Реклама без заказов", "warn"
    return SimpleNamespace(
        nm_id=nm_id,
        name=str(product.get("title") or ad.get("name") or "—"),
        sku=str(product.get("vendorCode") or "—"),
        open_count=open_count,
        cart_count=cart_count,
        order_count=order_count,
        buyout_count=buyout_count,
        open_dyn=open_dyn,
        cart_dyn=cart_dyn,
        order_dyn=order_dyn,
        add_to_cart_cr=raw_add / 100.0 if raw_add > 1 else raw_add,
        cart_to_order_cr=raw_order / 100.0 if raw_order > 1 else raw_order,
        buyout_rate=raw_buyout / 100.0 if raw_buyout > 1 else raw_buyout,
        ad_views=ad_views,
        ad_clicks=ad_clicks,
        ad_ctr=_div(ad_clicks, ad_views),
        ad_cpc=_div(ad_sum, ad_clicks),
        ad_atbs=float(ad.get("atbs") or 0),
        ad_orders=float(ad.get("orders") or 0),
        ad_sum=ad_sum,
        fact_revenue=float(actual.get("revenue") or 0),
        fact_profit=float(actual.get("profit") or 0),
        fact_drr=_div(ad_sum, float(actual.get("revenue") or 0)),
        status=status,
        status_level=status_level,
    )



def _openai_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-5.4-mini").strip() or "gpt-5.4-mini"


def _build_ai_context(date_from: date, date_to: date) -> tuple[str, SimpleNamespace, SimpleNamespace]:
    rows, summary, _ = _actual_sales_rows(
        limit=10000,
        date_from=date_from,
        date_to=date_to,
        query="",
        operation_type="",
        only_negative=False,
        only_missing_cost=False,
    )
    summary.margin = _div(float(summary.profit or 0.0), float(summary.revenue or 0.0))
    summary.drr = _div(float(summary.advertising or 0.0), float(summary.revenue or 0.0))
    sku_rows = _group_sales_by_sku(rows)
    top_profit = sorted(sku_rows, key=lambda r: float(getattr(r, "profit", 0) or 0), reverse=True)[:10]
    worst_profit = sorted(sku_rows, key=lambda r: float(getattr(r, "profit", 0) or 0))[:10]
    high_drr = sorted([r for r in sku_rows if float(getattr(r, "revenue", 0) or 0) > 0], key=lambda r: float(getattr(r, "drr", 0) or 0), reverse=True)[:10]
    low_margin = sorted([r for r in sku_rows if float(getattr(r, "revenue", 0) or 0) > 0], key=lambda r: float(getattr(r, "margin", 0) or 0))[:10]
    missing_cost = [r for r in sku_rows if int(getattr(r, "missing_cost", 0) or 0) > 0][:15]

    reports = []
    try:
        reports = list_dashboard_reports(120, period_type=None)
    except Exception:
        reports = []
    reports = [r for r in reports if r.period_end >= date_from and r.period_start <= date_to]
    reports.sort(key=lambda r: (r.period_start, r.period_end))

    def sku_pack(r):
        return {
            "name": str(getattr(r, "name", "") or "")[:160],
            "sku": str(getattr(r, "sku", "") or "")[:80],
            "units": round(float(getattr(r, "units", 0) or 0), 3),
            "revenue": round(float(getattr(r, "revenue", 0) or 0), 2),
            "profit": round(float(getattr(r, "profit", 0) or 0), 2),
            "margin_percent": round(float(getattr(r, "margin", 0) or 0) * 100, 2),
            "drr_percent": round(float(getattr(r, "drr", 0) or 0) * 100, 2),
            "advertising": round(float(getattr(r, "advertising", 0) or 0), 2),
            "wb_expenses": round(float(getattr(r, "wb_expenses", 0) or 0), 2),
            "cogs": round(float(getattr(r, "cogs", 0) or 0), 2),
            "missing_cost_operations": int(getattr(r, "missing_cost", 0) or 0),
        }

    data = {
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "summary": {
            "operations": int(getattr(summary, "operations", 0) or len(rows)),
            "units": round(float(getattr(summary, "quantity", 0) or 0), 3),
            "revenue": round(float(getattr(summary, "revenue", 0) or 0), 2),
            "payout": round(float(getattr(summary, "payout", 0) or 0), 2),
            "wb_expenses": round(float(getattr(summary, "wb_expenses", 0) or 0), 2),
            "cogs": round(float(getattr(summary, "cogs", 0) or 0), 2),
            "external_expenses": round(float(getattr(summary, "external_expenses", 0) or 0), 2),
            "advertising": round(float(getattr(summary, "advertising", 0) or 0), 2),
            "tax": round(float(getattr(summary, "tax", 0) or 0), 2),
            "profit_before_tax": round(float(getattr(summary, "profit_before_tax", 0) or 0), 2),
            "profit": round(float(getattr(summary, "profit", 0) or 0), 2),
            "margin_percent": round(float(getattr(summary, "margin", 0) or 0) * 100, 2),
            "drr_percent": round(float(getattr(summary, "drr", 0) or 0) * 100, 2),
            "missing_cost_count": int(getattr(summary, "missing_cost_count", 0) or 0),
        },
        "top_profit_skus": [sku_pack(r) for r in top_profit],
        "worst_skus": [sku_pack(r) for r in worst_profit],
        "high_drr_skus": [sku_pack(r) for r in high_drr],
        "low_margin_skus": [sku_pack(r) for r in low_margin],
        "missing_cost_skus": [sku_pack(r) for r in missing_cost],
        "period_reports": [
            {
                "period": _period(r.period_start, r.period_end),
                "type": getattr(r, "period_type", ""),
                "revenue": round(float(r.revenue or 0), 2),
                "profit": round(float(r.profit or 0), 2),
                "advertising": round(float(r.advertising or 0), 2),
                "margin_percent": round(_div(float(r.profit or 0), float(r.revenue or 0)) * 100, 2),
            }
            for r in reports[-20:]
        ],
    }
    stats = SimpleNamespace(sku_count=len(sku_rows), operations=int(getattr(summary, "operations", 0) or len(rows)))
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")), summary, stats


def _extract_openai_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str) and payload.get("output_text"):
        return payload["output_text"]
    chunks: list[str] = []
    for item in payload.get("output", []) or []:
        for content in item.get("content", []) or []:
            if isinstance(content, dict):
                if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                    chunks.append(content["text"])
    return "\n".join(chunks).strip()


def _ask_openai(question: str, context_json: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY не задан в Railway Variables.")
    model = _openai_model()
    system = (
        "Ты AI-аналитик Wildberries для продавца спортпита. Отвечай по-русски, коротко и по делу. "
        "Используй только переданные данные дашборда. Не придумывай цифры и не обещай действий вне системы. "
        "Если данных недостаточно, прямо скажи, что нужно догрузить /syncdaily или /backfill. "
        "Структура ответа: 1) вывод, 2) почему, 3) что проверить, 4) конкретные действия."
    )
    prompt = f"Вопрос пользователя:\n{question}\n\nДанные дашборда JSON:\n{context_json}"
    body = json.dumps({
        "model": model,
        "instructions": system,
        "input": prompt,
        "max_output_tokens": 1200,
    }).encode("utf-8")
    request_obj = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"OpenAI API вернул ошибку {exc.code}: {detail}") from exc
    text = _extract_openai_text(data)
    if not text:
        raise RuntimeError("OpenAI API вернул пустой ответ.")
    return text



@app.route("/funnel-ads")
def funnel_ads():
    if not database_enabled():
        return Response("DATABASE_URL не задан.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    today = _safe_today()
    default_to = today - timedelta(days=1)
    default_from = default_to - timedelta(days=6)
    date_from = _query_date("date_from") or default_from
    date_to = _query_date("date_to") or default_to
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    query = (request.args.get("q") or "").strip().lower()
    drop_threshold = _query_percent("drop", 0.20)
    warnings: list[str] = []
    try:
        funnel_items, funnel_warnings = _load_sales_funnel(date_from, date_to)
        warnings.extend(funnel_warnings)
    except Exception as exc:
        funnel_items = []
        warnings.append(f"Не удалось получить воронку WB: {exc}")
    ad_by_nm, ad_warnings = _load_ad_stats(date_from, date_to)
    warnings.extend(ad_warnings)

    actual_rows, _, _ = _actual_sales_rows(
        limit=20000,
        date_from=date_from,
        date_to=date_to,
        query="",
        operation_type="",
        only_negative=False,
        only_missing_cost=False,
    )
    actual_by_nm: dict[int, dict[str, float]] = {}
    for r in actual_rows:
        try:
            nm_id = int(getattr(r, "nm_id", 0) or 0)
        except Exception:
            nm_id = 0
        if not nm_id:
            continue
        row = actual_by_nm.setdefault(nm_id, {"revenue": 0.0, "profit": 0.0})
        row["revenue"] += float(getattr(r, "revenue", 0.0) or 0.0)
        row["profit"] += float(getattr(r, "profit", 0.0) or 0.0)

    rows = []
    used_nms = set()
    for item in funnel_items:
        product = item.get("product") or {}
        nm_id = int(product.get("nmId") or 0)
        row = _funnel_product_row(item, ad_by_nm.get(nm_id), actual_by_nm, drop_threshold=drop_threshold)
        used_nms.add(nm_id)
        if query and query not in row.name.lower() and query not in row.sku.lower() and query not in str(row.nm_id):
            continue
        rows.append(row)
    for nm_id, ad in ad_by_nm.items():
        if nm_id in used_nms:
            continue
        dummy = {"product": {"nmId": nm_id, "title": ad.get("name") or f"nmID {nm_id}", "vendorCode": "—"}, "statistic": {"selected": {}, "comparison": {}}}
        row = _funnel_product_row(dummy, ad, actual_by_nm, drop_threshold=drop_threshold)
        if query and query not in row.name.lower() and query not in row.sku.lower() and query not in str(row.nm_id):
            continue
        rows.append(row)
    rows.sort(key=lambda r: (0 if r.status_level == "bad" else 1 if r.status_level == "warn" else 2, -abs(r.open_dyn), -r.ad_sum, -r.open_count))

    totals = SimpleNamespace(
        products=len(rows),
        open_count=sum(r.open_count for r in rows),
        cart_count=sum(r.cart_count for r in rows),
        order_count=sum(r.order_count for r in rows),
        buyout_count=sum(r.buyout_count for r in rows),
        ad_views=sum(r.ad_views for r in rows),
        ad_clicks=sum(r.ad_clicks for r in rows),
        ad_sum=sum(r.ad_sum for r in rows),
    )
    totals.cart_cr = _div(totals.cart_count, totals.open_count)
    totals.buyout_rate = _div(totals.buyout_count, totals.order_count)
    totals.ad_ctr = _div(totals.ad_clicks, totals.ad_views)
    insights = []
    for r in rows:
        if r.open_dyn <= -drop_threshold:
            insights.append(SimpleNamespace(level="bad", title="Упали переходы", name=r.name, sku=r.sku, reason=f"Переходы изменились на {_percent(r.open_dyn)}. Проверь позицию, ставку, цену, остатки и видимость карточки."))
        elif r.cart_dyn <= -drop_threshold:
            insights.append(SimpleNamespace(level="bad", title="Упали корзины", name=r.name, sku=r.sku, reason=f"Корзины изменились на {_percent(r.cart_dyn)}. Проверь цену, первый экран, инфографику, отзывы и оффер."))
        elif r.order_dyn <= -drop_threshold:
            insights.append(SimpleNamespace(level="bad", title="Упали заказы", name=r.name, sku=r.sku, reason=f"Заказы изменились на {_percent(r.order_dyn)}. Проверь срок доставки, цену, рейтинг, остатки и конкурентов."))
        elif r.ad_clicks > 0 and r.ad_orders == 0:
            insights.append(SimpleNamespace(level="warn", title="Клики без заказов", name=r.name, sku=r.sku, reason=f"Реклама дала {int(r.ad_clicks)} кликов и 0 заказов. Проверь релевантность запросов, карточку и цену."))
        if len(insights) >= 15:
            break
    return render_template_string(
        FUNNEL_ADS_TEMPLATE,
        date_from_value=_date_input(date_from),
        date_to_value=_date_input(date_to),
        query=query,
        drop_input=_input_percent(drop_threshold),
        drop_threshold=drop_threshold,
        period_label=f"{_date_display(date_from)}–{_date_display(date_to)}",
        rows=rows,
        totals=totals,
        insights=insights,
        warnings=warnings,
        money=_money,
        percent=_percent,
        units=_units,
    )


@app.route("/ai-chat", methods=["GET", "POST"])
def ai_chat():
    if not database_enabled():
        return Response("DATABASE_URL не задан.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    today = _safe_today()
    default_to = today - timedelta(days=1)
    default_from = default_to - timedelta(days=29)
    date_from = _query_date("date_from") or default_from
    date_to = _query_date("date_to") or default_to
    if request.method == "POST":
        try:
            raw_from = request.form.get("date_from", "").strip()
            raw_to = request.form.get("date_to", "").strip()
            if raw_from:
                date_from = datetime.strptime(raw_from, "%Y-%m-%d").date()
            if raw_to:
                date_to = datetime.strptime(raw_to, "%Y-%m-%d").date()
        except ValueError:
            pass
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    question = (request.form.get("question") if request.method == "POST" else request.args.get("question")) or ""
    question = question.strip()
    answer = ""
    error = ""
    context_json, summary, context_stats = _build_ai_context(date_from, date_to)
    if question:
        if not os.getenv("OPENAI_API_KEY", "").strip():
            error = "OPENAI_API_KEY не задан в Railway Variables. Добавь ключ и перезапусти сервис."
        else:
            try:
                answer = _ask_openai(question, context_json)
            except Exception as exc:
                logger.exception("OpenAI AI-chat failed")
                error = str(exc)
    return render_template_string(
        AI_CHAT_TEMPLATE,
        date_from=date_from,
        date_to=date_to,
        question=question,
        answer=answer,
        error=error,
        api_ready=bool(os.getenv("OPENAI_API_KEY", "").strip()),
        model_label=f"Модель: {_openai_model()}",
        summary=summary,
        context_stats=context_stats,
        q1=quote("Почему изменилась прибыль за период?"),
        q2=quote("Какие SKU проверить в первую очередь и почему?"),
        q3=quote("Что сделать, чтобы поднять маржу и не уронить продажи?"),
        money=_money,
        percent=_percent,
        units=_units,
        margin_class=_margin_class,
        date_display=_date_display,
    )


@app.get("/plan-fact")
def plan_fact():
    if not database_enabled():
        return Response("DATABASE_URL не задан.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    today = _safe_today()
    first_day = today.replace(day=1)
    # последний день текущего месяца
    if first_day.month == 12:
        month_end = date(first_day.year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(first_day.year, first_day.month + 1, 1) - timedelta(days=1)
    date_from = _query_date("date_from") or first_day
    date_to = _query_date("date_to") or month_end
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    targets = SimpleNamespace(
        revenue=max(0.0, _query_float("revenue_target", 0.0)),
        profit=max(0.0, _query_float("profit_target", 0.0)),
        units=max(0.0, _query_float("units_target", 0.0)),
        margin=max(0.0, _query_percent("margin_target", 0.15)),
        drr=max(0.0, _query_percent("drr_target", 0.20)),
    )
    rows, actual, cutoff = _actual_sales_rows(
        limit=5000,
        date_from=date_from,
        date_to=min(date_to, today - timedelta(days=1)) if date_to >= today else date_to,
        query="",
        operation_type="",
        only_negative=False,
        only_missing_cost=False,
    )
    # _summarize_sale_rows возвращает SimpleNamespace, добавим безопасные поля.
    actual.margin = _div(float(actual.profit or 0), float(actual.revenue or 0))
    actual.drr = _div(float(actual.advertising or 0), float(actual.revenue or 0))

    effective_to = min(date_to, today - timedelta(days=1)) if date_to >= today else date_to
    elapsed_days = max(1, (effective_to - date_from).days + 1) if effective_to >= date_from else 1
    total_days = max(1, (date_to - date_from).days + 1)
    remaining_days = max(0, total_days - elapsed_days)
    factor = total_days / elapsed_days if elapsed_days else 1.0
    forecast = SimpleNamespace(
        revenue=float(actual.revenue or 0) * factor,
        profit=float(actual.profit or 0) * factor,
        units=float(actual.quantity or 0) * factor,
        margin=float(actual.margin or 0),
        drr=float(actual.drr or 0),
    )
    progress = SimpleNamespace(
        revenue=_div(float(actual.revenue or 0), targets.revenue),
        profit=_div(float(actual.profit or 0), targets.profit),
        units=_div(float(actual.quantity or 0), targets.units),
        margin=_div(float(actual.margin or 0), targets.margin),
        drr=_div(targets.drr, float(actual.drr or 0)) if float(actual.drr or 0) > 0.000001 else 1.0,
    )
    avg = SimpleNamespace(
        revenue_per_day=float(actual.revenue or 0) / elapsed_days,
        profit_per_day=float(actual.profit or 0) / elapsed_days,
        units_per_day=float(actual.quantity or 0) / elapsed_days,
    )
    need = SimpleNamespace(
        revenue_per_day=max(0.0, (targets.revenue - float(actual.revenue or 0)) / remaining_days) if remaining_days and targets.revenue else 0.0,
        profit_per_day=max(0.0, (targets.profit - float(actual.profit or 0)) / remaining_days) if remaining_days and targets.profit else 0.0,
        units_per_day=max(0.0, (targets.units - float(actual.quantity or 0)) / remaining_days) if remaining_days and targets.units else 0.0,
    )

    def _forecast_row(name: str, actual_value: float, forecast_value: float, target_value: float, kind: str = "money") -> dict[str, str]:
        if kind == "percent":
            actual_text = _percent(actual_value)
            forecast_text = _percent(forecast_value)
            target_text = _percent(target_value) if target_value else "не задан"
            need_text = "—"
            ok = forecast_value >= target_value if name.lower().startswith("маржа") else (forecast_value <= target_value if target_value else True)
        elif kind == "units":
            actual_text = f"{_units(actual_value)} шт."
            forecast_text = f"{_units(forecast_value)} шт."
            target_text = f"{_units(target_value)} шт." if target_value else "не задан"
            need_text = f"{_number_input(max(0.0, (target_value - actual_value) / remaining_days) if remaining_days and target_value else 0.0)} шт."
            ok = forecast_value >= target_value if target_value else True
        else:
            actual_text = _money(actual_value)
            forecast_text = _money(forecast_value)
            target_text = _money(target_value) if target_value else "не задан"
            need_text = _money(max(0.0, (target_value - actual_value) / remaining_days) if remaining_days and target_value else 0.0)
            ok = forecast_value >= target_value if target_value else True
        if not target_value:
            status = "цель не задана"
            cls = "muted"
        elif ok:
            status = "идём в план"
            cls = "profit-pos"
        else:
            status = "ниже плана"
            cls = "profit-neg"
        return {"name": name, "actual": actual_text, "forecast": forecast_text, "target": target_text, "need_per_day": need_text, "status": status, "status_class": cls, "forecast_class": cls}

    forecast_rows = [
        _forecast_row("Выручка", float(actual.revenue or 0), forecast.revenue, targets.revenue, "money"),
        _forecast_row("Чистая прибыль", float(actual.profit or 0), forecast.profit, targets.profit, "money"),
        _forecast_row("Продажи", float(actual.quantity or 0), forecast.units, targets.units, "units"),
        _forecast_row("Маржа", float(actual.margin or 0), forecast.margin, targets.margin, "percent"),
        _forecast_row("ДРР", float(actual.drr or 0), forecast.drr, targets.drr, "percent"),
    ]
    margin_status = "цель не задана" if not targets.margin else ("выше цели" if actual.margin >= targets.margin else "ниже цели")
    drr_status = "лимит не задан" if not targets.drr else ("в пределах лимита" if actual.drr <= targets.drr else "выше лимита")
    return render_template_string(
        PLAN_FACT_TEMPLATE,
        date_from=date_from,
        date_to=date_to,
        targets=targets,
        actual=actual,
        forecast=forecast,
        progress=progress,
        forecast_rows=forecast_rows,
        elapsed_days=elapsed_days,
        total_days=total_days,
        remaining_days=remaining_days,
        avg=avg,
        need=need,
        margin_status=margin_status,
        drr_status=drr_status,
        money=_money,
        percent=_percent,
        units=_units,
        number_input=_number_input,
        percent_input=_input_percent,
        margin_class=_margin_class,
        date_display=_date_display,
    )

@app.get("/supply-planner")
def supply_planner():
    if not database_enabled():
        return Response("DATABASE_URL не задан.", 503, {"Content-Type": "text/plain; charset=utf-8"})

    today = _safe_today()
    default_end = today - timedelta(days=1)
    date_to = _query_date("date_to") or default_end
    date_from = _query_date("date_from") or (date_to - timedelta(days=27))
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    lead_days = _query_int("lead_days", 10, minimum=0, maximum=180)
    safety_days = _query_int("safety_days", 7, minimum=0, maximum=180)
    target_days = _query_int("target_days", 30, minimum=1, maximum=365)
    low_days = _query_int("low_days", 14, minimum=1, maximum=365)
    query = request.args.get("q", "").strip()[:120]

    sales_rows = list_product_summaries(
        2000,
        period_type="weekly",
        date_from=date_from,
        date_to=date_to,
        query=query,
    )
    stock_records: dict[tuple[str, Any], dict[str, Any]] = {}
    warning = ""
    error = ""
    try:
        stock_records, warning, seller_names = asyncio.run(_load_current_stock_records())
    except RuntimeError as exc:
        error = str(exc)
        seller_names = []
    except WbApiError as exc:
        error = str(exc)
        seller_names = []
    except Exception as exc:
        logger.exception("Supply planner failed")
        error = f"Не удалось загрузить остатки WB: {type(exc).__name__}: {exc}"
        seller_names = []

    combined: dict[tuple[str, Any], dict[str, Any]] = {}
    # Проходим по nm-ключам и избегаем дублей.
    seen_nm: set[int] = set()
    for key, rec in stock_records.items():
        if key[0] != "nm":
            continue
        nm_id = int(rec.get("nm_id") or 0)
        if nm_id in seen_nm:
            continue
        seen_nm.add(nm_id)
        if query:
            q = query.casefold()
            blob = f"{rec.get('name','')} {rec.get('sku','')} {rec.get('nm_id','')}".casefold()
            if q not in blob:
                continue
        combined[("nm", nm_id)] = {
            "nm_id": nm_id,
            "sku": rec.get("sku") or f"WB-{nm_id}",
            "name": rec.get("name") or f"Товар WB {nm_id}",
            "fbw": float(rec.get("fbw") or 0),
            "fbs": float(rec.get("fbs") or 0),
            "to_client": float(rec.get("to_client") or 0),
            "from_client": float(rec.get("from_client") or 0),
            "sales_units": 0.0,
            "sales_days": 0,
            "cogs_per_unit": 0.0,
        }

    for row in sales_rows:
        key = _stock_key(row.nm_id, row.sku)
        # Если есть остатки по nmID, используем именно их ключ, чтобы не плодить строки.
        stock_rec = stock_records.get(key)
        if stock_rec and stock_rec.get("nm_id"):
            key = ("nm", int(stock_rec["nm_id"]))
        item = combined.setdefault(
            key,
            {
                "nm_id": int(row.nm_id) if row.nm_id else (int(stock_rec["nm_id"]) if stock_rec and stock_rec.get("nm_id") else None),
                "sku": row.sku,
                "name": row.name,
                "fbw": float(stock_rec.get("fbw") or 0) if stock_rec else 0.0,
                "fbs": float(stock_rec.get("fbs") or 0) if stock_rec else 0.0,
                "to_client": float(stock_rec.get("to_client") or 0) if stock_rec else 0.0,
                "from_client": float(stock_rec.get("from_client") or 0) if stock_rec else 0.0,
                "sales_units": 0.0,
                "sales_days": 0,
                "cogs_per_unit": 0.0,
            },
        )
        item["sales_units"] += float(row.units or 0)
        active_days = max(1, (row.last_period - row.first_period).days + 1)
        item["sales_days"] = max(int(item.get("sales_days") or 0), active_days)
        if abs(float(row.units or 0)) > 0.000001:
            item["cogs_per_unit"] = float(row.cogs or 0) / float(row.units or 1)
        if not item.get("sku"):
            item["sku"] = row.sku
        if not item.get("name"):
            item["name"] = row.name

    rows: list[dict[str, Any]] = []
    planning_days = lead_days + safety_days + target_days
    reorder_threshold = lead_days + safety_days
    for item in combined.values():
        available = float(item.get("fbw") or 0) + float(item.get("fbs") or 0)
        sales_days = int(item.get("sales_days") or max(1, (date_to - date_from).days + 1))
        sales_units = float(item.get("sales_units") or 0)
        daily_sales = sales_units / sales_days if sales_days > 0 else 0.0
        days_left = available / daily_sales if daily_sales > 0.000001 else None
        min_supply = max(0, int((daily_sales * reorder_threshold - available) + 0.999999)) if daily_sales > 0 else 0
        recommended = max(0, int((daily_sales * planning_days - available) + 0.999999)) if daily_sales > 0 else 0
        cogs_per_unit = float(item.get("cogs_per_unit") or 0)
        investment = recommended * cogs_per_unit if recommended and cogs_per_unit else 0.0
        if daily_sales <= 0.000001:
            status = "⚪ нет продаж"
            status_class = "muted"
            send_by = "—"
            rank = 4
        elif available <= 0.000001:
            status = "🔴 нет остатка"
            status_class = "profit-neg"
            send_by = today.strftime("%d.%m.%Y")
            rank = 0
        elif days_left is not None and days_left <= reorder_threshold:
            status = "🔴 срочно"
            status_class = "profit-neg"
            send_by = today.strftime("%d.%m.%Y")
            rank = 1
        elif days_left is not None and days_left < low_days:
            status = "🟠 скоро"
            status_class = "value warn"
            send_by = (today + timedelta(days=max(0, int(days_left - reorder_threshold)))).strftime("%d.%m.%Y")
            rank = 2
        else:
            status = "🟢 норма"
            status_class = "profit-pos"
            send_by = (today + timedelta(days=max(0, int((days_left or 0) - reorder_threshold)))).strftime("%d.%m.%Y") if days_left else "—"
            rank = 3
        rows.append({
            "rank": rank,
            "status": status,
            "status_class": status_class,
            "name": item.get("name") or item.get("sku") or "—",
            "sku": item.get("sku") or "—",
            "nm_id": item.get("nm_id"),
            "available": available,
            "fbw": float(item.get("fbw") or 0),
            "fbs": float(item.get("fbs") or 0),
            "to_client": float(item.get("to_client") or 0),
            "from_client": float(item.get("from_client") or 0),
            "sales_units": sales_units,
            "daily_sales": daily_sales,
            "days_left": days_left,
            "days_left_text": _fmt_days(days_left),
            "send_by": send_by,
            "min_supply": min_supply,
            "recommended_supply": recommended,
            "cogs_per_unit": cogs_per_unit,
            "investment": investment,
        })
    rows.sort(key=lambda r: (r["rank"], r["days_left"] if r["days_left"] is not None else 999999, -r["daily_sales"], r["name"].casefold()))
    totals = {
        "products": len(rows),
        "critical": sum(1 for r in rows if r["rank"] in {0, 1}),
        "low": sum(1 for r in rows if r["rank"] == 2),
        "available": sum(r["available"] for r in rows),
        "fbw": sum(r["fbw"] for r in rows),
        "fbs": sum(r["fbs"] for r in rows),
        "to_client": sum(r["to_client"] for r in rows),
        "from_client": sum(r["from_client"] for r in rows),
        "recommended": sum(r["recommended_supply"] for r in rows),
        "investment": sum(r["investment"] for r in rows),
        "daily_sales": sum(r["daily_sales"] for r in rows),
    }
    return render_template_string(
        SUPPLY_TEMPLATE,
        rows=rows,
        totals=totals,
        error=error,
        warning=warning,
        as_of=today.strftime("%d.%m.%Y"),
        date_from_value=date_from.isoformat(),
        date_to_value=date_to.isoformat(),
        lead_days=lead_days,
        safety_days=safety_days,
        target_days=target_days,
        low_days=low_days,
        query=query,
        money=_money,
        percent=_percent,
        units=_units,
        number=_number_input,
    )


@app.get("/logistics")
def logistics():
    if not database_enabled():
        return Response("DATABASE_URL не задан.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    selected_type = request.args.get("period_type", "weekly").strip()
    if selected_type not in {"weekly", "daily", "xlsx"}:
        selected_type = "weekly"
    date_from = _query_date("date_from")
    date_to = _query_date("date_to")
    query = request.args.get("q", "").strip()[:120]
    warn_wb = _query_percent("warn_wb", 0.25)
    critical_wb = _query_percent("critical_wb", 0.35)
    source_rows = list_product_summaries(
        1000,
        period_type=selected_type,
        date_from=date_from,
        date_to=date_to,
        query=query,
    )
    rows = [_logistics_row(row, warn_wb=warn_wb, critical_wb=critical_wb) for row in source_rows if abs(float(row.revenue or 0)) > 0.000001 or abs(float(row.units or 0)) > 0.000001]
    rows.sort(key=lambda r: (r["burden_share"], r["wb_share"], r["revenue"]), reverse=True)
    op_rows = list_sale_operations(
        5000,
        period_type=selected_type,
        date_from=date_from,
        date_to=date_to,
        query=query,
    )
    detail_totals, detail_breakdown, detail_rows = _operation_logistics_details(list(op_rows))
    total_revenue = sum(r["revenue"] for r in rows)
    total_units = sum(r["units"] for r in rows)
    total_wb = sum(r["wb_services"] for r in rows)
    total_external = sum(r["external"] for r in rows)
    totals = {
        "products": len(rows),
        "units": total_units,
        "revenue": total_revenue,
        "payout": sum(r["payout"] for r in rows),
        "wb_services": total_wb,
        "external": total_external,
        "burden": total_wb + total_external,
        "wb_share": _div(total_wb, total_revenue),
        "external_share": _div(total_external, total_revenue),
        "burden_share": _div(total_wb + total_external, total_revenue),
        "wb_per_unit": _div(total_wb, total_units),
        "external_per_unit": _div(total_external, total_units),
        "burden_per_unit": _div(total_wb + total_external, total_units),
    }
    chart_rows = rows[:12]
    period_label = "выбранный период"
    if date_from or date_to:
        period_label = f"{date_from.strftime('%d.%m.%Y') if date_from else '—'} — {date_to.strftime('%d.%m.%Y') if date_to else '—'}"
    return render_template_string(
        LOGISTICS_TEMPLATE,
        title=os.getenv("DASHBOARD_TITLE", "WB Profit Dashboard").strip() or "WB Profit Dashboard",
        rows=rows,
        totals=totals,
        selected_type=selected_type,
        date_from_value=date_from.isoformat() if date_from else "",
        date_to_value=date_to.isoformat() if date_to else "",
        query=query,
        warn_wb=warn_wb,
        critical_wb=critical_wb,
        warn_input=_input_percent(warn_wb),
        critical_input=_input_percent(critical_wb),
        period_label=period_label,
        chart_labels=[(r["name"] or r["sku"] or "—")[:28] for r in chart_rows],
        chart_wb=[round(r["wb_share"] * 100, 2) for r in chart_rows],
        chart_external=[round(r["external_share"] * 100, 2) for r in chart_rows],
        detail_totals=SimpleNamespace(**detail_totals),
        detail_breakdown=detail_breakdown,
        detail_rows=detail_rows,
        abs=abs,
        money=_money,
        percent=_percent,
        units=_units,
        type_label=_type_label,
    )


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




def _content_cards_meta() -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any]], str]:
    """Loads richer WB card metadata for SKU matrix: brand, subject/category, photo, sizes."""
    token = os.getenv("WB_API_TOKEN", "").strip()
    if not token:
        return {}, {}, "WB_API_TOKEN не задан: карточки товаров не загружены."
    url = "https://content-api.wildberries.ru/content/v2/get/cards/list?locale=ru"
    by_nm: dict[int, dict[str, Any]] = {}
    by_sku: dict[str, dict[str, Any]] = {}
    cursor: dict[str, Any] = {"limit": 100}
    warning = ""
    for _page in range(20):
        body = {"settings": {"sort": {"ascending": True}, "filter": {"withPhoto": -1}, "cursor": cursor}}
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={**_wb_tariff_headers(), "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                return {}, {}, "Для фото, бренда и категории нужен WB-токен с доступом к карточкам/Контенту."
            return {}, {}, f"Карточки WB не загружены: ошибка {exc.code}."
        except Exception as exc:
            return {}, {}, f"Карточки WB не загружены: {exc}"
        cards = payload.get("cards") if isinstance(payload, dict) else []
        if not isinstance(cards, list):
            break
        for raw in cards:
            if not isinstance(raw, dict):
                continue
            try:
                nm = int(raw.get("nmID") or raw.get("nmId") or 0)
            except (TypeError, ValueError):
                nm = 0
            if nm <= 0:
                continue
            photos = raw.get("photos") or []
            photo_url = ""
            if isinstance(photos, list) and photos:
                first = photos[0] if isinstance(photos[0], dict) else {}
                photo_url = str(first.get("c246x328") or first.get("big") or first.get("tm") or "")
            sizes = raw.get("sizes") or []
            variant = ""
            if isinstance(sizes, list) and sizes:
                vals = []
                for size in sizes[:3]:
                    if isinstance(size, dict):
                        val = str(size.get("techSize") or size.get("wbSize") or size.get("name") or "").strip()
                        if val and val not in vals:
                            vals.append(val)
                variant = ", ".join(vals)
            meta = {
                "nm_id": nm,
                "sku": str(raw.get("vendorCode") or "").strip(),
                "name": str(raw.get("title") or "").strip(),
                "brand": str(raw.get("brand") or "").strip(),
                "category": str(raw.get("subjectName") or raw.get("object") or raw.get("imtName") or "").strip(),
                "variant": variant,
                "photo_url": photo_url,
                "product_url": _wb_product_url(nm),
            }
            by_nm[nm] = meta
            if meta["sku"]:
                by_sku[meta["sku"].casefold()] = meta
        cur = payload.get("cursor") or {}
        total = int(cur.get("total") or 0) if isinstance(cur, dict) else 0
        if total < int(cursor.get("limit", 100)):
            break
        updated_at = cur.get("updatedAt") if isinstance(cur, dict) else None
        next_nm = cur.get("nmID") or cur.get("nmId") if isinstance(cur, dict) else None
        if not updated_at or not next_nm:
            break
        cursor = {"limit": 100, "updatedAt": updated_at, "nmID": next_nm}
    return by_nm, by_sku, warning


def _apply_meta_to_matrix_rows(rows: list[SimpleNamespace]) -> str:
    by_nm, by_sku, warning = _content_cards_meta()
    for row in rows:
        meta = None
        try:
            nm = int(getattr(row, "nm_id", 0) or 0)
        except (TypeError, ValueError):
            nm = 0
        if nm:
            meta = by_nm.get(nm)
        if not meta:
            meta = by_sku.get(str(getattr(row, "sku", "") or "").casefold())
        if not meta:
            continue
        row.nm_id = meta.get("nm_id") or row.nm_id
        row.name = meta.get("name") or row.name
        row.category = meta.get("category") or row.category
        row.brand = meta.get("brand") or row.brand
        row.variant = meta.get("variant") or row.variant
        row.photo_url = meta.get("photo_url") or row.photo_url
        row.product_url = meta.get("product_url") or row.product_url
        try:
            data = json.loads(row.json.replace("&#39;", "'"))
            data["nmId"] = int(row.nm_id or 0)
            data["sku"] = row.sku
            row.json = json.dumps(data, ensure_ascii=False).replace("'", "&#39;")
        except Exception:
            pass
    return warning


def _ensure_unit_matrix_scenario_table() -> None:
    with _scenario_connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS unit_matrix_scenarios (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_unit_matrix_scenarios_updated ON unit_matrix_scenarios(updated_at DESC)")


def _matrix_scenario_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "payload": row.get("payload") or {},
        "created_at": row["created_at"].strftime("%d.%m.%Y %H:%M") if row.get("created_at") else "",
        "updated_at": row["updated_at"].strftime("%d.%m.%Y %H:%M") if row.get("updated_at") else "",
    }


def _list_unit_matrix_scenarios(limit: int = 100) -> list[dict[str, Any]]:
    if not database_enabled():
        return []
    try:
        _ensure_unit_matrix_scenario_table()
        with _scenario_connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, name, payload, created_at, updated_at
                    FROM unit_matrix_scenarios
                    ORDER BY updated_at DESC, id DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [_matrix_scenario_payload(row) for row in cursor.fetchall()]
    except Exception:
        logger.exception("Не удалось получить сценарии SKU-юнитки")
        return []



def _list_unit_calc_scenarios(limit: int = 300) -> list[dict[str, Any]]:
    if not database_enabled():
        return []
    try:
        _ensure_scenario_table()
        with _scenario_connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, name, query, created_at, updated_at
                    FROM unit_calc_scenarios
                    ORDER BY updated_at DESC, id DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [_scenario_payload(row) for row in cursor.fetchall()]
    except Exception:
        logger.exception("Не удалось получить сценарии калькулятора юнитки")
        return []

@app.get("/unit-matrix")
def unit_matrix():
    if not database_enabled():
        return Response("DATABASE_URL не задан.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    selected_type = request.args.get("period_type", "actual").strip()
    if selected_type not in {"actual", "weekly", "daily", "xlsx"}:
        selected_type = "actual"
    date_from = _query_date("date_from")
    date_to = _query_date("date_to") or date.today()
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    query = request.args.get("q", "").strip()[:120]
    target_margin = _query_percent("target_margin", 0.15)

    if selected_type == "actual":
        sale_rows, _summary, _cutoff = _actual_sales_rows(
            limit=20000,
            date_from=date_from,
            date_to=date_to,
            query=query,
            operation_type="",
            only_negative=False,
            only_missing_cost=False,
        )
        detail_totals, detail_breakdown, detail_rows = _operation_logistics_details(list(sale_rows))
        detail_by_sku = {str(r.get("sku") or "").casefold(): r for r in detail_rows}
        source_rows = _sku_summary_from_sales(list(sale_rows))
        # _sku_summary_from_sales does not include payout/wb_expenses, so enrich from operation rows.
        payout_by_sku: dict[str, float] = {}
        for op in sale_rows:
            k = str(getattr(op, "sku", "") or "").casefold()
            payout_by_sku[k] = payout_by_sku.get(k, 0.0) + float(getattr(op, "payout", 0.0) or 0.0)
        enriched = []
        for row in source_rows:
            d = dict(row.__dict__)
            d["payout"] = payout_by_sku.get(str(row.sku or "").casefold(), float(getattr(row, "revenue", 0.0) or 0.0))
            d["nm_id"] = getattr(row, "nm_id", None)
            enriched.append(SimpleNamespace(**d))
        source_rows = enriched
        period_label = "Актуально без дублей"
    else:
        source_rows = list_product_summaries(1000, period_type=selected_type, date_from=date_from, date_to=date_to, query=query)
        op_rows = list_sale_operations(5000, period_type=selected_type, date_from=date_from, date_to=date_to, query=query)
        _tot, _bd, detail_rows = _operation_logistics_details(list(op_rows))
        detail_by_sku = {str(r.get("sku") or "").casefold(): r for r in detail_rows}
        period_label = _type_label(selected_type)

    matrix_rows = []
    for row in source_rows:
        if abs(float(getattr(row, "units", 0.0) or 0.0)) <= 0.000001 and abs(float(getattr(row, "revenue", 0.0) or 0.0)) <= 0.000001:
            continue
        d = detail_by_sku.get(str(getattr(row, "sku", "") or "").casefold())
        matrix_rows.append(_unit_matrix_row_from_summary(row, d))
    matrix_rows.sort(key=lambda r: (r.name or r.sku).casefold())
    meta_warning = _apply_meta_to_matrix_rows(matrix_rows)

    tariff_date = (date_to or date.today()).isoformat()
    fbs_warehouses, fbs_map, fbs_warning = _load_box_tariff_map(tariff_date, "fbs")
    fbw_warehouses, fbw_map, fbw_warning = _load_box_tariff_map(tariff_date, "fbw")
    warehouse_options = sorted(set(fbs_warehouses) | set(fbw_warehouses), key=lambda x: x.casefold())
    tariff_maps = {"fbs": fbs_map, "fbw": fbw_map}
    tariff_warning = fbs_warning or fbw_warning or meta_warning
    categories = sorted({r.category for r in matrix_rows if r.category}, key=lambda x: x.casefold())
    brands = sorted({r.brand for r in matrix_rows if r.brand}, key=lambda x: x.casefold())
    return render_template_string(
        UNIT_MATRIX_TEMPLATE,
        rows=matrix_rows,
        selected_type=selected_type,
        date_from_value=date_from.isoformat() if date_from else "",
        date_to_value=date_to.isoformat() if date_to else "",
        query=query,
        target_margin_input=_input_percent(target_margin),
        target_margin=target_margin,
        warehouse_options=warehouse_options,
        tariff_maps=tariff_maps,
        tariff_warning=tariff_warning,
        categories=categories,
        brands=brands,
        period_label=period_label,
        matrix_scenarios=[{k: v for k, v in item.items() if k != "payload"} for item in _list_unit_matrix_scenarios(200)],
        calc_scenarios=_list_unit_calc_scenarios(500),
        money=_money,
        percent=_percent,
        units=_units,
    )


@app.get("/unit-calculator")
def unit_calculator():
    if not database_enabled():
        return Response("DATABASE_URL не задан.", 503, {"Content-Type": "text/plain; charset=utf-8"})
    products = list_product_summaries(1000, period_type="weekly")
    products = sorted(products, key=lambda r: ((r.name or r.sku or "").casefold(), r.sku or ""))
    selected_sku = request.args.get("sku", "").strip()
    product_summary = None
    if selected_sku:
        key = selected_sku.casefold()
        product_summary = next((p for p in products if (p.sku or "").casefold() == key), None)
    values = _unit_calculator_values(product_summary)
    tariff_notice = None
    warehouse_options: list[str] = []
    if str(values.warehouse_mode or "api") == "api":
        warehouse_options, wh_notice = _load_tariff_warehouse_names(str(values.package_type or "box"), str(values.tariff_date or date.today().isoformat()))
        if wh_notice and not tariff_notice:
            tariff_notice = wh_notice
    if request.args.get("autoload_tariffs"):
        values, tariff_notice = _apply_wb_tariffs(values)
    if request.args.get("actual_logistics"):
        values, tariff_notice = _apply_actual_logistics_from_sales(values)
    calculated = bool(request.args)
    calc, breakdown = _calculate_unit_plan(values) if calculated else (DotDict(), [])
    return render_template_string(
        UNIT_CALCULATOR_TEMPLATE,
        title=os.getenv("DASHBOARD_TITLE", "WB Profit Dashboard").strip() or "WB Profit Dashboard",
        products=products,
        values=values,
        warehouse_options=warehouse_options,
        manual_warehouses=COMMON_FBS_POINTS,
        tariff_notice=tariff_notice,
        calculated=calculated,
        calc=calc,
        breakdown=breakdown,
        money=_money,
        percent=_percent,
        units=_units,
        number=_number_input,
        number_input=_number_input,
        percent_input=_input_percent,
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



def _json(data: dict[str, Any], status: int = 200) -> Response:
    return Response(json.dumps(data, ensure_ascii=False), status, {"Content-Type": "application/json; charset=utf-8"})


def _scenario_connect():
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL не задан.")
    return psycopg.connect(url, connect_timeout=12, row_factory=dict_row)


def _ensure_scenario_table() -> None:
    with _scenario_connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS unit_calc_scenarios (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    query TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_unit_calc_scenarios_updated ON unit_calc_scenarios(updated_at DESC)"
            )


def _scenario_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "query": row.get("query") or "",
        "created_at": row["created_at"].strftime("%d.%m.%Y %H:%M") if row.get("created_at") else "",
        "updated_at": row["updated_at"].strftime("%d.%m.%Y %H:%M") if row.get("updated_at") else "",
    }


@app.get("/api/unit-scenarios")
def api_unit_scenarios_list():
    if not database_enabled():
        return _json({"error": "DATABASE_URL не задан."}, 503)
    try:
        _ensure_scenario_table()
        with _scenario_connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, name, query, created_at, updated_at
                    FROM unit_calc_scenarios
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 300
                    """
                )
                return _json({"items": [_scenario_payload(row) for row in cursor.fetchall()]})
    except Exception as exc:
        logger.exception("Не удалось получить сценарии юнитки")
        return _json({"error": str(exc)}, 500)


@app.post("/api/unit-scenarios")
def api_unit_scenarios_save():
    if not database_enabled():
        return _json({"error": "DATABASE_URL не задан."}, 503)
    try:
        data = request.get_json(silent=True) or {}
        name = str(data.get("name") or "").strip()
        query = str(data.get("query") or "").strip()
        if not name:
            return _json({"error": "Название сценария обязательно."}, 400)
        if len(name) > 180:
            name = name[:180]
        _ensure_scenario_table()
        with _scenario_connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO unit_calc_scenarios (name, query)
                    VALUES (%s, %s)
                    ON CONFLICT (name)
                    DO UPDATE SET query = EXCLUDED.query, updated_at = NOW()
                    RETURNING id, name, query, created_at, updated_at
                    """,
                    (name, query),
                )
                row = cursor.fetchone()
                return _json(_scenario_payload(row))
    except Exception as exc:
        logger.exception("Не удалось сохранить сценарий юнитки")
        return _json({"error": str(exc)}, 500)


@app.post("/api/unit-scenarios/<int:scenario_id>/duplicate")
def api_unit_scenarios_duplicate(scenario_id: int):
    if not database_enabled():
        return _json({"error": "DATABASE_URL не задан."}, 503)
    try:
        _ensure_scenario_table()
        with _scenario_connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT name, query FROM unit_calc_scenarios WHERE id = %s", (scenario_id,))
                row = cursor.fetchone()
                if not row:
                    return _json({"error": "Сценарий не найден."}, 404)
                params = urllib.parse.parse_qs(row["query"] or "", keep_blank_values=True)
                flat = urllib.parse.parse_qsl(row["query"] or "", keep_blank_values=True)
                p = urllib.parse.urlencode([(k, ("fbw" if k == "scheme" and v.lower() == "fbs" else "fbs" if k == "scheme" and v.lower() == "fbw" else v)) for k, v in flat])
                old_scheme = (params.get("scheme", ["fbs"])[0] or "fbs").upper()
                new_scheme = "FBW" if old_scheme == "FBS" else "FBS"
                base_name = (row["name"] or "Сценарий").strip()
                name = f"{base_name} · {new_scheme}"
                # если такой дубль уже есть, добавляем время, чтобы не перезаписать старый вариант
                cursor.execute("SELECT 1 FROM unit_calc_scenarios WHERE name = %s", (name,))
                if cursor.fetchone():
                    name = f"{name} · {datetime.now().strftime('%d.%m %H:%M')}"
                cursor.execute(
                    """
                    INSERT INTO unit_calc_scenarios (name, query)
                    VALUES (%s, %s)
                    RETURNING id, name, query, created_at, updated_at
                    """,
                    (name, p),
                )
                return _json(_scenario_payload(cursor.fetchone()))
    except Exception as exc:
        logger.exception("Не удалось продублировать сценарий юнитки")
        return _json({"error": str(exc)}, 500)


@app.delete("/api/unit-scenarios/<int:scenario_id>")
def api_unit_scenarios_delete(scenario_id: int):
    if not database_enabled():
        return _json({"error": "DATABASE_URL не задан."}, 503)
    try:
        _ensure_scenario_table()
        with _scenario_connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM unit_calc_scenarios WHERE id = %s", (scenario_id,))
        return Response("", 204)
    except Exception as exc:
        logger.exception("Не удалось удалить сценарий юнитки")
        return _json({"error": str(exc)}, 500)




@app.get("/api/unit-matrix-scenarios")
def api_unit_matrix_scenarios_list():
    if not database_enabled():
        return _json({"error": "DATABASE_URL не задан."}, 503)
    try:
        return _json({"items": [{k: v for k, v in item.items() if k != "payload"} for item in _list_unit_matrix_scenarios(300)]})
    except Exception as exc:
        logger.exception("Не удалось получить сценарии SKU-юнитки")
        return _json({"error": str(exc)}, 500)


@app.get("/api/unit-matrix-scenarios/<int:scenario_id>")
def api_unit_matrix_scenario_get(scenario_id: int):
    if not database_enabled():
        return _json({"error": "DATABASE_URL не задан."}, 503)
    try:
        _ensure_unit_matrix_scenario_table()
        with _scenario_connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id, name, payload, created_at, updated_at FROM unit_matrix_scenarios WHERE id = %s", (scenario_id,))
                row = cursor.fetchone()
                if not row:
                    return _json({"error": "Сценарий не найден."}, 404)
                return _json(_matrix_scenario_payload(row))
    except Exception as exc:
        logger.exception("Не удалось загрузить сценарий SKU-юнитки")
        return _json({"error": str(exc)}, 500)


@app.post("/api/unit-matrix-scenarios")
def api_unit_matrix_scenario_save():
    if not database_enabled():
        return _json({"error": "DATABASE_URL не задан."}, 503)
    try:
        data = request.get_json(silent=True) or {}
        name = str(data.get("name") or "").strip()
        payload = data.get("payload") or {}
        if not name:
            return _json({"error": "Название сценария обязательно."}, 400)
        if len(name) > 180:
            name = name[:180]
        _ensure_unit_matrix_scenario_table()
        with _scenario_connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO unit_matrix_scenarios (name, payload)
                    VALUES (%s, %s::jsonb)
                    ON CONFLICT (name)
                    DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
                    RETURNING id, name, payload, created_at, updated_at
                    """,
                    (name, json.dumps(payload, ensure_ascii=False)),
                )
                return _json(_matrix_scenario_payload(cursor.fetchone()))
    except Exception as exc:
        logger.exception("Не удалось сохранить сценарий SKU-юнитки")
        return _json({"error": str(exc)}, 500)


@app.delete("/api/unit-matrix-scenarios/<int:scenario_id>")
def api_unit_matrix_scenario_delete(scenario_id: int):
    if not database_enabled():
        return _json({"error": "DATABASE_URL не задан."}, 503)
    try:
        _ensure_unit_matrix_scenario_table()
        with _scenario_connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM unit_matrix_scenarios WHERE id = %s", (scenario_id,))
        return Response("", 204)
    except Exception as exc:
        logger.exception("Не удалось удалить сценарий SKU-юнитки")
        return _json({"error": str(exc)}, 500)


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
