#!/usr/bin/env python3
"""
Salesforce Object HTML Documenter
Usage: python sf_doc_generator.py <ObjectApiName> <OrgAlias>
"""

import json
import re
import subprocess
import sys
import os
from datetime import datetime
from html import escape


# ── Salesforce data fetchers ─────────────────────────────────────────────────

def run_sf_describe(object_name, org_alias):
    cmd = f'sf sobject describe --sobject {object_name} --target-org {org_alias} --json'
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', shell=True)
    raw = result.stdout.lstrip('﻿')
    return json.loads(raw)


def run_soql(query, org_alias, tooling=False):
    safe_query = query.replace('"', '\\"')
    flag = '--use-tooling-api' if tooling else ''
    cmd = f'sf data query --query "{safe_query}" --target-org {org_alias} {flag} --json'
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', shell=True)
    try:
        data = json.loads(result.stdout.lstrip('﻿'))
        return data.get('result', {}).get('records', [])
    except Exception:
        return []


def get_field_descriptions(object_name, org_alias):
    records = run_soql(
        f"SELECT QualifiedApiName, Description FROM FieldDefinition WHERE EntityDefinition.QualifiedApiName = '{object_name}'",
        org_alias, tooling=True
    )
    return {r['QualifiedApiName']: r.get('Description') or '' for r in records}


def get_validation_rules(object_name, org_alias):
    return run_soql(
        f"SELECT ValidationName, Description, ErrorMessage, ErrorDisplayField, Active FROM ValidationRule "
        f"WHERE EntityDefinition.QualifiedApiName = '{object_name}' ORDER BY ValidationName",
        org_alias, tooling=True
    )


def get_record_triggered_flows(object_label, org_alias):
    return run_soql(
        f"SELECT ApiName, Label, TriggerType, IsActive, Description FROM FlowDefinitionView "
        f"WHERE TriggerObjectOrEventLabel = '{object_label}' "
        f"AND TriggerType IN ('RecordBeforeSave', 'RecordAfterSave', 'RecordBeforeDelete')",
        org_alias, tooling=False
    )


# ── Formula explanations via Claude API ─────────────────────────────────────

def get_formula_explanations(formula_fields, object_name):
    try:
        import anthropic
    except ImportError:
        print("  [anthropic not installed — skipping formula explanations]")
        return {}

    pairs = [(f['name'], f['calculatedFormula']) for f in formula_fields
             if f.get('calculatedFormula')]
    if not pairs:
        return {}

    print(f"  Generating explanations for {len(pairs)} formula fields...")
    formulas_text = '\n'.join(f'- {name}:\n  {formula}' for name, formula in pairs)

    client = anthropic.Anthropic()
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": (
                    f"Eres un experto en Salesforce. Analiza estas fórmulas del objeto '{object_name}' "
                    f"y proporciona una explicación breve (2-3 frases) en español de lo que hace cada una. "
                    f"Sé conciso y técnico.\n\n"
                    f"Responde ÚNICAMENTE con JSON válido: {{\"field_api_name\": \"explicación\"}}\n\n"
                    f"{formulas_text}"
                )
            }]
        )
        text = msg.content[0].text
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        print(f"  [formula explanations failed: {e}]")
    return {}


# ── Field helpers ────────────────────────────────────────────────────────────

def field_type_display(field):
    ftype = field['type']
    if field.get('calculated'):
        rt = field.get('extraTypeInfo') or ftype
        type_map = {
            'string': 'Text', 'double': 'Number', 'boolean': 'Checkbox',
            'date': 'Date', 'datetime': 'Date/Time', 'currency': 'Currency',
            'percent': 'Percent', 'int': 'Number', 'textarea': 'Text',
        }
        label = type_map.get(rt, rt.capitalize() if rt else 'Text')
        return f"Formula({label})"
    if ftype == 'string':
        return f"Text({field.get('length', '')})"
    elif ftype == 'textarea':
        return f"Long Text({field.get('length', '')})"
    elif ftype == 'picklist':
        return 'Picklist'
    elif ftype == 'multipicklist':
        return 'Multi-Picklist'
    elif ftype == 'reference':
        refs = field.get('referenceTo', [])
        if len(refs) <= 2:
            display = ', '.join(refs)
        else:
            display = f"{refs[0]} +{len(refs) - 1}"
        return f"Lookup({display})"
    elif ftype == 'boolean':
        return 'Checkbox'
    elif ftype == 'double':
        return f"Number({field.get('precision', 0)}, {field.get('scale', 0)})"
    elif ftype == 'int':
        return f"Number({field.get('digits', '')})"
    elif ftype == 'currency':
        return f"Currency({field.get('precision', '')}, {field.get('scale', '')})"
    elif ftype == 'date':
        return 'Date'
    elif ftype == 'datetime':
        return 'Date/Time'
    elif ftype == 'phone':
        return 'Phone'
    elif ftype == 'email':
        return 'Email'
    elif ftype == 'url':
        return 'URL'
    elif ftype == 'id':
        return 'ID'
    elif ftype == 'percent':
        return f"Percent({field.get('precision', '')}, {field.get('scale', '')})"
    elif ftype == 'autonumber':
        return 'Auto Number'
    elif ftype == 'encryptedstring':
        return f"Encrypted Text({field.get('length', '')})"
    else:
        return ftype


