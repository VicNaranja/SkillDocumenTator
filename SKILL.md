---
name: documentar-objeto
description: Genera documentación HTML de un objeto Salesforce con todos sus campos, tipos, descripciones, valores picklist (API value → Label), reglas de validación, record-triggered flows y apex triggers, con un botón para actualizarla desde el org elegido; y abre las páginas en vivo de usuarios, licencias y flows del org. Actívalo cuando el usuario pida "documenta el objeto X", "documentar objeto", "/documentar-objeto ObjectName", "documentar userList", "documentar userLicence" o "documentar flows".
license: MIT
compatibility: "Requiere Python 3 y Salesforce CLI (sf) autenticado en la org de destino."
allowed-tools: Bash PowerShell Read Write Glob Grep
metadata:
  author: vsaez2
  version: "5.0"
---

Eres un documentador de objetos Salesforce. Dado el nombre de un objeto y un alias de org, generas un fichero HTML con toda la metadata del objeto incluyendo explicaciones de fórmulas y descripciones de flows generadas por IA, más una sección de observaciones a revisar.

## Cuándo activar

Activa esta skill cuando el usuario:
- Use `/documentar-objeto` con o sin argumentos
- Pida "documenta el objeto X", "genera la documentación de X", "quiero el HTML del objeto X"
- Mencione documentar un sObject de Salesforce
- Pida `documentar userList`, `documentar userLicence` o `documentar flows`: son las páginas en vivo (ver «Páginas en vivo»), no un objeto

## Pasos

### 1. Obtener parámetros

Extrae de `$ARGUMENTS`:
- Primera palabra → nombre del objeto (API Name), ej: `VoiceCall`, `Case`, `Account`; o `all` para regenerar todos; o `userList`, `userLicence` o `flows`, que no son objetos y van por «Páginas en vivo» (no sigas los pasos 2–8)
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

Obtén las fórmulas del objeto con el generador (ruta del paso 2), que devuelve un JSON `{ "label": "Etiqueta", "formulas": { "Campo__c": "fórmula" } }` con solo los campos `calculated = true`; la `label` es la que pide el paso 4:

```bash
python "<RUTA_SCRIPT>" --formulas OBJECT_NAME ORG_ALIAS
```

No uses el `sf sobject describe` entero: en un objeto grande no cabe en la salida de la herramienta, llega truncado y no se pueden comparar las fórmulas con el caché.

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

Para descargar los flows sin ensuciar `force-app/`, escribe un manifiesto temporal con los que necesites en `documentator/_tmp_flows.xml` y usa `--target-metadata-dir`:

```bash
sf project retrieve start --manifest documentator/_tmp_flows.xml --target-org ORG_ALIAS --target-metadata-dir documentator/.tmp_flows --unzip
```

Los `.flow` quedan en `documentator/.tmp_flows/unpackaged/unpackaged/flows/`. No los borres tú: los borra el generador al terminar (paso 7), junto con el manifiesto.

Escribe estas rutas así, relativas y con `/`: son las que permite el modo no interactivo (ver «Modo no interactivo»), y cualquier otra se deniega.

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

Los cuatro temporales (`_explanations_`, `_triggers_`, `_flow_descriptions_`, `_review_`) y `_tmp_flows.xml` se escriben en la raíz de `documentator/`, y **los borra el propio generador** al terminar, igual que `documentator/.tmp_flows`: no hay que borrarlos a mano. Los dos cachés viven dentro del subdirectorio del objeto y **no se borran**: son lo que evita repetir el trabajo en la siguiente ejecución.

Informa de la ruta del fichero generado e indica:
- Explicaciones de fórmula reutilizadas del caché vs regeneradas
- Descripciones de flow reutilizadas vs regeneradas, y cuántos flows tenían descripción propia
- Triggers encontrados y su estado
- Número de observaciones en la sección Revisar, por severidad

Y que la página se abre con el servidor (ver «Servidor y botón Actualizar»): abierta desde el disco se ve igual, pero el botón no funciona.

## Servidor y botón Actualizar

