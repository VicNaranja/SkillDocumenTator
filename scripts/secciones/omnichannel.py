"""
Seccion "Omni-Channel": toda la configuracion de Omni-Channel del org —canales de
servicio, colas con su enrutamiento, flows que enrutan el trabajo, presencia y
supervision— y los canales de mensajeria que le entregan trabajo, ya cruzada por
canal para pintarla.

Es la unica pagina en vivo que se guarda en disco (ver servir_documentator.py):
leer la metadata de todos los flows activos tarda decenas de segundos, y la
configuracion cambia poco.

Gotchas, comprobados contra un org:

- Una cola es de un canal cuando acepta su objeto (`QueueSobject`) y tiene
  configuracion de enrutamiento (`Group.QueueRoutingConfigId`). En SOQL, `Group`
  no trae la relacion `QueueRoutingConfig`: se cruza por Id.
- Los flows de Omni-Channel son los `ProcessType = 'RoutingFlow'`, pero el trabajo
  lo enruta la accion `routeWork`, que puede estar en cualquier flow: se busca en
  la metadata de todos los activos. La accion lleva el canal en
  `serviceChannelDevName` y la cola en `queueId`, con su etiqueta en `queueLabel`.
- Los flows de paquete (con namespace) no dejan leer su metadata: su
  `ActiveVersionId` no es un Id ("ns__Nombre-1"). Salen sin detalle.
- Que hace un canal de mensajeria con una sesion nueva (`sessionHandlerType`,
  `sessionHandlerFlow`, `sessionHandlerQueue`) no esta en el sObject
  `MessagingChannel`, cuyos `RoutingType` y `TargetQueueId` pueden venir vacios:
  solo en su metadata. Igual que los ajustes de Omni-Channel (`OmniChannelSettings`).
- Hay canales de mensajeria cuya metadata no se puede leer: la Metadata API responde
  500 UNKNOWN_EXCEPTION (visto con uno de tipo `Phone`) y tumba el lote entero. Si
  falla un lote se leen de uno en uno, y el que falla sale con lo que trae el sObject.
- Si Omni-Channel o la mensajeria no estan activados, sus objetos no existen y la
  consulta falla con INVALID_TYPE.
"""

import concurrent.futures

from org import ErrorCli

ID = "omnichannel"
TITULO = "Omni-Channel"

# Trabajo asignado (AgentWork) en este numero de dias.
DIAS_ACTIVIDAD = 90

AJUSTES = [
    ("enableOmniChannel", "Omni-Channel activado"),
    ("enableOmniSkillsRouting", "Enrutamiento por skills"),
    ("enableOmniSecondaryRoutingPriority", "Prioridad de enrutamiento secundaria"),
    ("enableOmniStatusCapModel", "Modelo de capacidad por estado"),
    ("enableOmniAutoLoginPrompt", "Pedir conexión a Omni-Channel al entrar"),
]

MODELOS = {"LeastActive": "Menos activo", "MostAvailable": "Más disponible"}
ENRUTAMIENTOS = {"QueueBased": "A una cola", "SkillsBased": "Por skills", "Copilot": "A un agente de Agentforce"}
TIPOS_FLOW = {"RoutingFlow": "Flow de Omni-Channel"}
TIPOS_GRUPO = {
    "Regular": "Grupo público",
    "Role": "Rol",
    "RoleAndSubordinates": "Rol y subordinados",
    "RoleAndSubordinatesInternal": "Rol y subordinados internos",
}

# Elementos de un flow que se pintan, con la etiqueta de su tipo.
ELEMENTOS = {
    "actionCalls": "Acción",
    "apexPluginCalls": "Apex",
    "assignments": "Asignación",
    "collectionProcessors": "Colección",
    "customErrors": "Error",
    "decisions": "Decisión",
    "loops": "Bucle",
    "recordCreates": "Crear registros",
    "recordDeletes": "Borrar registros",
    "recordLookups": "Buscar registros",
    "recordRollbacks": "Deshacer",
    "recordUpdates": "Actualizar registros",
    "screens": "Pantalla",
    "subflows": "Subflow",
    "transforms": "Transformar",
    "waits": "Espera",
    "ends": "Fin",
}


