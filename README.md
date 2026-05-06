# documentar-objeto

Skill para Claude Code que genera documentación HTML interactiva de cualquier objeto Salesforce — campos, valores picklist, explicaciones de fórmulas, reglas de validación, flows y triggers Apex — con un único comando.

## Características

- **Catálogo completo de campos** — estándar y custom, con badges de tipo, marcadores de campo requerido y valores picklist (`valor API → Label`)
- **Explicaciones de fórmulas** — panel expandible por campo fórmula con el código y una explicación generada por IA
- **Reglas de validación** — nombre, estado, descripción y mensaje de error
- **Record-Triggered Flows** — tipo de trigger, estado activo/inactivo y descripción
- **Apex Triggers** — detecta el patrón TriggerFactory y handlers directos; lista cada método por contexto (Before Insert, After Update, etc.)
- **Modo oscuro** — botón de toggle persistido en `localStorage`
- **Búsqueda y filtros** — búsqueda en tiempo real por API name o label; filtros por tipo (estándar, custom, picklist, fórmula, requerido)
- **Caché de fórmulas** — las explicaciones se cachean por campo+fórmula; solo se regeneran cuando la fórmula cambia
- **Página de índice** — `index.html` actualizado automáticamente tras cada ejecución con estadísticas del objeto

## Requisitos

| Herramienta | Versión |
|-------------|---------|
| Python | 3.8 + |
| Salesforce CLI (`sf`) | 2.x |
| Claude Code | cualquiera |

El skill usa el [SDK de Anthropic](https://pypi.org/project/anthropic/) para generar las explicaciones de fórmulas. Instálalo una vez:

```bash
pip install anthropic
```

El CLI debe estar autenticado contra la org de destino:

```bash
sf org login web --alias mi-sandbox
```

## Instalación

Copia la carpeta del skill en el directorio de skills de Claude Code:

```bash
# macOS / Linux
cp -r documentar-objeto ~/.claude/skills/

# Windows (PowerShell)
Copy-Item -Recurse documentar-objeto "$env:USERPROFILE\.claude\skills\"
```

Reinicia Claude Code (o recarga la ventana) para que el skill quede disponible.

## Uso

Ejecuta el slash command desde cualquier directorio de proyecto Salesforce:

```
/documentar-objeto <NombreApiObjeto> <AliasOrg>
```

Documentar un objeto concreto:

```
/documentar-objeto Account mi-sandbox
```

Regenerar todos los objetos ya documentados:

```
/documentar-objeto all mi-sandbox
```

El fichero HTML se guarda en `documentator/<NombreApiObjeto>.html` en el directorio actual, y `documentator/index.html` se actualiza automáticamente.

## Ejemplos

> Abre [`docs/demo.html`](docs/demo.html) en cualquier navegador para probar una demo completamente interactiva con el objeto ficticio `PetClinic__c` — no necesitas servidor ni org de Salesforce.

### Tabla de campos — búsqueda, filtros y copia de API names

![Tabla de campos](docs/screenshots/fields.png)

### Campos fórmula — código expandible + explicación IA

![Panel de fórmula expandido](docs/screenshots/formula.png)

### Reglas de validación

![Reglas de validación](docs/screenshots/rules.png)

### Record-Triggered Flows

![Flows](docs/screenshots/flows.png)

### Apex Triggers — métodos por contexto

![Sección de Apex Triggers](docs/screenshots/triggers.png)

### Modo oscuro

![Modo oscuro](docs/screenshots/dark-mode.jpg)

## Estructura del proyecto

```
documentar-objeto/
├── SKILL.md                        # Instrucciones del skill para Claude Code
├── README.md
├── docs/
│   ├── demo.html                   # Demo autocontenida (objeto ficticio)
│   └── screenshots/                # Imágenes usadas en este README
└── scripts/
    └── sf_doc_generator.py         # Generador HTML (invocado por el skill)
```

Tras ejecutar el skill, el proyecto de destino recibe:

```
documentator/
├── index.html
├── Account.html
├── _formula_cache_Account.json     # Caché persistente de fórmulas (no borrar)
└── ...
```

## Cómo funciona

1. **Describe** — ejecuta `sf sobject describe` para obtener la metadata de todos los campos
2. **Descripciones de campo** — consulta `FieldDefinition` vía Tooling API
3. **Caché de fórmulas** — carga las explicaciones cacheadas; solo llama a la API de Anthropic para fórmulas nuevas o modificadas
4. **Análisis de triggers** — escanea los ficheros `.trigger` locales, detecta el patrón de delegación (TriggerFactory o handler directo), lee las clases handler y extrae descripciones de métodos por contexto
5. **Reglas de validación y flows** — consulta Tooling API
6. **Generación HTML** — combina todo en un único fichero HTML autocontenido

## Licencia

MIT