def is_required(field):
    return (
        not field.get('nillable', True)
        and field['type'] not in ('id', 'boolean')
        and not field.get('defaultedOnCreate', False)
    )


def get_field_tags(field):
    tags = ['custom' if field['name'].endswith('__c') else 'standard']
    if field['type'] in ('picklist', 'multipicklist'):
        tags.append('picklist')
    if field.get('calculated'):
        tags.append('formula')
    if is_required(field):
        tags.append('required')
    return ' '.join(tags)


# ── HTML section builders ────────────────────────────────────────────────────

def picklist_html(field):
    values = field.get('picklistValues', [])
    active = [v for v in values if v.get('active')]
    if not active:
        return '<em class="empty">vacío</em>'
    items = []
    for v in active:
        api_val = escape(v['value'])
        label = escape(v.get('label') or '')
        if api_val == label:
            items.append(f'<li><code>{api_val}</code></li>')
        else:
            items.append(f'<li><code>{api_val}</code><span class="arrow">→</span>{label}</li>')
    return '<ul class="picklist-values">' + ''.join(items) + '</ul>'


def type_cell_html(field):
    ftype = field['type']
    css_class = 'calculated' if field.get('calculated') else ftype
    badge = f'<span class="type-badge type-{css_class}">{escape(field_type_display(field))}</span>'
    req_badge = '<span class="req-badge" title="Campo requerido">Req</span>' if is_required(field) else ''
    formula_btn = ''
    if field.get('calculated') and field.get('calculatedFormula'):
        row_id = f'frow-{escape(field["name"])}'
        formula_btn = f'<br><button class="formula-toggle-btn" data-row="{row_id}">▶ ver fórmula</button>'
    return f'{badge}{req_badge}{formula_btn}'


def categorize_fields(fields):
    standard = sorted([f for f in fields if not f['name'].endswith('__c')], key=lambda f: f['name'])
    custom = sorted([f for f in fields if f['name'].endswith('__c')], key=lambda f: f['name'])
    return [('Campos estándar', standard), ('Campos custom', custom)]


def build_field_rows(sections, descriptions, formula_explanations=None):
    fe = formula_explanations or {}
    html = ''
    for section_name, section_fields in sections:
        if not section_fields:
            continue
        html += f'<tr class="section-header"><td colspan="5">— {escape(section_name)} —</td></tr>\n'
        for field in section_fields:
            fname = field['name']
            label = escape(field.get('label', ''))
            desc = escape(descriptions.get(fname, '') or '')
            tags = get_field_tags(field)
            pvals = picklist_html(field) if field['type'] in ('picklist', 'multipicklist') else ''
            html += (
                f'<tr class="field-row" data-tags="{tags}">'
                f'<td><code class="api-name" title="Copiar">{escape(fname)}</code></td>'
                f'<td>{label}</td>'
                f'<td class="type-cell">{type_cell_html(field)}</td>'
                f'<td class="desc">{desc}</td>'
                f'<td>{pvals}</td>'
                f'</tr>\n'
            )
            # Formula expand row
            formula = field.get('calculatedFormula') or ''
            if field.get('calculated') and formula:
                row_id = f'frow-{fname}'
                explanation = escape(fe.get(fname, ''))
                expl_html = (
                    f'<div class="frow-expl">'
                    f'<div class="frow-expl-label">💡 Qué hace</div>'
                    f'<p>{explanation}</p>'
                    f'</div>'
                ) if explanation else ''
                html += (
                    f'<tr class="formula-expand-row" id="{row_id}" style="display:none">'
                    f'<td colspan="5">'
                    f'<div class="frow-header">📐 {escape(fname)}</div>'
                    f'<div class="frow-body">'
                    f'<div class="frow-code"><pre>{escape(formula)}</pre></div>'
                    f'{expl_html}'
                    f'</div>'
                    f'</td>'
                    f'</tr>\n'
                )
    return html


def build_validation_rules_html(rules):
    if not rules:
        return '<p class="empty-section">No hay reglas de validación en este objeto.</p>'
    rows = ''
    for r in rules:
        name = escape(r.get('ValidationName', ''))
        desc = escape(r.get('Description') or '')
        error = escape(r.get('ErrorMessage') or '')
        field = escape(r.get('ErrorDisplayField') or 'Page')
        active = r.get('Active', False)
        status_class = 'active-badge' if active else 'inactive-badge'
        status_label = 'Activa' if active else 'Inactiva'
        rows += (
            f'<tr>'
            f'<td><code>{name}</code></td>'
            f'<td><span class="{status_class}">{status_label}</span></td>'
            f'<td class="desc">{desc}</td>'
            f'<td>{error}</td>'
            f'<td>{field}</td>'
            f'</tr>\n'
        )
    return f'''<table class="secondary-table">
  <thead><tr>
    <th>Nombre</th><th>Estado</th><th>Descripción</th>
    <th>Mensaje de error</th><th>Campo de error</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>'''