def _o_nada(org, soql, tooling=False):
    """La consulta, o nada si el objeto no existe porque la funcionalidad no esta activada."""
    try:
        return org.consultar(soql, tooling=tooling)
    except ErrorCli as error:
        if "INVALID_TYPE" in str(error):
            return []
        raise


def _en(ids):
    return "('" + "','".join(sorted(ids)) + "')"


def _nombres(org, objeto, ids, campo="Name"):
    """{Id: nombre} de los registros, en lotes de 200 Ids."""
    ids = sorted(i for i in set(ids) if i)
    resultado = {}
    for inicio in range(0, len(ids), 200):
        for r in org.consultar("SELECT Id, {} FROM {} WHERE Id IN {}".format(campo, objeto, _en(ids[inicio:inicio + 200]))):
            resultado[r["Id"]] = r
    return resultado


def _valor(parametro):
    """Valor de un parametro de flow: el literal, o «variable X» si sale de un recurso."""
    valor = (parametro or {}).get("value") or {}
    for clave in ("stringValue", "numberValue", "booleanValue"):
        if valor.get(clave) not in (None, ""):
            return str(valor[clave])
    if valor.get("setupReference"):
        return valor["setupReference"]
    if valor.get("elementReference"):
        return "variable " + valor["elementReference"]
    return ""


def _grafo(metadata):
    """
    Nodos y conexiones de un flow a partir de su metadata, mas sus acciones
    routeWork. Las conexiones llevan la etiqueta de la regla de decision o del
    camino que siguen.
    """
    nodos, aristas, enrutamientos = [], [], []

    def conectar(origen, conector, etiqueta="", fallo=False):
        destino = (conector or {}).get("targetReference")
        if destino:
            aristas.append({"de": origen, "a": destino, "etiqueta": etiqueta, "fallo": fallo})

    for tipo, etiqueta_tipo in ELEMENTOS.items():
        for elemento in metadata.get(tipo) or []:
            nombre = elemento.get("name")
            enruta = tipo == "actionCalls" and elemento.get("actionType") == "routeWork"
            nodos.append({
                "id": nombre,
                "etiqueta": elemento.get("label") or nombre,
                "tipo": tipo,
                "tipoEtiqueta": "Enrutar trabajo" if enruta else etiqueta_tipo,
            })
            conectar(nombre, elemento.get("connector"))
            conectar(nombre, elemento.get("faultConnector"), "Si falla", fallo=True)
            conectar(nombre, elemento.get("nextValueConnector"), "Cada elemento")
            conectar(nombre, elemento.get("noMoreValuesConnector"), "Al terminar")
            for regla in elemento.get("rules") or []:
                conectar(nombre, regla.get("connector"), regla.get("label") or regla.get("name") or "")
            for evento in elemento.get("waitEvents") or []:
                conectar(nombre, evento.get("connector"), evento.get("label") or evento.get("name") or "")
            if elemento.get("defaultConnector"):
                conectar(nombre, elemento["defaultConnector"], elemento.get("defaultConnectorLabel") or "Por defecto")

            if enruta:
                parametros = {p.get("name"): p for p in elemento.get("inputParameters") or []}
                tipo_enrutamiento = _valor(parametros.get("routingType"))
                enrutamientos.append({
                    "elemento": elemento.get("label") or nombre,
                    "canal": _valor(parametros.get("serviceChannelDevName")) or _valor(parametros.get("serviceChannelId")),
                    "tipo": tipo_enrutamiento,
                    "tipoEtiqueta": ENRUTAMIENTOS.get(tipo_enrutamiento, tipo_enrutamiento),
                    "colaId": _valor(parametros.get("queueId")),
                    "cola": _valor(parametros.get("queueLabel")),
                    "agenteIA": _valor(parametros.get("copilotLabel")) or _valor(parametros.get("botLabel")),
                })

    inicio = metadata.get("start") or {}
    nodos.insert(0, {"id": "__inicio__", "etiqueta": "Inicio", "tipo": "start", "tipoEtiqueta": "Inicio"})
    conectar("__inicio__", inicio.get("connector"))
    for camino in inicio.get("scheduledPaths") or []:
        conectar("__inicio__", camino.get("connector"), camino.get("label") or camino.get("name") or "")
    if metadata.get("startElementReference"):
        conectar("__inicio__", {"targetReference": metadata["startElementReference"]})

    return {"nodos": nodos, "aristas": aristas}, enrutamientos


