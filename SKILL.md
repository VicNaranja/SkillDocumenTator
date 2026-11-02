---
name: documentar-objeto
description: Genera documentación HTML de un objeto Salesforce con todos sus campos, tipos, descripciones, valores picklist (API value → Label), reglas de validación, record-triggered flows y apex triggers. Actívalo cuando el usuario pida "documenta el objeto X", "documentar objeto", "/documentar-objeto ObjectName".
license: MIT
compatibility: "Requiere Python 3 y Salesforce CLI (sf) autenticado en la org de destino."
allowed-tools: Bash PowerShell Read Write Glob Grep
metadata:
  author: vsaez2
  version: "4.0"
---

Eres un documentador de objetos Salesforce. Dado el nombre de un objeto y un alias de org, generas un fichero HTML con toda la metadata del objeto incluyendo explicaciones de fórmulas y descripciones de flows generadas por IA, más una sección de observaciones a revisar.

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

Si no se proporciona alias, lista los orgs disponibles y pide al usuario que confirme:

```bash
sf org list --json
```

Pasa **siempre** el alias explícito con `--target-org` en todos los comandos. No dependas del `target-org` configurado: cambia al autenticarse y puede apuntar a un org que no es el que el usuario espera.

**Si el objeto es `all`:** lee `documentator/manifest.json` en el directorio actual para obtener la lista de objetos ya documentados y ejecuta los pasos 2–7 para cada uno secuencialmente. Si `manifest.json` no existe, informa de que no hay objetos documentados todavía.

### 2. Localizar el script

Búscalo con Glob, patrón `**/documentar-objeto/scripts/sf_doc_generator.py`.

Usa la ruta absoluta resultante **literalmente** en el paso 7. No la guardes en una variable de shell: cada llamada a Bash/PowerShell es un proceso nuevo y las variables no sobreviven entre llamadas.

Si Glob no devuelve nada, informa al usuario y para.

### 3. Campos fórmula y caché de explicaciones

Obtén el describe del objeto:

```bash
sf sobject describe --sobject OBJECT_NAME --target-org ORG_ALIAS --json
```

Extrae los campos con `calculated = true` y su `calculatedFormula`.

Lee `documentator/OBJECT_NAME/_formula_cache.json` si existe:

```json
{ "Field__c": { "formula": "texto exacto de la fórmula", "explanation": "..." } }
```

Para cada campo fórmula:
- Si está en caché **y la fórmula es idéntica** → reutiliza la explicación
- Si no está o la fórmula cambió → genera explicación nueva (2-3 frases en español, técnica y concisa)

Guarda el caché actualizado en `documentator/OBJECT_NAME/_formula_cache.json` (**persistente**, no se borra) y escribe las explicaciones finales en `documentator/_explanations_OBJECT_NAME.json`:

```json
{ "Field__c": "Explicación..." }
```

Si no consigues las fórmulas, escribe `{}` y continúa.

### 4. Descripciones de flows y caché por versión

Consulta los record-triggered flows del objeto:

```bash
sf data query --query "SELECT ApiName, Label, TriggerType, IsActive, Description, VersionNumber FROM FlowDefinitionView WHERE TriggerObjectOrEventLabel = 'OBJECT_LABEL' AND TriggerType IN ('RecordBeforeSave','RecordAfterSave','RecordBeforeDelete')" --target-org ORG_ALIAS --json
```

Ojo: el filtro es por **label** del objeto (`Voice Call`), no por API name (`VoiceCall`).

Solo hay que generar descripción para los flows con `Description` **vacía** — la que ya escribió el equipo siempre manda.

Lee el caché `documentator/OBJECT_NAME/_flow_cache.json` si existe:

```json
{ "Flow_Api_Name": { "version": 2, "description": "..." } }
```

Para cada flow sin descripción propia:
- Si está en caché **y `version` coincide con `VersionNumber`** → reutiliza, no vuelvas a descargar el flow
- Si no está o la versión cambió → descarga el XML y genera la descripción

`VersionNumber` es la clave correcta de invalidación: Flow Builder crea una versión nueva en cada guardado. `LastModifiedDate` no sirve, porque también se mueve al activar o desactivar sin tocar la lógica.