def build_flows_html(flows):
    if not flows:
        return '<p class="empty-section">No hay Record-Triggered Flows en este objeto.</p>'
    trigger_labels = {
        'RecordBeforeSave': 'Before Save',
        'RecordAfterSave': 'After Save',
        'RecordBeforeDelete': 'Before Delete',
    }
    rows = ''
    for f in flows:
        api_name = escape(f.get('ApiName', ''))
        label = escape(f.get('Label', ''))
        trigger = escape(trigger_labels.get(f.get('TriggerType', ''), f.get('TriggerType', '')))
        desc = escape(f.get('Description') or '')
        active = f.get('IsActive', False)
        status_class = 'active-badge' if active else 'inactive-badge'
        status_label = 'Activo' if active else 'Inactivo'
        rows += (
            f'<tr>'
            f'<td><code>{api_name}</code></td>'
            f'<td>{label}</td>'
            f'<td><span class="{status_class}">{status_label}</span></td>'
            f'<td>{trigger}</td>'
            f'<td class="desc">{desc}</td>'
            f'</tr>\n'
        )
    return f'''<table class="secondary-table">
  <thead><tr>
    <th>API Name</th><th>Label</th><th>Estado</th><th>Trigger</th><th>Descripción</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>'''


# ── Apex Triggers ────────────────────────────────────────────────────────────

def build_triggers_html(triggers):
    if not triggers:
        return '<p class="empty-section">No se encontraron Apex Triggers en el código fuente para este objeto.</p>'
    html = ''
    for t in triggers:
        name    = escape(t.get('name', ''))
        events  = escape(', '.join(t.get('events', [])))
        status  = t.get('status', 'unknown')
        handler = escape(t.get('handler', ''))
        note    = escape(t.get('note', ''))
        contexts = t.get('contexts', [])
        status_class = 'active-badge' if status == 'active' else 'inactive-badge'
        status_label = 'Activo' if status == 'active' else 'Desactivado'
        handler_html = f' <span class="trigger-handler">→ <code>{handler}</code></span>' if handler else ''
        html += (
            f'<div class="trigger-block">'
            f'<div class="trigger-header">'
            f'<code class="trigger-name">{name}</code>'
            f'<span class="{status_class}">{status_label}</span>'
            f'<span class="trigger-events">{events}</span>'
            f'{handler_html}'
            f'</div>'
        )
        if note:
            html += f'<p class="trigger-note">{note}</p>'
        if contexts:
            rows = ''
            for ctx in contexts:
                event = escape(ctx.get('event', ''))
                for m in ctx.get('methods', []):
                    rows += (
                        f'<tr>'
                        f'<td class="trigger-ctx">{event}</td>'
                        f'<td><code>{escape(m.get("name",""))}</code></td>'
                        f'<td class="desc">{escape(m.get("description",""))}</td>'
                        f'</tr>\n'
                    )
            html += f'''<table class="secondary-table">
  <thead><tr><th>Contexto</th><th>Método / Acción</th><th>Descripción</th></tr></thead>
  <tbody>{rows}</tbody>
</table>'''
        elif status == 'disabled':
            html += '<p class="trigger-note" style="margin:4px 16px 14px">Toda la lógica del trigger está comentada en el código fuente.</p>'
        html += '</div>'
    return html


# ── Index management ─────────────────────────────────────────────────────────

def update_index(output_dir, object_name, stats):
    manifest_path = os.path.join(output_dir, 'manifest.json')
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding='utf-8') as f:
            manifest = json.load(f)
    else:
        manifest = []

    manifest = [e for e in manifest if e['object'] != object_name]
    manifest.append({
        'object': object_name,
        'file': f'{object_name}.html',
        'fields': stats['total'],
        'custom': stats['custom'],
        'validations': stats['validations'],
        'flows': stats['flows'],
        'triggers': stats.get('triggers', 0),
        'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
    })
    manifest.sort(key=lambda e: e['object'])

    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    rows = ''
    for e in manifest:
        rows += (
            f'<tr>'
            f'<td><a href="{escape(e["file"])}">{escape(e["object"])}</a></td>'
            f'<td>{e["fields"]}</td>'
            f'<td>{e["custom"]}</td>'
            f'<td>{e.get("validations", 0)}</td>'
            f'<td>{e.get("flows", 0)}</td>'
            f'<td>{e.get("triggers", 0)}</td>'
            f'<td>{e["date"]}</td>'
            f'</tr>\n'
        )

    index_html = f'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Salesforce Docs — Índice</title>
