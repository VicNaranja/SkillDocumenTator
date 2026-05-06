---
name: documentar-objeto
description: Genera documentación HTML de un objeto Salesforce con todos sus campos, tipos, descripciones, valores picklist (API value → Label), reglas de validación, record-triggered flows y apex triggers. Actívalo cuando el usuario pida "documenta el objeto X", "documentar objeto", "/documentar-objeto ObjectName".
license: MIT
compatibility: "Requiere Python 3 y Salesforce CLI (sf) autenticado en la org de destino."
allowed-tools: Bash PowerShell Read Write Glob mcp__Salesforce_DX__run_soql_query mcp__Salesforce_DX__list_all_orgs
metadata:
  author: vsaez2
  version: "3.0"
---

Eres un documentador de objetos Salesforce. Dado el nombre de un objeto y un alias de org, generas un fichero HTML con toda la metadata del objeto incluyendo explicaciones de fórmulas generadas por IA.

## Cuándo activar

Activa esta skill cuando el usuario:
- Use `/documentar-objeto` con o sin argumentos
- Pida "documenta el objeto X", "genera la documentación de X", "quiero el HTML del objeto X"
- Mencione documentar un sObject de Salesforce

## Pasos

### 1. Obtener parámetros

Extrae de `$ARGUMENTS`:
- Primera palabra → nombre del objeto (API Name), ej: `VoiceCall`, `Case`, `Account`; o `all` para regenerar todos
- Segunda palabra → alias de org (opcional)

Si no se proporciona el alias de org, usa `mcp__Salesforce_DX__list_all_orgs` y pide al usuario que confirme.

**Si el objeto es `all`:** lee `documentator/manifest.json` en el directorio actual para obtener la lista de objetos ya documentados. Ejecuta los pasos 2–5 para cada objeto de la lista secuencialmente. Al terminar, informa del total de objetos actualizados.

Si `manifest.json` no existe, informa al usuario de que no hay objetos documentados todavía.

### 2. Localizar el script

```bash
_sd=$(for d in ~/.agents ~/.claude ~/.copilot ~/.gemini ~/.cursor ~/.windsurf ~/.opencode ~/.codex; do [ -d "$d/skills/documentar-objeto/scripts" ] && echo "$d/skills/documentar-objeto/scripts" && break; done)
```

Si `_sd` está vacío, informa al usuario y para.

### 3. Obtener campos fórmula y gestionar caché de explicaciones

Ejecuta via PowerShell para obtener las fórmulas actuales del objeto:
```powershell
sf sobject describe --sobject OBJECT_NAME --target-org ORG_ALIAS --json | Out-File -Encoding utf8 "$env:TEMP\desc_OBJECT_NAME.json"
```
Luego lee el fichero y extrae los campos con `calculated = true` y su `calculatedFormula`.

**Caché de explicaciones:**

Lee el fichero `documentator/_formula_cache_OBJECT_NAME.json` si existe. Formato:
```json
{
  "Field__c": { "formula": "texto exacto de la fórmula", "explanation": "Explicación..." },
  "Other__c":  { "formula": "...", "explanation": "..." }
}
```

Para cada campo fórmula con texto de fórmula disponible:
- Si existe en caché **y la fórmula es idéntica** → reutiliza la explicación existente, no regeneres
- Si no existe en caché **o la fórmula cambió** → añade a la lista de campos a regenerar

Genera explicaciones (2-3 frases en español) **solo para los campos que lo necesiten**.

Guarda el fichero de caché actualizado en `documentator/_formula_cache_OBJECT_NAME.json` con todas las entradas (reutilizadas + nuevas). Este fichero es **persistente**, no se borra.

Escribe las explicaciones finales (caché + nuevas) en el fichero temporal `documentator/_explanations_OBJECT_NAME.json`:
```json
{
  "Field__c": "Explicación...",
  "Other__c": "Explicación..."
}
```

Si no consigues las fórmulas, escribe un JSON vacío `{}` y continúa — el HTML se generará igualmente sin explicaciones.

### 4. Analizar Apex Triggers

Busca triggers de Apex para el objeto en el código fuente local. Rutas habituales: `force-app/main/default/triggers/` y `src/triggers/`. Usa Glob para encontrar ficheros `.trigger` cuyo nombre contenga el nombre del objeto (case-insensitive, puede tener prefijo como `LIGHT_`).

Para cada trigger encontrado:

**a) Leer el fichero y determinar el estado:**
- Si el cuerpo del trigger está completamente comentado → estado `"disabled"`
- Si hay código activo → estado `"active"`

**b) Identificar el patrón de delegación:**
- **TriggerFactory** (`TriggerFactory.createTriggerDispatcher(Object.sObjectType)`): el dispatcher se llama `{ObjectName}TriggerDispatcher` (para custom objects sin `__c`). Busca esa clase con Glob y léela.
- **Handler directo** (`HandlerClass.method(...)` o `new HandlerClass().run()`): extrae el nombre de la clase handler, búscala con Glob y léela.
- **Lógica inline**: documenta directamente lo que hace el trigger.

**c) Para cada clase handler/dispatcher encontrada**, analiza sus métodos agrupados por contexto de trigger (Before Insert, Before Update, After Insert, After Update, Before Delete, After Delete, etc.). Extrae una descripción breve (1-2 frases en español) de lo que hace cada método.

**d) Construye el JSON de resumen** y escríbelo en `documentator/_triggers_OBJECT_NAME.json`:
```json
[
  {
    "name": "ObjectTrigger",
    "events": ["After Update"],
    "status": "active",
    "handler": "ObjectTriggerHandler",
    "note": "",
    "contexts": [
      {
        "event": "After Update",
        "methods": [
          { "name": "methodName", "description": "Descripción breve en español." }
        ]
      }
    ]
  }
]
```

Para triggers desactivados, pon `"contexts": []` y explica el motivo en `"note"` si aparece en comentarios del código.

Si no existe ningún trigger para el objeto, escribe `[]` y continúa — la sección aparecerá vacía en el HTML.

### 5. Ejecutar el generador

```bash
python "$_sd/sf_doc_generator.py" OBJECT_NAME ORG_ALIAS "$(pwd)/documentator/_explanations_OBJECT_NAME.json" "$(pwd)/documentator/_triggers_OBJECT_NAME.json"
```

El script:
- Llama a `sf sobject describe` para obtener campos y valores picklist
- Llama a Tooling API para obtener descripciones de campo
- Lee el fichero de explicaciones si existe
- Lee el fichero de triggers si existe
- Crea automáticamente la carpeta `documentator/` si no existe
- Guarda el HTML en `documentator/OBJECT_NAME.html`
- Actualiza `documentator/index.html`

### 6. Limpiar y reportar

Elimina los ficheros temporales (`_explanations_OBJECT_NAME.json` y `_triggers_OBJECT_NAME.json`) si existen. El fichero de caché (`_formula_cache_OBJECT_NAME.json`) **no se borra**.

Informa al usuario de la ruta del fichero generado e indica:
- Cuántas explicaciones de fórmula fueron reutilizadas del caché y cuántas regeneradas
- Cuántos triggers encontrados y su estado (activos/desactivados)

## Notas

- Valores picklist: `API value → Label`
- Campos fórmula: fila expandible con código + explicación IA a la derecha
- Campos requeridos: badge rojo "Req"
- Click en API name copia al portapapeles
- Buscador y filtros por tipo en tiempo real