Para descargar los flows sin ensuciar `force-app/`, escribe un manifiesto temporal con los que necesites y usa `--target-metadata-dir`:

```bash
sf project retrieve start --manifest <tmp>.xml --target-org ORG_ALIAS --target-metadata-dir .tmp_flows --unzip
```

Los `.flow` quedan en `.tmp_flows/unpackaged/unpackaged/flows/`. Borra `.tmp_flows` y el manifiesto al terminar.

Tres gotchas comprobados:

- **`--output-dir` no sirve.** Devuelve `success: true` con cero ficheros y sin error, aunque los flows existan (`sf org list metadata --metadata-type Flow` los lista). Solo funciona `--target-metadata-dir`.
- **La ruta debe estar dentro del proyecto.** Un directorio fuera del raíz falla con `OutputDirOutsideProjectError`.
- **El retrieve baja la última versión, no la activa.** Si un flow tiene un borrador más nuevo que la versión en producción, describirías el borrador.

Para lo tercero, incluye `ActiveVersionId` y `LatestVersionId` en la consulta del paso anterior. Cuando difieren, hay un borrador sin publicar y el XML descargado **no** es lo que se ejecuta. En ese caso pide la versión activa por Tooling API, que devuelve la definición completa en el campo `Metadata`:

```bash
sf api request rest "/services/data/v63.0/tooling/sobjects/Flow/<ActiveVersionId>" --target-org ORG_ALIAS
```

Describe siempre la versión activa, y anota la existencia del borrador en la sección Revisar: un borrador pendiente es justo el tipo de cosa que conviene que el equipo vea.

Lee el `.flow-meta.xml` resultante y resume en 2-3 frases en español: qué dispara el flow, sobre qué condiciones y qué hace. Céntrate en el efecto de negocio, no en enumerar nodos.

Guarda el caché actualizado y escribe las descripciones finales en `documentator/_flow_descriptions_OBJECT_NAME.json`:

```json
{ "Flow_Api_Name": "Descripción..." }
```

Estas descripciones salen marcadas con la etiqueta `IA` en el HTML. Si no consigues descargar algún flow, omítelo del JSON y continúa: aparecerá como "Sin descripción".

### 5. Analizar Apex Triggers

Busca triggers del objeto en el código fuente local. Rutas habituales: `force-app/main/default/triggers/` y `src/triggers/`. Usa Glob para encontrar ficheros `.trigger` cuyo nombre contenga el nombre del objeto (case-insensitive, puede llevar prefijo como `LIGHT_`).

Para cada trigger:

**a) Determinar el estado:** cuerpo completamente comentado → `"disabled"`; con código activo → `"active"`.

**b) Identificar el patrón de delegación:**
- **TriggerFactory** (`TriggerFactory.createTriggerDispatcher(Object.sObjectType)`): el dispatcher se llama `{ObjectName}TriggerDispatcher`. Búscalo con Glob y léelo.
- **Handler directo** (`HandlerClass.method(...)` o `new HandlerClass().run()`): extrae el nombre de la clase, búscala con Glob y léela.
- **Lógica inline**: documenta directamente lo que hace el trigger.

**c) Para cada handler/dispatcher**, analiza sus métodos agrupados por contexto (Before Insert, Before Update, After Insert, etc.) y extrae una descripción breve (1-2 frases en español) de cada uno.

**d) Escribe el resumen** en `documentator/_triggers_OBJECT_NAME.json`:

```json
[
  {
    "name": "ObjectTrigger",
    "events": ["After Update"],
    "status": "active",
    "handler": "ObjectTriggerHandler",
    "note": "",
    "contexts": [
      { "event": "After Update",
        "methods": [ { "name": "methodName", "description": "..." } ] }
    ]
  }
]
```

Para triggers desactivados, `"contexts": []` y el motivo en `"note"` si aparece en comentarios. Si no hay ningún trigger, escribe `[]` y continúa.

### 6. Observaciones a revisar

Con todo lo recopilado en los pasos 3-5, anota lo que llame la atención **del objeto completo**, no solo de los triggers. Escríbelo en `documentator/_review_OBJECT_NAME.json`:

```json
[
  { "severity": "warning",
    "area": "Orquestación",
    "target": "",
    "finding": "Qué has observado, concreto y verificable.",
    "impact": "Por qué importa." }
]
```