def _metadata_flow(org, version):
    registros = org.consultar("SELECT Metadata FROM Flow WHERE Id = '{}'".format(version), tooling=True)
    return (registros[0].get("Metadata") or {}) if registros else {}


def _flows(org):
    """
    Los flows de Omni-Channel y cualquier otro flow activo que enrute trabajo,
    con su grafo. Los de paquete salen sin leer.
    """
    campos = (
        "SELECT DurableId, ApiName, Label, Description, ProcessType, IsActive, VersionNumber, ActiveVersionId, "
        "LatestVersionId, NamespacePrefix, TriggerObjectOrEventLabel FROM FlowDefinitionView WHERE "
    )
    # FlowDefinitionView no admite OR en el WHERE («Disjunctions not supported»).
    definiciones = {
        d["DurableId"]: d
        for consulta in ("ProcessType = 'RoutingFlow'", "IsActive = true AND NamespacePrefix = null")
        for d in org.consultar(campos + consulta)
    }.values()
    legibles = {
        d["DurableId"]: d.get("ActiveVersionId") or d.get("LatestVersionId")
        for d in definiciones
        if not d.get("NamespacePrefix") and (d.get("ActiveVersionId") or d.get("LatestVersionId"))
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ejecutor:
        pendientes = {clave: ejecutor.submit(_metadata_flow, org, version) for clave, version in legibles.items()}
        metadatas = {clave: pendiente.result() for clave, pendiente in pendientes.items()}

    try:
        tipos = dict(org.picklist("FlowDefinitionView", "ProcessType"), **TIPOS_FLOW)
    except ErrorCli:
        tipos = dict(TIPOS_FLOW)

    flows = []
    for d in definiciones:
        es_omni = d.get("ProcessType") == "RoutingFlow"
        grafo, enrutamientos = _grafo(metadatas[d["DurableId"]]) if d["DurableId"] in metadatas else (None, [])
        # Del resto de flows activos solo interesan los que enrutan trabajo.
        if not es_omni and not enrutamientos:
            continue
        activa = d.get("ActiveVersionId")
        flows.append({
            "id": d["DurableId"],
            "etiqueta": d.get("Label") or d.get("ApiName") or "",
            "api": ((d["NamespacePrefix"] + "__") if d.get("NamespacePrefix") else "") + (d.get("ApiName") or ""),
            "tipo": tipos.get(d.get("ProcessType"), d.get("ProcessType") or ""),
            "objeto": d.get("TriggerObjectOrEventLabel") or "",
            "descripcion": d.get("Description") or "",
            "activo": bool(d.get("IsActive")),
            "version": d.get("VersionNumber"),
            "sinActivar": bool(activa) and activa != d.get("LatestVersionId"),
            "paquete": d.get("NamespacePrefix") or "",
            "legible": grafo is not None,
            "enrutamientos": enrutamientos,
            "grafo": grafo,
        })
    return sorted(flows, key=lambda f: (not f["activo"], f["etiqueta"].lower()))


def _mensajeria(org):
    """Canales de mensajeria, con su gestion de sesiones leida de la metadata."""
    canales = _o_nada(
        org,
        "SELECT Id, DeveloperName, MasterLabel, MessageType, IsActive, RoutingType, TargetQueueId, "
        "FallbackQueueId, IsRestrictedToBusinessHours, BusinessHoursId FROM MessagingChannel ORDER BY MasterLabel",
    )
    if not canales:
        return [], {}, set()
    nombres = [c["DeveloperName"] for c in canales]
    ilegibles = set()
    try:
        registros = org.leer_metadata("MessagingChannel", nombres)
    except ErrorCli:
        registros = []
        for nombre in nombres:
            try:
                registros += org.leer_metadata("MessagingChannel", [nombre])
            except ErrorCli:
                ilegibles.add(nombre)
    return canales, {m.get("fullName"): m for m in registros}, ilegibles


def _ajustes(org):
    registros = org.leer_metadata("OmniChannelSettings", ["OmniChannel"])
    return registros[0] if registros else {}


def recoger(org):
    canales = _o_nada(
        org,
        "SELECT Id, DeveloperName, MasterLabel, RelatedEntity, HasAutoAcceptEnabled, HasAfterConvoWorkTimer, "
        "AfterConvoWorkMaxTime, DoesMinimizeWidgetOnAccept FROM ServiceChannel ORDER BY MasterLabel",
    )
    if not canales:
        return {
            "id": ID, "titulo": TITULO, "sinOmni": True,
            "descripcion": "El org no tiene canales de servicio: Omni-Channel no está activado o no se ha configurado.",
        }

    actividad = "LAST_N_DAYS:{}".format(DIAS_ACTIVIDAD)
    tareas = {
        "ajustes": lambda: _ajustes(org),
        "enrutamientos": lambda: org.consultar(
            "SELECT Id, DeveloperName, MasterLabel, RoutingPriority, RoutingModel, CapacityWeight, CapacityPercentage, "
            "PushTimeout, OverflowAssigneeId, IsAttributeBased, DropAdditionalSkillsTimeout FROM QueueRoutingConfig"
        ),
        "colas": lambda: org.consultar(
            "SELECT Id, Name, DeveloperName, QueueRoutingConfigId FROM Group WHERE Type = 'Queue' ORDER BY Name"
        ),
        "objetosCola": lambda: org.consultar("SELECT QueueId, SobjectType FROM QueueSobject"),
        "miembros": lambda: org.consultar(
            "SELECT GroupId, UserOrGroupId FROM GroupMember WHERE Group.Type = 'Queue'"
        ),
        "estados": lambda: org.consultar("SELECT Id, DeveloperName, MasterLabel FROM ServicePresenceStatus ORDER BY MasterLabel"),
        "estadosCanal": lambda: org.consultar("SELECT ServiceChannelId, ServicePresenceStatusId FROM ServiceChannelStatus"),
        "presencia": lambda: org.consultar(
            "SELECT Id, DeveloperName, MasterLabel, Capacity, OptionsIsAutoAcceptEnabled, OptionsIsDeclineEnabled, "
            "OptionsIsDeclineReasonEnabled, PresenceStatusOnDeclineId, PresenceStatusOnPushTimeoutId, "
            "InterruptibleCapacity FROM PresenceUserConfig ORDER BY MasterLabel"
        ),
        "presenciaUsuarios": lambda: org.consultar("SELECT PresenceUserConfigId, UserId FROM PresenceUserConfigUser"),
        "presenciaPerfiles": lambda: org.consultar("SELECT PresenceUserConfigId, ProfileId FROM PresenceUserConfigProfile"),
        "presenciaMotivos": lambda: _o_nada(org, "SELECT PresenceUserConfigId, PresenceDeclineReason.MasterLabel FROM PresenceConfigDeclineReason"),
        "supervision": lambda: _o_nada(org, "SELECT Id, DeveloperName, MasterLabel FROM OmniSupervisorConfig ORDER BY MasterLabel"),
        "supervisionColas": lambda: _o_nada(org, "SELECT OmniSupervisorConfigId, QueueId FROM OmniSupervisorConfigQueue"),
        "supervisionUsuarios": lambda: _o_nada(org, "SELECT OmniSupervisorConfigId, UserId FROM OmniSupervisorConfigUser"),
        "supervisionPerfiles": lambda: _o_nada(org, "SELECT OmniSupervisorConfigId, ProfileId FROM OmniSupervisorConfigProfile"),
        "supervisionGrupos": lambda: _o_nada(org, "SELECT OmniSupervisorConfigId, GroupId FROM OmniSupervisorConfigGroup"),
        "trabajoCanal": lambda: org.consultar(
            "SELECT ServiceChannelId c, COUNT(Id) n FROM AgentWork WHERE CreatedDate = {} GROUP BY ServiceChannelId".format(actividad)
        ),
        "trabajoCola": lambda: org.consultar(
            "SELECT OriginalQueueId c, COUNT(Id) n FROM AgentWork WHERE CreatedDate = {} GROUP BY OriginalQueueId".format(actividad)
        ),
        "mensajeria": lambda: _mensajeria(org),
        "flows": lambda: _flows(org),
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tareas)) as ejecutor:
        pendientes = {clave: ejecutor.submit(tarea) for clave, tarea in tareas.items()}
        r = {clave: pendiente.result() for clave, pendiente in pendientes.items()}

    mensajeria, metadata_mensajeria, mensajeria_ilegible = r["mensajeria"]

    # Nombres de lo que se referencia por Id: usuarios, grupos, roles, perfiles.
    ids_usuarios = {m["UserOrGroupId"] for m in r["miembros"] if m["UserOrGroupId"].startswith("005")}
    ids_usuarios |= {p["UserId"] for p in r["presenciaUsuarios"]} | {s["UserId"] for s in r["supervisionUsuarios"]}
    ids_grupos = {m["UserOrGroupId"] for m in r["miembros"] if m["UserOrGroupId"].startswith("00G")}
    ids_grupos |= {s["GroupId"] for s in r["supervisionGrupos"]}
    ids_desbordamiento = {q["OverflowAssigneeId"] for q in r["enrutamientos"] if q.get("OverflowAssigneeId")}
    ids_usuarios |= {i for i in ids_desbordamiento if i.startswith("005")}
    ids_perfiles = {p["ProfileId"] for p in r["presenciaPerfiles"]} | {s["ProfileId"] for s in r["supervisionPerfiles"]}

    usuarios = _nombres(org, "User", ids_usuarios, "Name, IsActive")
    grupos = _nombres(org, "Group", ids_grupos, "Name, DeveloperName, Type, RelatedId")
    roles = _nombres(org, "UserRole", [g.get("RelatedId") for g in grupos.values() if (g.get("RelatedId") or "").startswith("00E")])
    perfiles = _nombres(org, "Profile", ids_perfiles)

    colas_por_id = {c["Id"]: c for c in r["colas"]}
    colas_por_api = {c["DeveloperName"]: c for c in r["colas"]}

    def nombre_usuario(i):
        return (usuarios.get(i) or {}).get("Name") or i

    def nombre_grupo(i):
        if i in colas_por_id:
            return colas_por_id[i]["Name"]
        g = grupos.get(i) or {}
        if g.get("Type", "").startswith("Role"):
            return (roles.get(g.get("RelatedId")) or {}).get("Name") or g.get("DeveloperName") or i
        return g.get("Name") or g.get("DeveloperName") or i

    def miembro(i):
        if i.startswith("005"):
            u = usuarios.get(i) or {}
            return {"id": i, "tipo": "Usuario", "nombre": u.get("Name") or i, "activo": bool(u.get("IsActive", True))}
        g = grupos.get(i) or {}
        return {"id": i, "tipo": TIPOS_GRUPO.get(g.get("Type"), g.get("Type") or "Grupo"), "nombre": nombre_grupo(i), "activo": True}

    etiquetas_objetos = org.etiquetas_objetos()
    try:
        modelos = dict(org.picklist("QueueRoutingConfig", "RoutingModel"), **MODELOS)
    except ErrorCli:
        modelos = dict(MODELOS)

    enrutamientos = {}
    for q in r["enrutamientos"]:
        desborde = q.get("OverflowAssigneeId")
        enrutamientos[q["Id"]] = {
            "id": q["Id"],
            "nombre": q.get("MasterLabel") or q.get("DeveloperName"),
            "api": q.get("DeveloperName"),
            "modelo": modelos.get(q.get("RoutingModel"), q.get("RoutingModel") or ""),
            "prioridad": q.get("RoutingPriority"),
            "pesoCapacidad": q.get("CapacityWeight"),
            "porcentajeCapacidad": q.get("CapacityPercentage"),
            "timeout": q.get("PushTimeout"),
            "desbordamiento": (nombre_usuario(desborde) if desborde.startswith("005") else nombre_grupo(desborde)) if desborde else "",
            "porAtributos": bool(q.get("IsAttributeBased")),
            "quitarSkills": q.get("DropAdditionalSkillsTimeout"),
        }

    objetos_cola = {}
    for o in r["objetosCola"]:
        objetos_cola.setdefault(o["QueueId"], []).append(o["SobjectType"])
    miembros_cola = {}
    for m in r["miembros"]:
        miembros_cola.setdefault(m["GroupId"], []).append(miembro(m["UserOrGroupId"]))

    trabajo_canal = {f["c"]: f["n"] for f in r["trabajoCanal"]}
    trabajo_cola = {f["c"]: f["n"] for f in r["trabajoCola"]}

    supervision = []
    colas_supervisadas = {}
    for s in r["supervision"]:
        colas_s = [c["QueueId"] for c in r["supervisionColas"] if c["OmniSupervisorConfigId"] == s["Id"]]
        for c in colas_s:
            colas_supervisadas.setdefault(c, []).append(s.get("MasterLabel") or s["DeveloperName"])
        supervision.append({
            "id": s["Id"],
            "nombre": s.get("MasterLabel") or s["DeveloperName"],
            "colas": sorted(nombre_grupo(c) for c in colas_s),
            "usuarios": sorted(nombre_usuario(u["UserId"]) for u in r["supervisionUsuarios"] if u["OmniSupervisorConfigId"] == s["Id"]),
            "perfiles": sorted((perfiles.get(p["ProfileId"]) or {}).get("Name") or p["ProfileId"]
                               for p in r["supervisionPerfiles"] if p["OmniSupervisorConfigId"] == s["Id"]),
            "grupos": sorted(nombre_grupo(g["GroupId"]) for g in r["supervisionGrupos"] if g["OmniSupervisorConfigId"] == s["Id"]),
        })

    colas = {}
    for c in r["colas"]:
        config = c.get("QueueRoutingConfigId")
        colas[c["Id"]] = {
            "id": c["Id"],
            "nombre": c.get("Name") or c["DeveloperName"],
            "api": c["DeveloperName"],
            "objetos": sorted(objetos_cola.get(c["Id"], [])),
            "enrutamiento": enrutamientos.get(config),
            "miembros": sorted(miembros_cola.get(c["Id"], []), key=lambda m: (m["tipo"] != "Usuario", m["nombre"].lower())),
            "trabajo": trabajo_cola.get(c["Id"], 0),
            "supervision": colas_supervisadas.get(c["Id"], []),
        }

    estados = {e["Id"]: e for e in r["estados"]}
    estados_por_canal = {}
    for ec in r["estadosCanal"]:
        estados_por_canal.setdefault(ec["ServiceChannelId"], []).append(ec["ServicePresenceStatusId"])

    flows = r["flows"]
    flows_por_api = {f["api"]: f for f in flows}

    lista_mensajeria = []
    for m in mensajeria:
        meta = metadata_mensajeria.get(m["DeveloperName"]) or {}
        cola_respaldo = colas_por_api.get(meta.get("sessionHandlerQueue") or "") or colas_por_id.get(m.get("FallbackQueueId") or "")
        flow = meta.get("sessionHandlerFlow") or ""
        lista_mensajeria.append({
            "id": m["Id"],
            "nombre": m.get("MasterLabel") or m["DeveloperName"],
            "api": m["DeveloperName"],
            "tipo": m.get("MessageType") or "",
            "activo": bool(m.get("IsActive")),
            "gestion": meta.get("sessionHandlerType") or m.get("RoutingType") or "",
            "flow": flows_por_api[flow]["id"] if flow in flows_por_api else "",
            "flowApi": flow,
            "colaRespaldo": cola_respaldo["Id"] if cola_respaldo else "",
            "colaDestino": m.get("TargetQueueId") or "",
            "horario": bool(m.get("IsRestrictedToBusinessHours")),
            "legible": m["DeveloperName"] not in mensajeria_ilegible,
        })

    revisar = []
    lista_canales = []
    for canal in canales:
        api = canal["DeveloperName"]
        objeto = canal.get("RelatedEntity") or ""
        flows_canal = [f for f in flows if any(e["canal"] == api for e in f["enrutamientos"])]
        destinos = {e["colaId"] for f in flows_canal for e in f["enrutamientos"] if e["canal"] == api and e["colaId"] in colas}
        colas_canal = [
            c["id"] for c in sorted(colas.values(), key=lambda c: c["nombre"].lower())
            if (objeto in c["objetos"] and c["enrutamiento"]) or c["id"] in destinos
        ]
        mensajeria_canal = [m["id"] for m in lista_mensajeria] if objeto == "MessagingSession" else []
        # Un canal de mensajeria que entra por un flow de otro canal tambien cuenta.
        for m in lista_mensajeria:
            if m["flow"] and m["id"] not in mensajeria_canal and any(f["id"] == m["flow"] for f in flows_canal):
                mensajeria_canal.append(m["id"])
        for m in lista_mensajeria:
            if m["id"] in mensajeria_canal and m["flow"] and not any(f["id"] == m["flow"] for f in flows_canal):
                flows_canal.append(next(f for f in flows if f["id"] == m["flow"]))

        estados_canal = sorted(estados[e]["MasterLabel"] for e in estados_por_canal.get(canal["Id"], []) if e in estados)
        nombre = canal.get("MasterLabel") or api
        lista_canales.append({
            "id": canal["Id"],
            "nombre": nombre,
            "api": api,
            "objeto": objeto,
            "objetoEtiqueta": etiquetas_objetos.get(objeto, objeto),
            "aceptacionAutomatica": bool(canal.get("HasAutoAcceptEnabled")),
            "minimizarAlAceptar": bool(canal.get("DoesMinimizeWidgetOnAccept")),
            "trabajoPosterior": canal.get("AfterConvoWorkMaxTime") if canal.get("HasAfterConvoWorkTimer") else None,
            "colas": colas_canal,
            "flows": [f["id"] for f in flows_canal],
            "mensajeria": mensajeria_canal,
            "estados": estados_canal,
            "trabajo": trabajo_canal.get(canal["Id"], 0),
        })

        if not estados_canal:
            revisar.append({"canal": api, "texto": "Ningún estado de presencia habilita el canal {}: ningún agente puede recibir su trabajo.".format(nombre)})
        if not colas_canal:
            revisar.append({"canal": api, "texto": "El canal {} no tiene colas: ninguna acepta {} con configuración de enrutamiento ni es destino de un flow.".format(nombre, objeto)})
        for c in colas.values():
            if objeto in c["objetos"] and not c["enrutamiento"]:
                revisar.append({"canal": api, "texto": "La cola {} acepta {} pero no tiene configuración de enrutamiento.".format(c["nombre"], objeto)})
        for cid in colas_canal:
            c = colas[cid]
            if not c["miembros"]:
                revisar.append({"canal": api, "texto": "La cola {} no tiene miembros.".format(c["nombre"])})
            inactivos = [m["nombre"] for m in c["miembros"] if not m["activo"]]
            if inactivos:
                revisar.append({"canal": api, "texto": "La cola {} tiene usuarios inactivos como miembros: {}.".format(c["nombre"], ", ".join(inactivos))})
        for f in flows_canal:
            if not f["activo"]:
                revisar.append({"canal": api, "texto": "El flow {} enruta trabajo de este canal pero no está activo.".format(f["etiqueta"])})
            for e in f["enrutamientos"]:
                if e["canal"] == api and e["colaId"] and not e["colaId"].startswith("variable") and e["colaId"] not in colas:
                    revisar.append({"canal": api, "texto": "El flow {} envía a la cola «{}», que no existe en el org.".format(f["etiqueta"], e["cola"] or e["colaId"])})
        for mid in mensajeria_canal:
            m = next(x for x in lista_mensajeria if x["id"] == mid)
            if not m["activo"]:
                revisar.append({"canal": api, "texto": "El canal de mensajería {} no está activo.".format(m["nombre"])})
            if not m["legible"]:
                revisar.append({"canal": api, "texto": "Salesforce no deja leer la configuración del canal de mensajería {}: su flow y su cola de respaldo pueden faltar.".format(m["nombre"])})
            if m["flowApi"] and not m["flow"]:
                revisar.append({"canal": api, "texto": "El canal de mensajería {} usa el flow {}, que no está en el org.".format(m["nombre"], m["flowApi"])})

    configuraciones_usadas = {c["enrutamiento"]["api"] for c in colas.values() if c["enrutamiento"]}
    for q in enrutamientos.values():
        if q["api"] not in configuraciones_usadas:
            revisar.append({"canal": None, "texto": "La configuración de enrutamiento {} no la usa ninguna cola.".format(q["nombre"])})
    for f in flows:
        if f["legible"] and not f["enrutamientos"]:
            revisar.append({"canal": None, "texto": "El flow de Omni-Channel {} no tiene ninguna acción de enrutar trabajo.".format(f["etiqueta"])})

    motivos = {}
    for m in r["presenciaMotivos"]:
        motivos.setdefault(m["PresenceUserConfigId"], []).append(((m.get("PresenceDeclineReason") or {}).get("MasterLabel")) or "")
    presencia = []
    for p in r["presencia"]:
        presencia.append({
            "id": p["Id"],
            "nombre": p.get("MasterLabel") or p["DeveloperName"],
            "capacidad": p.get("Capacity"),
            "capacidadInterrumpible": p.get("InterruptibleCapacity"),
            "aceptacionAutomatica": bool(p.get("OptionsIsAutoAcceptEnabled")),
            "rechazo": bool(p.get("OptionsIsDeclineEnabled")),
            "motivosRechazo": sorted(motivos.get(p["Id"], [])) if p.get("OptionsIsDeclineReasonEnabled") else [],
            "estadoAlRechazar": (estados.get(p.get("PresenceStatusOnDeclineId")) or {}).get("MasterLabel") or "",
            "estadoAlTimeout": (estados.get(p.get("PresenceStatusOnPushTimeoutId")) or {}).get("MasterLabel") or "",
            "usuarios": sorted(nombre_usuario(u["UserId"]) for u in r["presenciaUsuarios"] if u["PresenceUserConfigId"] == p["Id"]),
            "perfiles": sorted((perfiles.get(x["ProfileId"]) or {}).get("Name") or x["ProfileId"]
                               for x in r["presenciaPerfiles"] if x["PresenceUserConfigId"] == p["Id"]),
        })

    canal_por_id = {c["id"]: c["nombre"] for c in lista_canales}
    lista_estados = [
        {"id": e["Id"], "nombre": e["MasterLabel"], "api": e["DeveloperName"],
         "canales": sorted(canal_por_id[ec["ServiceChannelId"]] for ec in r["estadosCanal"]
                           if ec["ServicePresenceStatusId"] == e["Id"] and ec["ServiceChannelId"] in canal_por_id)}
        for e in r["estados"]
    ]

    ajustes = r["ajustes"]
    return {
        "id": ID,
        "titulo": TITULO,
        "descripcion": (
            "Cómo llega el trabajo a los agentes: por qué canal entra, qué flow lo enruta, a qué cola va y quién "
            "lo atiende, con la configuración de cada pieza. La pestaña General resume el org; cada canal tiene la suya."
        ),
        "sinOmni": False,
        "diasActividad": DIAS_ACTIVIDAD,
        "ajustes": [{"etiqueta": etiqueta, "activo": ajustes.get(clave) == "true"} for clave, etiqueta in AJUSTES],
        "canales": lista_canales,
        "colas": colas,
        "flows": flows,
        "mensajeria": lista_mensajeria,
        "estados": lista_estados,
        "presencia": presencia,
        "supervision": supervision,
        "revisar": revisar,
    }
