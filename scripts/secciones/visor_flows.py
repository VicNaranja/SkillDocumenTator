"""
Seccion "Visor de flows": todos los flows del org de una vez, con su tipo, su
disparador, si estan activos y quien los toco por ultimo.

Se traen enteros al abrir la pestana —son unos centenares de filas— y el
buscador, los filtros y la ordenacion trabajan en el navegador sin volver al
org. La base es FlowDefinitionView, que va por SOQL normal y ya trae resueltos el
nombre de quien modifico y el objeto que dispara. Lo que esa vista no da se
completa con la Tooling API: el contexto de ejecucion de la version activa y el
objeto de los Process Builder.
"""

import concurrent.futures

ID = "visor-flows"
TITULO = "Visor de flows"

CAMPOS = (
    "DurableId, ApiName, Label, Description, ProcessType, TriggerType, RecordTriggerType, "
    "TriggerObjectOrEventLabel, TriggerOrder, IsActive, VersionNumber, ActiveVersionId, "
    "LatestVersionId, ApiVersion, LastModifiedBy, LastModifiedDate, NamespacePrefix, "
    "ManageableState, InstalledPackageName, IsTemplate"
)

# Las etiquetas del org salen en ingles y largas («Record—Run After Save»); en una
# barra de filtros se leen mejor cortas. Lo que no este aqui usa la del org.
DISPARADORES = {
    "RecordBeforeSave": "Antes de guardar",
    "RecordAfterSave": "Después de guardar",
    "RecordBeforeDelete": "Antes de borrar",
    "Scheduled": "Programado",
    "PlatformEvent": "Evento de plataforma",
}
# «Record Change Process» es como el org llama a los Process Builder.
TIPOS = {"Workflow": "Process Builder"}

# Sin valor, la version corre en el modo por defecto: el que corresponda a como se
# lance (los de registro, en sistema; los de pantalla, como el usuario).
CONTEXTOS = {
    None: "Por defecto",
    "DefaultMode": "Por defecto",
    "SystemModeWithSharing": "Sistema con sharing",
    "SystemModeWithoutSharing": "Sistema sin sharing",
}

# Disparo de un Process Builder, que vive en sus processMetadataValues.
OPERACIONES_PROCESS_BUILDER = {
    "onCreateOnly": "Create",
    "onAllChanges": "CreateAndUpdate",
}

# Objeto de cada version de Process Builder ya leida, por (alias, Id de version).
# Una version no cambia una vez guardada, asi que no hay nada que invalidar: si se
# activa otra, su Id es otro.
_objetos_process_builder = {}


def _etiquetas(org, campo):
    """{valor: etiqueta} del picklist, con el valor tal cual si el org no da etiqueta."""
    try:
        return org.picklist("FlowDefinitionView", campo)
    except Exception:
        return {}


def _contextos(org):
    """
    {Id de FlowDefinition: RunInMode de su version activa}.

    FlowVersionView no admite consultarse sin filtrar por flow, asi que sale de
    la Tooling API. Los flows de paquetes gestionados no aparecen ahi.
    """
    return {
        r["DefinitionId"]: r.get("RunInMode")
        for r in org.consultar("SELECT DefinitionId, RunInMode FROM Flow WHERE Status = 'Active'", tooling=True)
    }


def _objeto_process_builder(org, version):
    """
    (ObjectType, TriggerType) de una version de Process Builder.

    FlowDefinitionView no da el objeto de los Process Builder: esta en la
    metadata de la version, y la Tooling API solo devuelve Metadata consultando
    los flows de uno en uno.
    """
    clave = (org.alias, version)
    if clave not in _objetos_process_builder:
        registros = org.consultar("SELECT Metadata FROM Flow WHERE Id = '{}'".format(version), tooling=True)
        valores = {}
        for valor in ((registros[0].get("Metadata") or {}).get("processMetadataValues") or []) if registros else []:
            valores[valor.get("name")] = (valor.get("value") or {}).get("stringValue")
        _objetos_process_builder[clave] = (valores.get("ObjectType") or "", valores.get("TriggerType") or "")
    return _objetos_process_builder[clave]