`severity`: `error` (algo roto), `warning` (probable problema), `info` (conviene saberlo). `area` es libre: `Orquestación`, `Apex Trigger`, `Flow`, `Campo`, `Validación`. `target` es opcional (nombre de API del elemento).

Qué buscar:

- **Solapamiento flow/trigger**: varios record-triggered flows del mismo `TriggerType` sobre el objeto, o flows conviviendo con Apex triggers. El orden entre flows del mismo tipo no está garantizado por Salesforce.
- **Automatización muerta**: flows inactivos que siguen desplegados, triggers comentados.
- **Trigger mal enrutado**: métodos de contexto invocados sin distinguir `Trigger.isInsert` de `Trigger.isUpdate`, lógica duplicada, SOQL o DML dentro de bucles.
- **Fórmulas problemáticas**: referencias a `$User` (no filtrables en informes ni usables en reglas de flujo), fórmulas que cruzan muchos objetos.
- **Metadata sin documentar**: proporción alta de campos custom sin `Description`.

Si no hay nada reseñable, escribe `[]`. **No rellenes por rellenar**: una sección con tres observaciones reales vale más que quince triviales, y el objetivo es que el equipo se la lea.

Estas notas salen en el HTML bajo un aviso de que son análisis automático no verificado. Mantén ese contrato: describe lo que observas, no afirmes causas que no has comprobado.

### 7. Ejecutar el generador

Con la ruta absoluta del paso 2, en una sola llamada:

```bash
python "<RUTA_SCRIPT>" OBJECT_NAME ORG_ALIAS \
  "<CWD>/documentator/_explanations_OBJECT_NAME.json" \
  "<CWD>/documentator/_triggers_OBJECT_NAME.json" \
  "<CWD>/documentator/_flow_descriptions_OBJECT_NAME.json" \
  "<CWD>/documentator/_review_OBJECT_NAME.json"
```

Los cuatro ficheros son posicionales y opcionales, pero **no se pueden saltar**: si quieres pasar el de review, tienes que pasar los cuatro. Si alguno no aplica, escribe `{}` o `[]` en él.

Invoca el script con el literal `python` al principio del comando, no a través de una variable de shell: las reglas de permisos hacen match por prefijo, así que `python "…"` queda cubierto por un `Bash(python *)` del allowlist mientras que `& $py "…"` no.

En Windows, si `python` no responde, prueba `py` o la ruta directa a `python.exe`: el alias de ejecución de la Microsoft Store puede estar interceptando el comando.

El script llama a `sf sobject describe` y a la Tooling API por su cuenta, crea el subdirectorio del objeto si no existe, guarda `documentator/OBJECT_NAME/OBJECT_NAME.html` y actualiza `documentator/index.html` y `manifest.json`.

### 8. Limpiar y reportar

Estructura resultante — un subdirectorio por objeto, para que la carpeta no se llene de ficheros sueltos al documentar muchos:

```
documentator/
├── index.html              ← índice de todos los objetos
├── manifest.json
├── VoiceCall/
│   ├── VoiceCall.html
│   ├── _formula_cache.json   ← persistente
│   └── _flow_cache.json      ← persistente
└── Case/
    └── ...
```

Los cuatro temporales (`_explanations_`, `_triggers_`, `_flow_descriptions_`, `_review_`) se escriben en la raíz de `documentator/` y **se borran** al terminar. Los dos cachés viven dentro del subdirectorio del objeto y **no se borran**: son lo que evita repetir el trabajo en la siguiente ejecución.

Informa de la ruta del fichero generado e indica:
- Explicaciones de fórmula reutilizadas del caché vs regeneradas
- Descripciones de flow reutilizadas vs regeneradas, y cuántos flows tenían descripción propia
- Triggers encontrados y su estado
- Número de observaciones en la sección Revisar, por severidad

## Notas

- Valores picklist: `API value → Label`
- Campos fórmula: fila expandible con código + explicación IA a la derecha
- Campos requeridos: badge rojo "Req"
- Click en API name copia al portapapeles
- Buscador y filtros por tipo en tiempo real
- La sección Revisar va la última, después de Apex Triggers