<script>if(localStorage.getItem('sf-docs-theme')==='dark')document.documentElement.setAttribute('data-theme','dark');</script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{ --bg:#f4f6f9; --surface:#fff; --surface2:#f3f4f6; --border:#e0e0e0; --border-light:#f0f0f0; --text:#1a1a2e; --text-muted:#5a6474; --accent:#0070d2; --hover-bg:#f0f7ff; }}
  [data-theme="dark"] {{ --bg:#0d1117; --surface:#161b22; --surface2:#21262d; --border:#30363d; --border-light:#21262d; --text:#e6edf3; --text-muted:#8b949e; --accent:#58a6ff; --hover-bg:#1c2128; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 14px; background: var(--bg); color: var(--text); padding: 32px; }}
  .container {{ max-width: 900px; margin: 0 auto; }}
  header {{ background: linear-gradient(135deg, #0070d2, #1589ee); color: #fff; padding: 28px 32px; border-radius: 8px 8px 0 0; display:flex; align-items:center; justify-content:space-between; }}
  header h1 {{ font-size: 24px; font-weight: 700; margin-bottom: 6px; }}
  header p {{ opacity: 0.85; font-size: 13px; }}
  .dm-toggle {{ padding:6px 11px; border:1px solid rgba(255,255,255,0.4); border-radius:6px; background:rgba(255,255,255,0.15); color:#fff; cursor:pointer; font-size:15px; line-height:1; }}
  .dm-toggle:hover {{ background:rgba(255,255,255,0.25); }}
  table {{ width: 100%; border-collapse: collapse; background: var(--surface); box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-radius: 0 0 8px 8px; }}
  thead th {{ background: var(--surface2); padding: 10px 16px; text-align: left; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); border-bottom: 2px solid var(--border); }}
  tbody tr {{ border-bottom: 1px solid var(--border-light); transition: background 0.1s; }}
  tbody tr:hover {{ background: var(--hover-bg); }}
  td {{ padding: 12px 16px; }}
  a {{ color: var(--accent); font-weight: 600; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .num {{ color: var(--text-muted); font-size: 13px; }}
  .date {{ color: var(--text-muted); font-size: 12px; }}
</style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <h1>Salesforce Object Documentation</h1>
      <p>{len(manifest)} objeto{'s' if len(manifest) != 1 else ''} documentado{'s' if len(manifest) != 1 else ''}</p>
    </div>
    <button id="dm-toggle" class="dm-toggle" title="Cambiar tema">🌙</button>
  </header>
  <table>
    <thead>
      <tr><th>Objeto</th><th>Campos</th><th>Custom</th><th>Validaciones</th><th>Flows</th><th>Triggers</th><th>Generado</th></tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
</div>
<script>
(function(){{
  var btn=document.getElementById('dm-toggle'),root=document.documentElement;
  if(root.getAttribute('data-theme')==='dark')btn.textContent='☀️';
  btn.addEventListener('click',function(){{
    var dark=root.getAttribute('data-theme')==='dark';
    if(dark){{root.removeAttribute('data-theme');btn.textContent='🌙';localStorage.setItem('sf-docs-theme','light');}}
    else{{root.setAttribute('data-theme','dark');btn.textContent='☀️';localStorage.setItem('sf-docs-theme','dark');}}
  }});
}})();
</script>
</body>
</html>'''

    index_path = os.path.join(output_dir, 'index.html')
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(index_html)
    print(f"Índice actualizado: {index_path}")


# ── Main HTML generator ──────────────────────────────────────────────────────

def generate_html(object_name, org_alias, output_dir, explanations_file=None, triggers_file=None):
    print(f"Describing {object_name} on {org_alias}...")
    describe_data = run_sf_describe(object_name, org_alias)
    describe_result = describe_data['result']
    fields = describe_result['fields']
    object_label = describe_result.get('label', object_name)

    print("Getting field descriptions...")
    descriptions = get_field_descriptions(object_name, org_alias)

    print("Getting validation rules...")
    val_rules = get_validation_rules(object_name, org_alias)

    print(f"Getting record-triggered flows (label: '{object_label}')...")
    flows = get_record_triggered_flows(object_label, org_alias)
    trigger_order = {'RecordBeforeSave': 0, 'RecordAfterSave': 1, 'RecordBeforeDelete': 2}
    flows.sort(key=lambda f: trigger_order.get(f.get('TriggerType', ''), 99))

    total = len(fields)
    custom_count = sum(1 for f in fields if f['name'].endswith('__c'))
    standard_count = total - custom_count
    picklist_count = sum(1 for f in fields if f['type'] == 'picklist')
    formula_fields = [f for f in fields if f.get('calculated')]
    formula_count = len(formula_fields)
    required_count = sum(1 for f in fields if is_required(f))

    if explanations_file and os.path.exists(explanations_file):
        print(f"Loading formula explanations from {explanations_file}...")
        with open(explanations_file, encoding='utf-8-sig') as f:
            formula_explanations = json.load(f)
    else:
        print("Getting formula explanations...")
        formula_explanations = get_formula_explanations(formula_fields, object_name)

    if triggers_file and os.path.exists(triggers_file):
        print(f"Loading trigger summary from {triggers_file}...")
        with open(triggers_file, encoding='utf-8-sig') as f:
            apex_triggers = json.load(f)
    else:
        apex_triggers = []

    trigger_count = len([t for t in apex_triggers if t.get('status') == 'active'])

    sections = categorize_fields(fields)
    rows_html = build_field_rows(sections, descriptions, formula_explanations)
    val_html = build_validation_rules_html(val_rules)
    flows_html = build_flows_html(flows)
    triggers_html = build_triggers_html(apex_triggers)
    now = datetime.now().strftime('%Y-%m-%d %H:%M')

    html = f'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(object_name)} — Salesforce Docs</title>
<script>if(localStorage.getItem('sf-docs-theme')==='dark')document.documentElement.setAttribute('data-theme','dark');</script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg:#f4f6f9; --surface:#fff; --surface2:#f3f4f6;
    --border:#e0e0e0; --border-light:#f0f0f0;
    --text:#1a1a2e; --text-muted:#5a6474; --text-dim:#555;
    --accent:#0070d2; --hover-bg:#fafbff; --input-bg:#fff;
    --code-bg:#f0f4ff; --code-color:#3451b2; --code-hover:#dde8ff;
    --sec-bg:#e8f0fe; --sec-color:#1a56db; --sec-border:#c7d7fc;
    --fborder:#f5c800; --fhdr-bg:#fef9c3; --fhdr-color:#78500a;
    --fbody-bg:#fffdf0; --fcode-sep:#f5d000;
    --fbtn-bg:#fffbe6; --fbtn-color:#b07a00; --fbtn-border:#f5d000;
    --fbtn-hover:#fff3b0; --fbtn-open-bg:#f5d000; --fbtn-open-color:#5a3e00;
  }}
  [data-theme="dark"] {{
    --bg:#0d1117; --surface:#161b22; --surface2:#21262d;
    --border:#30363d; --border-light:#21262d;
    --text:#e6edf3; --text-muted:#8b949e; --text-dim:#8b949e;
    --accent:#58a6ff; --hover-bg:#1c2128; --input-bg:#21262d;
    --code-bg:#2d333b; --code-color:#79c0ff; --code-hover:#373e47;
    --sec-bg:#1c2d3e; --sec-color:#79c0ff; --sec-border:#1f3d5c;
    --fborder:#b08800; --fhdr-bg:#2d2200; --fhdr-color:#e6b800;
    --fbody-bg:#1e1a00; --fcode-sep:#8a6a00;
    --fbtn-bg:#2d2200; --fbtn-color:#e6b800; --fbtn-border:#8a6a00;
    --fbtn-hover:#3d3000; --fbtn-open-bg:#8a6a00; --fbtn-open-color:#ffe066;
  }}

  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    font-size: 13px;
    background: var(--bg);
    color: var(--text);
    padding: 24px;
  }}

  .container {{ max-width: 1500px; margin: 0 auto; }}

  a.back {{ display: inline-block; margin-bottom: 12px; color: #0070d2; text-decoration: none; font-size: 13px; }}
  a.back:hover {{ text-decoration: underline; }}

  header {{
    background: linear-gradient(135deg, #0070d2 0%, #1589ee 100%);
    color: #fff;
    padding: 24px 32px;
    border-radius: 8px 8px 0 0;
  }}

  header h1 {{ font-size: 26px; font-weight: 700; letter-spacing: -0.5px; margin-bottom: 12px; }}
  header h1 span {{ font-size: 14px; font-weight: 400; opacity: 0.75; margin-left: 10px; }}

  .meta {{ display: flex; flex-wrap: wrap; gap: 18px; font-size: 13px; opacity: 0.9; }}
  .badge {{ background: rgba(255,255,255,0.2); padding: 2px 9px; border-radius: 12px; font-weight: 600; }}

  /* ── Toolbar ── */
  .toolbar {{
    background: var(--surface);
    padding: 12px 16px;
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    border-bottom: 1px solid var(--border);
    border-left: 1px solid var(--border);
    border-right: 1px solid var(--border);
  }}
  .dm-toggle {{
    margin-left: 8px; padding: 5px 10px;
    border: 1px solid var(--border); border-radius: 6px;
    background: var(--surface); color: var(--text);
    cursor: pointer; font-size: 14px; line-height: 1;
    transition: background 0.15s;
  }}
  .dm-toggle:hover {{ background: var(--hover-bg); }}

  .search-wrap {{ position: relative; flex: 1; min-width: 200px; max-width: 380px; }}
  .search-wrap input {{
    width: 100%;
    padding: 7px 12px 7px 32px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 13px;
    background: var(--input-bg);
    color: var(--text);
    outline: none;
    transition: border-color 0.15s;
  }}
  .search-wrap input:focus {{ border-color: var(--accent); }}
  .search-wrap::before {{
    content: '🔍';
    position: absolute;
    left: 9px;
    top: 50%;
    transform: translateY(-50%);
    font-size: 12px;
    pointer-events: none;
  }}

  .filter-group {{ display: flex; gap: 6px; flex-wrap: wrap; }}

  .filter-btn {{
    padding: 5px 12px;
    border: 1px solid var(--border);
    border-radius: 20px;
    background: var(--surface);
    color: var(--text);
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s;
    white-space: nowrap;
  }}
  .filter-btn:hover {{ border-color: var(--accent); color: var(--accent); }}
  .filter-btn.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}

  .count-label {{ margin-left: auto; font-size: 12px; color: var(--text-muted); white-space: nowrap; }}
  .count-label strong {{ color: var(--text); }}

  /* ── Main table ── */
  table.main-table {{
    width: 100%;
    border-collapse: collapse;
    background: var(--surface);
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  }}

  table.main-table thead th {{
    background: var(--surface2);
    padding: 10px 14px;
    text-align: left;
    font-weight: 600;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-muted);
    border-bottom: 2px solid var(--border);
    position: sticky;
    top: 0;
    z-index: 10;
  }}

  tr.section-header td {{
    background: var(--sec-bg);
    color: var(--sec-color);
    font-weight: 700;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    padding: 7px 14px;
    border-top: 2px solid var(--sec-border);
    border-bottom: 1px solid var(--sec-border);
  }}

  tr.field-row td {{
    padding: 9px 14px;
    border-bottom: 1px solid var(--border-light);
    vertical-align: top;
  }}

  tr.field-row:hover td {{ background: var(--hover-bg); }}

  code.api-name {{
    font-family: 'SF Mono', 'Fira Code', Consolas, monospace;
    font-size: 12px;
    background: var(--code-bg);
    color: var(--code-color);
    padding: 2px 6px;
    border-radius: 4px;
    cursor: pointer;
    white-space: nowrap;
  }}
  code.api-name:hover {{ background: var(--code-hover); }}
  code.api-name.copied {{ background: #d4edda; color: #155724; }}

  .type-cell {{ max-width: 155px; }}

  .type-badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    white-space: nowrap;
    background: #f0f4f8;
    color: #4a5568;
  }}
  .type-picklist {{ background: #e6f7ff; color: #0070d2; }}
  .type-multipicklist {{ background: #e6f7ff; color: #0070d2; }}
  .type-reference {{ background: #fff0e6; color: #b35a00; }}
  .type-boolean {{ background: #e6ffe6; color: #1a7a1a; }}
  .type-datetime, .type-date {{ background: #f5e6ff; color: #7a00b3; }}
  .type-string, .type-textarea {{ background: #f0f4f8; color: #4a5568; }}
  .type-double, .type-int, .type-currency, .type-percent {{ background: #e6fff0; color: #007a4d; }}
  .type-id, .type-autonumber {{ background: #f0f0f0; color: #666; }}
  .type-calculated {{ background: #fffbe6; color: #b07a00; }}

  .req-badge {{
    display: inline-block;
    margin-left: 5px;
    padding: 1px 5px;
    background: #fff0f0;
    color: #c00;
    border: 1px solid #fcc;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 700;
    vertical-align: middle;
  }}

  .formula-toggle-btn {{
    margin-top: 5px;
    padding: 2px 8px;
    font-size: 11px;
    color: var(--fbtn-color);
    background: var(--fbtn-bg);
    border: 1px solid var(--fbtn-border);
    border-radius: 4px;
    cursor: pointer;
    white-space: nowrap;
  }}
  .formula-toggle-btn:hover {{ background: var(--fbtn-hover); }}
  .formula-toggle-btn.open {{ background: var(--fbtn-open-bg); color: var(--fbtn-open-color); }}

  .formula-expand-row td {{ padding: 0; border-bottom: 2px solid var(--fborder); }}
  .frow-header {{
    padding: 8px 16px;
    background: var(--fhdr-bg);
    font-size: 12px;
    font-weight: 700;
    color: var(--fhdr-color);
    border-bottom: 1px solid var(--fcode-sep);
  }}
  .frow-body {{
    display: grid;
    grid-template-columns: 1fr 1fr;
  }}
  .frow-code {{
    padding: 14px 16px;
    border-right: 1px solid var(--fcode-sep);
    background: var(--fbody-bg);
  }}
  .frow-code pre {{
    font-family: 'SF Mono', 'Fira Code', Consolas, monospace;
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-all;
    color: var(--text);
    margin: 0;
  }}
  .frow-expl {{
    padding: 14px 16px;
    background: var(--fbody-bg);
  }}
  .frow-expl-label {{
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--fhdr-color);
    margin-bottom: 8px;
  }}
  .frow-expl p {{
    font-size: 13px;
    color: var(--text-dim);
    line-height: 1.6;
  }}

  .desc {{ color: var(--text-dim); font-style: italic; min-width: 280px; max-width: 380px; }}

  ul.picklist-values {{ list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 3px; }}
  ul.picklist-values li {{ display: flex; align-items: baseline; gap: 6px; font-size: 12px; line-height: 1.4; }}
  ul.picklist-values .arrow {{ color: var(--text-muted); font-size: 11px; flex-shrink: 0; }}
  ul.picklist-values code {{ font-size: 11px; }}

  em.empty {{ color: var(--text-muted); font-size: 12px; }}

  /* ── Secondary sections ── */
  .section-block {{
    margin-top: 32px;
    background: var(--surface);
    border-radius: 8px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    overflow: hidden;
  }}

  .section-title {{
    background: linear-gradient(135deg, #0070d2 0%, #1589ee 100%);
    color: #fff;
    padding: 14px 20px;
    font-size: 15px;
    font-weight: 700;
  }}

  .section-title span {{ font-size: 12px; font-weight: 400; opacity: 0.75; margin-left: 8px; }}

  table.secondary-table {{
    width: 100%;
    border-collapse: collapse;
  }}

  table.secondary-table thead th {{
    background: var(--surface2);
    padding: 9px 14px;
    text-align: left;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
  }}

  table.secondary-table tbody tr {{ border-bottom: 1px solid var(--border-light); }}
  table.secondary-table tbody tr:hover td {{ background: var(--hover-bg); }}
  table.secondary-table td {{ padding: 9px 14px; vertical-align: top; font-size: 13px; }}

  .active-badge {{
    display: inline-block; padding: 2px 8px; border-radius: 12px;
    background: #d4edda; color: #155724; font-size: 11px; font-weight: 600;
  }}
  .inactive-badge {{
    display: inline-block; padding: 2px 8px; border-radius: 12px;
    background: #f8d7da; color: #721c24; font-size: 11px; font-weight: 600;
  }}

  .empty-section {{ padding: 16px 20px; color: #888; font-style: italic; }}

  /* ── Apex Triggers ── */
  .trigger-block {{ border-bottom: 1px solid var(--border); padding: 14px 20px; }}
  .trigger-block:last-child {{ border-bottom: none; }}
  .trigger-header {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }}
  .trigger-name {{
    font-family: 'SF Mono', 'Fira Code', Consolas, monospace;
    font-size: 13px; background: var(--code-bg); color: var(--code-color);
    padding: 2px 8px; border-radius: 4px;
  }}
  .trigger-events {{ font-size: 12px; color: var(--text-muted); }}
  .trigger-handler {{ font-size: 12px; color: var(--text-muted); }}
  .trigger-handler code {{
    font-family: 'SF Mono', 'Fira Code', Consolas, monospace;
    font-size: 12px; color: var(--code-color);
  }}
  .trigger-note {{ font-size: 12px; color: var(--text-muted); font-style: italic; margin: 2px 0 10px; }}
  .trigger-ctx {{ font-size: 12px; font-weight: 600; color: var(--sec-color); background: var(--sec-bg); white-space: nowrap; }}

  /* ── Responsive ── */
  @media (max-width: 900px) {{
    .desc {{ min-width: 160px; max-width: 220px; }}
  }}
</style>
</head>
<body>
<div class="container">
  <a class="back" href="index.html">← Volver al índice</a>

  <header>
    <h1>{escape(object_name)} <span>Salesforce Object</span></h1>
    <div class="meta">
      <div>Total <strong class="badge">{total}</strong></div>
      <div>Estándar <strong class="badge">{standard_count}</strong></div>
      <div>Custom <strong class="badge">{custom_count}</strong></div>
      <div>Picklists <strong class="badge">{picklist_count}</strong></div>
      <div>Fórmulas <strong class="badge">{formula_count}</strong></div>
      <div>Requeridos <strong class="badge">{required_count}</strong></div>
      <div>Org: <strong>{escape(org_alias)}</strong></div>
      <div>Generado: <strong>{now}</strong></div>
    </div>
  </header>

  <div class="toolbar">
    <div class="search-wrap">
      <input type="text" id="search" placeholder="Buscar campo…" autocomplete="off">
    </div>
    <div class="filter-group">
      <button class="filter-btn active" data-filter="all">Todos</button>
      <button class="filter-btn" data-filter="standard">Estándar</button>
      <button class="filter-btn" data-filter="custom">Custom</button>
      <button class="filter-btn" data-filter="picklist">Picklist</button>
      <button class="filter-btn" data-filter="formula">Fórmula</button>
      <button class="filter-btn" data-filter="required">Requerido</button>
    </div>
    <span class="count-label">Mostrando <strong id="visible-count">{total}</strong> de {total}</span>
    <button id="dm-toggle" class="dm-toggle" title="Cambiar tema">🌙</button>
  </div>

  <table class="main-table">
    <thead>
      <tr>
        <th style="width:200px">API Name</th>
        <th style="width:170px">Label</th>
        <th style="width:170px">Tipo</th>
        <th style="min-width:300px">Descripción</th>
        <th>Valores Picklist (API → Label)</th>
      </tr>
    </thead>
    <tbody id="fields-tbody">
{rows_html}
    </tbody>
  </table>

  <!-- Validation Rules -->
  <div class="section-block">
    <div class="section-title">Reglas de validación <span>{len(val_rules)} regla{'s' if len(val_rules) != 1 else ''}</span></div>
    {val_html}
  </div>

  <!-- Record-Triggered Flows -->
  <div class="section-block">
    <div class="section-title">Record-Triggered Flows <span>{len(flows)} flow{'s' if len(flows) != 1 else ''}</span></div>
    {flows_html}
  </div>

  <!-- Apex Triggers -->
  <div class="section-block">
    <div class="section-title">Apex Triggers <span>{len(apex_triggers)} trigger{'s' if len(apex_triggers) != 1 else ''}</span></div>
    {triggers_html}
  </div>

</div>

<script>
(function() {{
  const searchInput = document.getElementById('search');
  const filterBtns = document.querySelectorAll('.filter-btn');
  const rows = document.querySelectorAll('.field-row');
  const visibleCount = document.getElementById('visible-count');
  let activeFilter = 'all';

  function applyFilters() {{
    const q = searchInput.value.toLowerCase().trim();
    let count = 0;

    rows.forEach(row => {{
      const text = row.textContent.toLowerCase();
      const tags = row.dataset.tags || '';
      const matchesSearch = !q || text.includes(q);
      const matchesFilter = activeFilter === 'all' || tags.split(' ').includes(activeFilter);
      const visible = matchesSearch && matchesFilter;
      row.style.display = visible ? '' : 'none';
      if (visible) count++;
    }});

    // Show/hide section headers
    document.querySelectorAll('tr.section-header').forEach(header => {{
      let next = header.nextElementSibling;
      let hasVisible = false;
      while (next && !next.classList.contains('section-header')) {{
        if (next.style.display !== 'none') {{ hasVisible = true; break; }}
        next = next.nextElementSibling;
      }}
      header.style.display = hasVisible ? '' : 'none';
    }});

    visibleCount.textContent = count;
  }}

  searchInput.addEventListener('input', applyFilters);

  filterBtns.forEach(btn => {{
    btn.addEventListener('click', () => {{
      filterBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeFilter = btn.dataset.filter;
      applyFilters();
    }});
  }});

  // Copy API name to clipboard on click
  document.querySelectorAll('code.api-name').forEach(el => {{
    el.addEventListener('click', () => {{
      navigator.clipboard.writeText(el.textContent).then(() => {{
        el.classList.add('copied');
        setTimeout(() => el.classList.remove('copied'), 1200);
      }});
    }});
  }});

  // Toggle formula expand rows
  document.querySelectorAll('.formula-toggle-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      const row = document.getElementById(btn.dataset.row);
      if (!row) return;
      const open = row.style.display !== 'none';
      row.style.display = open ? 'none' : '';
      btn.textContent = open ? '▶ ver fórmula' : '▼ ocultar fórmula';
      btn.classList.toggle('open', !open);
    }});
  }});

  // Dark mode toggle
  const dmToggle = document.getElementById('dm-toggle');
  const root = document.documentElement;
  if (root.getAttribute('data-theme') === 'dark') dmToggle.textContent = '☀️';
  dmToggle.addEventListener('click', () => {{
    const isDark = root.getAttribute('data-theme') === 'dark';
    if (isDark) {{
      root.removeAttribute('data-theme');
      dmToggle.textContent = '🌙';
      localStorage.setItem('sf-docs-theme', 'light');
    }} else {{
      root.setAttribute('data-theme', 'dark');
      dmToggle.textContent = '☀️';
      localStorage.setItem('sf-docs-theme', 'dark');
    }}
  }});
}})();
</script>
</body>
</html>'''

    output_path = os.path.join(output_dir, f'{object_name}.html')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Generado: {output_path}")

    update_index(output_dir, object_name, {
        'total': total,
        'custom': custom_count,
        'validations': len(val_rules),
        'flows': len(flows),
        'triggers': trigger_count,
    })

    return output_path


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Uso: python sf_doc_generator.py <ObjectApiName> <OrgAlias> [explanations.json]")
        sys.exit(1)

    obj_name = sys.argv[1]
    org = sys.argv[2]
    explanations_file = sys.argv[3] if len(sys.argv) > 3 else None
    triggers_file = sys.argv[4] if len(sys.argv) > 4 else None
    out = os.path.join(os.getcwd(), 'documentator')
    os.makedirs(out, exist_ok=True)

    generate_html(obj_name, org, out, explanations_file=explanations_file, triggers_file=triggers_file)