def recoger(org):
    """Lista completa de flows, con las etiquetas de sus picklists ya resueltas."""
    flows = org.consultar("SELECT {} FROM FlowDefinitionView ORDER BY Label".format(CAMPOS))

    versiones_process_builder = {
        f["DurableId"]: f.get("ActiveVersionId") or f.get("LatestVersionId")
        for f in flows
        if f.get("ProcessType") == "Workflow" and (f.get("ActiveVersionId") or f.get("LatestVersionId"))
    }

    # La metadata de cada Process Builder es una llamada por version: en paralelo,
    # junto con la de los contextos, para que el coste sea el de la mas lenta.
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ejecutor:
        contextos = ejecutor.submit(_contextos, org)
        pendientes = {
            definicion: ejecutor.submit(_objeto_process_builder, org, version)
            for definicion, version in versiones_process_builder.items()
        }
        contextos = contextos.result()
        process_builder = {definicion: pendiente.result() for definicion, pendiente in pendientes.items()}

    tipos = dict(_etiquetas(org, "ProcessType"), **TIPOS)
    disparadores = dict(_etiquetas(org, "TriggerType"), **DISPARADORES)
    operaciones = _etiquetas(org, "RecordTriggerType")
    etiquetas_objetos = org.etiquetas_objetos()

    filas = []
    for f in flows:
        tipo = f.get("ProcessType") or ""
        disparador = f.get("TriggerType") or ""
        disparador_etiqueta = disparadores.get(disparador, disparador)
        operacion = operaciones.get(f.get("RecordTriggerType") or "", f.get("RecordTriggerType") or "")
        objeto = f.get("TriggerObjectOrEventLabel") or ""

        if f["DurableId"] in process_builder:
            objeto_api, disparo = process_builder[f["DurableId"]]
            # Misma forma que el resto: etiqueta del objeto, no su nombre API.
            objeto = objeto or etiquetas_objetos.get(objeto_api, objeto_api)
            operacion = operacion or OPERACIONES_PROCESS_BUILDER.get(disparo, disparo)
            # Se distingue de «Después de guardar» para que filtrar por ese
            # disparador no mezcle flows con Process Builder.
            if objeto_api and not disparador:
                disparador_etiqueta = "Al guardar (Process Builder)"

        prefijo = f.get("NamespacePrefix")
        activa = f.get("ActiveVersionId")
        filas.append({
            "id": f["DurableId"],
            "etiqueta": f.get("Label") or "",
            "nombre": (prefijo + "__" if prefijo else "") + (f.get("ApiName") or ""),
            "descripcion": f.get("Description") or "",
            "tipo": tipo,
            "tipoEtiqueta": tipos.get(tipo, tipo),
            "disparador": disparador,
            "disparadorEtiqueta": disparador_etiqueta,
            "operacion": operacion,
            "objeto": objeto,
            "ordenEjecucion": f.get("TriggerOrder"),
            # Solo de los activos que da la Tooling API: un inactivo no corre, y los
            # de paquete gestionado no salen ahi.
            "contexto": CONTEXTOS.get(contextos[f["DurableId"]], contextos[f["DurableId"]])
            if activa and f["DurableId"] in contextos else "",
            "activo": bool(f.get("IsActive")),
            "version": f.get("VersionNumber"),
            # Hay una version posterior a la activa: lo que se ve en el builder no
            # es lo que se ejecuta.
            "sinActivar": bool(activa) and activa != f.get("LatestVersionId"),
            "api": f.get("ApiVersion"),
            "modificadoPor": f.get("LastModifiedBy") or "",
            "modificado": f.get("LastModifiedDate") or "",
            "paquete": f.get("InstalledPackageName") or prefijo or "",
            "plantilla": bool(f.get("IsTemplate")),
        })

    return {
        "id": ID,
        "titulo": TITULO,
        "descripcion": (
            "Todos los flows del org, incluidos los de paquetes instalados. Los filtros de cada "
            "grupo se suman entre sí (uno u otro) y los grupos se combinan (uno y otro). "
            "Pulsa una cabecera para ordenar; con Mayúsculas, la añades como criterio siguiente."
        ),
        "flows": filas,
    }