`scripts/servir_documentator.py` sirve `documentator/` en `http://127.0.0.1:8788/`, junto con las páginas en vivo. Se arranca desde la raíz del proyecto:

```bash
python "<RUTA_SCRIPTS>/servir_documentator.py" --org ORG_ALIAS --abrir <ruta>
```

`--abrir` es la ruta que abre en el navegador (`VoiceCall/VoiceCall.html`, `vivo/usuarios`…); sin ella, el índice. Antes de arrancarlo, mira si ya está levantado pidiendo `http://127.0.0.1:8788/api/config`: si responde, no lances otro, abre la URL en el navegador. Si no, lánzalo **en segundo plano**, porque no termina hasta Ctrl+C.

Cada página de objeto tiene en la cabecera un selector de org y el botón **Actualizar**. El selector solo dice de qué org sale la siguiente actualización; la página que se ve es siempre la última generada, con su org y su fecha en la cabecera. Al pulsar, el servidor lanza Claude Code sin interfaz (`claude -p`) con esta skill para ese objeto y ese org, y la página se recarga cuando termina. Las actualizaciones van de una en una, porque todas reescriben el índice y el manifiesto: las que se piden mientras otra corre esperan en cola.

## Modo no interactivo

Cuando la sesión la lanza el botón, el prompt lo dice («Actualización automática de documentación…») y trae el objeto y el org. En ese modo:

- No preguntes nada ni pidas confirmación: no hay nadie para contestar. El alias viene dado; no listes orgs.
- La sesión va en modo `dontAsk` con una lista cerrada de herramientas (`HERRAMIENTAS` en `servir_documentator.py`): leer el repo; escribir solo en `documentator/`; `sf sobject describe`, `sf data query`, `sf api request rest` sin `--method`, `sf project retrieve start` a `documentator/.tmp_flows`; y el generador con `python`, que borra él mismo los temporales. Lo demás se deniega sin preguntar, así que usa exactamente los comandos y las rutas de esta skill.
- Si un paso falla, sigue con lo que tengas, como en el modo normal (fórmulas o flows sin explicación). Si no puedes ejecutar el generador, para.
- Termina con una frase: qué se ha actualizado o por qué ha fallado. Es lo que la página enseña.

## Páginas en vivo

`documentar userList`, `documentar userLicence` y `documentar flows` no generan ningún fichero: abren una página del servidor que consulta el org en cada carga. El selector de org ofrece los alias que la CLI tiene autenticados, recuerda el último elegido y marca `PRODUCCIÓN` cuando el org no es un sandbox (lo pregunta al org, no lo deduce del alias). No se guardan en disco porque muestran datos de usuarios del org, y un fichero con ellos acabaría versionado.

| Petición | Página | Qué tiene |
|---|---|---|
| `userList` | `vivo/usuarios` | Gestión de usuarios: listado de usuarios con filtros y columnas, asignar y quitar permisos, visor de permission set groups y visor de permission sets y perfiles |
| `userLicence` | `vivo/licencias` | Estado de las licencias del org: lo que mide cada gráfico lo explica la propia página |
| `flows` | `vivo/flows` | Visor de todos los flows del org, con filtros, buscador y ordenación |

Para cualquiera de las tres: arranca el servidor (o reutiliza el que ya esté) con `--org ORG_ALIAS --abrir vivo/<página>` e informa de la URL. Si no se da alias, el que se haya usado en la conversación; si no hay, pregunta.

**La gestión de usuarios escribe en el org**: asignar y quitar permisos, por POST y con confirmación en la página, que en producción exige escribir el alias. Es la única escritura del documentator.

Cada página es una entrada de `PAGINAS` en `scripts/secciones/__init__.py`, con sus módulos de sección (`recoger(org)` y, si lista algo bajo demanda, `detalle(org, cual)`), y su render en `scripts/plantilla/vivo.js`.

## Notas

- Valores picklist: `API value → Label`
- Campos fórmula: fila expandible con código + explicación IA a la derecha
- Campos requeridos: badge rojo "Req"
- Click en API name copia al portapapeles
- Buscador y filtros por tipo en tiempo real
- La sección Revisar va la última, después de Apex Triggers
