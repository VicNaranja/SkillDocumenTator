"""
Seccion "Gestion de usuarios": listado de los usuarios del org con filtros
acumulables y columnas a eleccion.

Al abrir la pestana solo se recoge el catalogo de los filtros —perfiles,
permission set groups, permission sets y campos de User—; el listado se pide
aparte, por /api/detalle, en cada cambio de filtro o de pagina. Asi un cambio de
filtro no vuelve a pagar el catalogo, y el catalogo no paga un listado que se va
a filtrar enseguida.

Las paginas son de 2.000 usuarios y avanzan con el localizador de consulta del
org (`nextRecordsUrl`), no con OFFSET: SOQL topa el OFFSET en 2.000 filas, asi
que con el no se pasaria de la segunda pagina.

Los filtros de permisos son subconsultas sobre PermissionSetAssignment, y SOQL
admite dos por consulta. Con mas de dos, las que sobran se resuelven aqui: se
trae la lista entera de Ids que cumple el resto, se cruza con las asignaciones
y las paginas salen de esa lista, guardada en memoria del servidor.

Todo lo que llega del navegador —campos, columnas, Ids, operadores— se valida
contra el describe y el catalogo antes de entrar en la SOQL, y los valores
libres se escapan: el texto acaba interpolado en la consulta.
"""

import collections
import datetime
import json
import re
import threading
import uuid

from org import ErrorCli

ID = "gestion-usuarios"
TITULO = "Gestión de usuarios"

TAMANO_PAGINA = 2000

# Ids por consulta al pedir las asignaciones de una pagina: 2.000 de golpe no
# caben en la URL de un GET.
LOTE_IDS = 300

PATRON_ID = re.compile(r"^[a-zA-Z0-9]{15,18}$")
PATRON_CURSOR = re.compile(r"^/services/data/v\d+\.\d+/query/[\w-]+$")
PATRON_CURSOR_PROPIO = re.compile(r"^py:([0-9a-f]{32}):(\d+)$")

# Subconsultas IN / NOT IN que admite una SOQL.
MAX_SUBCONSULTAS = 2

# Listas de Ids resueltas en Python, por token. Se guardan las ultimas: cada
# busqueda nueva crea una y nadie vuelve a las viejas.
MAX_LISTAS = 20
_listas = collections.OrderedDict()
_cerrojo = threading.Lock()

COLUMNA_GRUPOS = "__grupos"
COLUMNA_PERMISOS = "__permisos"

# Condicion de perfil: como el desplegable de la barra, pero se pueden sumar varias.
CONDICION_PERFILES = "__perfiles"

# Condiciones de licencias: como los antiguos desplegables, pero se pueden sumar varias.
CONDICION_LICENCIAS = "__licencias"
CONDICION_LICENCIAS_PS = "__licenciasPs"

# Opcion «Sin rol» del desplegable de roles.
SIN_ROL = "__sin_rol"

COLUMNAS_POR_DEFECTO = [
    "Name", "Username", "Email", "Profile.Name", "UserRole.Name",
    "IsActive", "LastLoginDate", "CreatedDate",
]

# Tipos que no se pueden pedir como columna simple: los compuestos no devuelven
# un valor plano y el base64 solo se lee registro a registro.
TIPOS_SIN_COLUMNA = {"address", "location", "base64", "complexvalue"}

TIPOS_TEXTO = {"string", "email", "phone", "url", "picklist", "combobox", "textarea", "multipicklist"}
TIPOS_NUMERO = {"int", "double", "currency", "percent", "long"}

# Objetos a los que apunta un lookup de User y que tienen Name: solo de esos se
# ofrece el nombre como columna. Pedir `.Name` de un objeto que no lo tiene
# romperia la consulta entera.
OBJETOS_CON_NOMBRE = {"User", "Profile", "UserRole", "Contact", "Account", "CallCenter"}


# Preferencias de interfaz de cada usuario (avisos vistos, tours, vistas por
# defecto): un centenar de checkboxes que no sirven para buscar ni gestionar.
PREFIJO_PREFERENCIAS = "UserPreferences"


def _campos(org):
    return [
        campo for campo in org.describir("User")
        if campo.get("name") and not campo["name"].startswith(PREFIJO_PREFERENCIAS)
    ]


def _etiqueta_relacion(etiqueta):
    """«Id. de perfil» o «Profile ID» → «Perfil» / «Profile»."""
    limpia = re.sub(r"^(Id\.?|ID)\s+(del?\s+)?", "", etiqueta, flags=re.IGNORECASE)
    limpia = re.sub(r"\s+ID$", "", limpia).strip() or etiqueta
    return limpia[:1].upper() + limpia[1:]


def _columnas(org):
    """Columnas que se pueden pedir, en {campo: {etiqueta, tipo}}."""
    columnas = {}
    for campo in _campos(org):
        tipo = campo.get("type")
        if tipo in TIPOS_SIN_COLUMNA:
            continue

        columnas[campo["name"]] = {"etiqueta": campo.get("label") or campo["name"], "tipo": tipo}

        destinos = campo.get("referenceTo") or []
        relacion = campo.get("relationshipName")
        if tipo == "reference" and relacion and destinos and set(destinos) <= OBJETOS_CON_NOMBRE:
            columnas[relacion + ".Name"] = {
                "etiqueta": _etiqueta_relacion(campo.get("label") or relacion),
                "tipo": "string",
            }

    columnas[COLUMNA_GRUPOS] = {"etiqueta": "Permission set groups", "tipo": "lista"}
    columnas[COLUMNA_PERMISOS] = {"etiqueta": "Permission sets", "tipo": "lista"}
    return columnas


def _uso(licencia):
    """«usadas 120 de 200», para leer en el desplegable cuantas quedan libres."""
    def numero(valor):
        return "{:,}".format(valor or 0).replace(",", ".")
    return "usadas {} de {}".format(numero(licencia.get("UsedLicenses")), numero(licencia.get("TotalLicenses")))


def _tipos_usuario(org, filtrables):
    """
    Tipos de usuario con cuantos usuarios tiene cada uno. Salen de los datos y no
    solo del describe: el picklist no trae los de sistema (AutomatedProcess,
    CloudIntegrationUser), y los usuarios si los tienen.
    """
    etiquetas = next(
        ({v["valor"]: v["etiqueta"] for v in f["valores"]} for f in filtrables if f["campo"] == "UserType"), {}
    )
    cuantos = {r["UserType"]: r["n"] for r in org.consultar("SELECT UserType, COUNT(Id) n FROM User GROUP BY UserType")}
    tipos = list(etiquetas) + [t for t in cuantos if t not in etiquetas]
    # Primero los que tienen usuarios, de mas a menos: los vacios solo estorban arriba.
    tipos.sort(key=lambda t: -cuantos.get(t, 0))
    return [
        {
            "id": tipo,
            "etiqueta": etiquetas.get(tipo, tipo),
            "detalle": "{} · {} {}".format(
                tipo, "{:,}".format(cuantos.get(tipo, 0)).replace(",", "."),
                "usuario" if cuantos.get(tipo, 0) == 1 else "usuarios",
            ),
        }
        for tipo in tipos
    ]


def recoger(org):
    """
    Catalogo de los filtros y de las columnas. El listado no va aqui: lo pide el
    navegador por /api/detalle con los filtros que haya puestos.
    """
    perfiles = org.consultar("SELECT Id, Name, UserLicense.Name FROM Profile ORDER BY Name")
    grupos = org.consultar(
        "SELECT Id, MasterLabel, DeveloperName, NamespacePrefix FROM PermissionSetGroup ORDER BY MasterLabel"
    )
    # Fuera los permission sets que son un perfil por dentro y los que agregan un
    # grupo: ninguno se asigna como permission set suelto.
    permisos = org.consultar(
        "SELECT Id, Label, Name, NamespacePrefix FROM PermissionSet "
        "WHERE IsOwnedByProfile = false AND PermissionSetGroupId = null ORDER BY Label"
    )

    def nombre_api(registro, campo):
        prefijo = registro.get("NamespacePrefix")
        return (prefijo + "__" if prefijo else "") + (registro.get(campo) or "")

    filtrables = []
    for campo in _campos(org):
        if not campo.get("filterable") or campo.get("type") in TIPOS_SIN_COLUMNA:
            continue
        filtrables.append({
            "campo": campo["name"],
            "etiqueta": campo.get("label") or campo["name"],
            "tipo": campo.get("type"),
            "valores": [
                {"valor": v["value"], "etiqueta": v.get("label") or v["value"]}
                for v in campo.get("picklistValues") or [] if v.get("active", True)
            ],
        })
    filtrables.sort(key=lambda f: f["etiqueta"].lower())

    columnas = _columnas(org)

    return {
        "id": ID,
        "titulo": TITULO,
        "descripcion": (
            "Usuarios del org, en páginas de {}. Los filtros se suman: cada uno recorta lo que "
            "dejan los demás, y dentro de un desplegable basta con cumplir una de las opciones "
            "marcadas, y en los modos «no», no cumplir ninguna. En permission sets, «tiene» mira la "
            "asignación directa y «contiene» suma la que llega dentro de un permission set group. "
            "Para combinar varios filtros de permisos, añádelos con «+ Condición»."
        ).format("{:,}".format(TAMANO_PAGINA).replace(",", ".")),
        "tamanoPagina": TAMANO_PAGINA,
        "perfiles": [
            {"id": p["Id"], "etiqueta": p["Name"], "detalle": (p.get("UserLicense") or {}).get("Name") or ""}
            for p in perfiles
        ],
        "grupos": [
            {"id": g["Id"], "etiqueta": g["MasterLabel"], "detalle": nombre_api(g, "DeveloperName")}
            for g in grupos
        ],
        "permisos": [
            {"id": p["Id"], "etiqueta": p["Label"], "detalle": nombre_api(p, "Name")}
            for p in permisos
        ],
        "filtrables": filtrables,
        "tiposUsuario": _tipos_usuario(org, filtrables),
        "roles": [{"id": SIN_ROL, "etiqueta": "Sin rol", "detalle": "usuarios sin rol asignado"}] + [
            {"id": r["Id"], "etiqueta": r["Name"], "detalle": r["DeveloperName"]}
            for r in org.consultar("SELECT Id, Name, DeveloperName FROM UserRole ORDER BY Name")
        ],
        # Primero las que tienen alguna asignada: de las de permission set hay
        # cientos, y la mayoria son de productos que el org no usa.
        "licencias": [
            {"id": l["Id"], "etiqueta": l["Name"], "detalle": _uso(l)}
            for l in sorted(
                org.consultar("SELECT Id, Name, TotalLicenses, UsedLicenses FROM UserLicense"),
                # UserLicense no ordena por UsedLicenses en la SOQL: se ordena aqui.
                key=lambda l: (-(l.get("UsedLicenses") or 0), l["Name"].lower()),
            )
        ],
        "licenciasPs": [
            {"id": l["Id"], "etiqueta": l["MasterLabel"], "detalle": "{} · {}".format(l["DeveloperName"], _uso(l))}
            for l in org.consultar(
                "SELECT Id, MasterLabel, DeveloperName, TotalLicenses, UsedLicenses "
                "FROM PermissionSetLicense ORDER BY UsedLicenses DESC, MasterLabel"
            )
        ],
        "columnas": [
            {"campo": campo, "etiqueta": datos["etiqueta"], "tipo": datos["tipo"]}
            for campo, datos in sorted(columnas.items(), key=lambda par: par[1]["etiqueta"].lower())
        ],
        "columnasPorDefecto": [c for c in COLUMNAS_POR_DEFECTO if c in columnas],
    }


# --------------------------------------------------------------------------- #


def _texto(valor):
    return "'" + str(valor).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _patron(valor):
    """Literal para LIKE '%valor%', con los comodines del usuario escapados."""
    escapado = str(valor).replace("\\", "\\\\").replace("'", "\\'").replace("%", "\\%").replace("_", "\\_")
    return "'%" + escapado + "%'"


def _ids(lista, que):
    lista = [i for i in (lista or []) if i]
    for identificador in lista:
        if not PATRON_ID.match(identificador):
            raise ValueError("Id de {} no válido: {}".format(que, identificador))
    return ", ".join(_texto(i) for i in lista)


def _fecha(valor):
    texto = valor.strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(texto, formato).date().isoformat()
        except ValueError:
            pass
    raise ValueError("«{}» no es una fecha (AAAA-MM-DD o DD/MM/AAAA).".format(valor))


def _condicion(condicion, definiciones):
    nombre = condicion.get("campo")
    definicion = definiciones.get(nombre)
    if not definicion or not definicion.get("filterable"):
        raise ValueError("El campo «{}» no existe en User o no se puede filtrar.".format(nombre))

    tipo = definicion.get("type")
    operador = condicion.get("operador")
    valor = str(condicion.get("valor") or "").strip()

    if operador in ("contiene", "no_contiene"):
        if tipo not in TIPOS_TEXTO:
            raise ValueError("«contiene» solo se aplica a campos de texto; {} es {}.".format(nombre, tipo))
        if not valor:
            raise ValueError("«contiene» necesita un valor en {}.".format(nombre))
        if tipo == "multipicklist":
            return "{} {} ({})".format(nombre, "INCLUDES" if operador == "contiene" else "EXCLUDES", _texto(valor))
        if operador == "contiene":
            return "{} LIKE {}".format(nombre, _patron(valor))
        # NOT LIKE deja fuera los vacios, y un campo vacio tampoco contiene el texto.
        return "({0} = null OR (NOT {0} LIKE {1}))".format(nombre, _patron(valor))

    if operador not in ("igual", "distinto"):
        raise ValueError("Operador no válido: {}".format(operador))
    comparador = "=" if operador == "igual" else "!="

    # Igual a nada es «sin valor», y distinto de nada, «con valor».
    if not valor:
        return "{} {} null".format(nombre, comparador)

    if tipo == "boolean":
        verdadero = valor.lower() in ("true", "sí", "si", "1", "verdadero")
        if not verdadero and valor.lower() not in ("false", "no", "0", "falso"):
            raise ValueError("{} es un checkbox: el valor es sí o no.".format(nombre))
        literal = "true" if verdadero else "false"
    elif tipo in TIPOS_NUMERO:
        try:
            numero = float(valor.replace(",", "."))
        except ValueError:
            raise ValueError("{} es numérico: «{}» no es un número.".format(nombre, valor))
        literal = int(numero) if numero.is_integer() else numero
    elif tipo == "date":
        literal = _fecha(valor)
    elif tipo == "datetime":
        # Una fecha y hora se compara por dia: nadie conoce el segundo exacto.
        return "DAY_ONLY({}) {} {}".format(nombre, comparador, _fecha(valor[:10] if "T" in valor else valor))
    else:
        literal = _texto(valor)

    return "{} {} {}".format(nombre, comparador, literal)


def _grupos_que_contienen(org, permisos):
    """Ids de los permission set groups que incluyen alguno de esos permission sets."""
    registros = org.consultar(
        "SELECT PermissionSetGroupId FROM PermissionSetGroupComponent WHERE PermissionSetId IN ({})".format(permisos)
    )
    return sorted({r["PermissionSetGroupId"] for r in registros if r.get("PermissionSetGroupId")})


MODOS_GRUPO = ("tiene", "no_tiene")
MODOS_PERMISO = ("tiene", "no_tiene", "contiene", "no_contiene")


def _filtro_permisos(org, clave, ids, modo):
    """
    Un filtro de permisos como (negado, «Objeto WHERE condicion»), del que se
    toma AssigneeId.

    En permission sets, «tiene» mira solo la asignacion directa y «contiene» suma
    la que llega dentro de un permission set group.
    """
    if clave == "grupos":
        if modo not in MODOS_GRUPO:
            raise ValueError("Modo no válido para permission set groups: {}".format(modo))
        lista = _ids(ids, "permission set group")
        return modo.startswith("no_"), "PermissionSetAssignment WHERE PermissionSetGroupId IN ({})".format(lista)

    if modo not in MODOS_PERMISO:
        raise ValueError("Modo no válido para permission sets: {}".format(modo))
    lista = _ids(ids, "permission set")
    filtro = "PermissionSetId IN ({})".format(lista)
    if modo in ("contiene", "no_contiene"):
        contenedores = _grupos_que_contienen(org, lista)
        if contenedores:
            filtro = "({} OR PermissionSetGroupId IN ({}))".format(
                filtro, ", ".join(_texto(g) for g in contenedores)
            )
    return modo.startswith("no_"), "PermissionSetAssignment WHERE " + filtro


def _filtros_permisos(org, filtros):
    """
    Todos los filtros sobre asignaciones: los desplegables de permisos y de
    licencias de permission set, las condiciones de permisos y el boton de
    Modify All Data. Cada uno es una subconsulta, y SOQL admite dos.
    """
    modos = filtros.get("modos") or {}
    resultado = []
    # Las asignaciones incluyen el permission set que es el perfil por dentro y el
    # que agrega cada grupo, asi que esta subconsulta cubre las tres vias.
    if filtros.get("modificarTodo"):
        resultado.append((False, "PermissionSetAssignment WHERE PermissionSet.PermissionsModifyAllData = true"))
    def por_licencia_ps(ids, modo):
        licencias = _ids(ids, "licencia de permission set")
        if not licencias:
            return
        if modo not in MODOS_GRUPO:
            raise ValueError("Modo no válido para licencias de permission set: {}".format(modo))
        resultado.append((
            modo == "no_tiene",
            "PermissionSetLicenseAssign WHERE PermissionSetLicenseId IN ({})".format(licencias),
        ))

    por_licencia_ps(filtros.get("licenciasPs"), modos.get("licenciasPs") or "tiene")
    for condicion in filtros.get("condiciones") or []:
        if condicion.get("campo") == CONDICION_LICENCIAS_PS:
            por_licencia_ps(condicion.get("valores"), condicion.get("operador"))
    for clave in ("grupos", "permisos"):
        if filtros.get(clave):
            resultado.append(_filtro_permisos(org, clave, filtros[clave], modos.get(clave) or "tiene"))
    for condicion in filtros.get("condiciones") or []:
        if condicion.get("campo") in (COLUMNA_GRUPOS, COLUMNA_PERMISOS) and condicion.get("valores"):
            clave = "grupos" if condicion["campo"] == COLUMNA_GRUPOS else "permisos"
            resultado.append(_filtro_permisos(org, clave, condicion["valores"], condicion.get("operador")))
    return resultado


def _subconsulta(negado, filtro):
    return "Id {} (SELECT AssigneeId FROM {})".format(
        "NOT IN" if negado else "IN", filtro
    )


def _where(filtros, definiciones, permisos):
    """WHERE de User con los filtros de campos y los filtros de permisos dados."""
    condiciones = []

    texto = (filtros.get("texto") or "").strip()
    if texto:
        condiciones.append("(Name LIKE {0} OR Username LIKE {0} OR Email LIKE {0})".format(_patron(texto)))

    # Usuarios concretos: es como se piden los seleccionados para exportarlos.
    ids = _ids(filtros.get("ids"), "usuario")
    if ids:
        if len(filtros["ids"]) > LOTE_IDS:
            raise ValueError("Como mucho {} usuarios por petición.".format(LOTE_IDS))
        condiciones.append("Id IN ({})".format(ids))

    if filtros.get("activos"):
        condiciones.append("IsActive = true")

    tipos = [t for t in filtros.get("tipos") or [] if t]
    if tipos:
        for tipo in tipos:
            if not re.match(r"^[A-Za-z]+$", tipo):
                raise ValueError("Tipo de usuario no válido: {}".format(tipo))
        modo = (filtros.get("modos") or {}).get("tipos") or "tiene"
        if modo not in MODOS_GRUPO:
            raise ValueError("Modo no válido para tipos de usuario: {}".format(modo))
        condiciones.append("UserType {} ({})".format(
            "NOT IN" if modo == "no_tiene" else "IN", ", ".join(_texto(t) for t in tipos)
        ))

    # Perfil de administrador: el estandar y cualquier copia suya, que son los que
    # dan Modify All Data desde el propio perfil.
    if filtros.get("perfilAdmin"):
        condiciones.append("Profile.PermissionsModifyAllData = true")

    modos = filtros.get("modos") or {}

    # Rol. NOT IN deja fuera a los que no tienen rol, asi que los vacios se
    # piden o se excluyen a mano, con o sin «Sin rol» marcado.
    roles = [r for r in filtros.get("roles") or [] if r]
    if roles:
        sin_rol = SIN_ROL in roles
        lista = _ids([r for r in roles if r != SIN_ROL], "rol")
        modo = modos.get("roles") or "tiene"
        if modo not in MODOS_GRUPO:
            raise ValueError("Modo no válido para roles: {}".format(modo))
        if modo == "tiene":
            partes = (["UserRoleId IN ({})".format(lista)] if lista else []) + (["UserRoleId = null"] if sin_rol else [])
            condiciones.append("(" + " OR ".join(partes) + ")")
        else:
            partes = (["UserRoleId NOT IN ({})".format(lista)] if lista else []) + (["UserRoleId != null"] if sin_rol else [])
            condicion = " AND ".join(partes)
            condiciones.append("({})".format(condicion if sin_rol else "UserRoleId = null OR " + condicion))

    def por_licencia(ids, modo):
        licencias = _ids(ids, "licencia de usuario")
        if not licencias:
            return
        if modo not in MODOS_GRUPO:
            raise ValueError("Modo no válido para licencias de usuario: {}".format(modo))
        condiciones.append("Profile.UserLicenseId {} ({})".format("NOT IN" if modo == "no_tiene" else "IN", licencias))

    por_licencia(filtros.get("licencias"), modos.get("licencias") or "tiene")
    for condicion in filtros.get("condiciones") or []:
        if condicion.get("campo") == CONDICION_LICENCIAS:
            por_licencia(condicion.get("valores"), condicion.get("operador"))

    def por_perfil(ids, modo):
        lista = _ids(ids, "perfil")
        if not lista:
            return
        if modo not in MODOS_GRUPO:
            raise ValueError("Modo no válido para perfiles: {}".format(modo))
        condiciones.append("ProfileId {} ({})".format("NOT IN" if modo == "no_tiene" else "IN", lista))

    por_perfil(filtros.get("perfiles"), (filtros.get("modos") or {}).get("perfiles") or "tiene")
    for condicion in filtros.get("condiciones") or []:
        if condicion.get("campo") == CONDICION_PERFILES:
            por_perfil(condicion.get("valores"), condicion.get("operador"))

    for negado, filtro in permisos:
        condiciones.append(_subconsulta(negado, filtro))

    for condicion in filtros.get("condiciones") or []:
        if condicion.get("campo") not in (
            COLUMNA_GRUPOS, COLUMNA_PERMISOS, CONDICION_PERFILES, CONDICION_LICENCIAS, CONDICION_LICENCIAS_PS
        ):
            condiciones.append(_condicion(condicion, definiciones))

    return (" WHERE " + " AND ".join(condiciones)) if condiciones else ""


def _resolver_en_python(org, filtros, definiciones, permisos):
    """
    Ids que cumplen todos los filtros cuando hay mas filtros de permisos de los
    que admite una SOQL, en el orden del listado.

    En la SOQL entran los que acotan («tiene»): cuanto mas corta la lista que
    devuelve, menos hay que cruzar aqui. Los demas se cruzan con las asignaciones.
    """
    ordenados = sorted(permisos, key=lambda par: par[0])
    en_soql, resto = ordenados[:MAX_SUBCONSULTAS], ordenados[MAX_SUBCONSULTAS:]

    ids = [r["Id"] for r in org.consultar(
        "SELECT Id FROM User{} ORDER BY Name, Id".format(_where(filtros, definiciones, en_soql))
    )]

    for negado, filtro in resto:
        if not ids:
            break
        asignados = {
            r["AssigneeId"] for r in org.consultar(
                "SELECT AssigneeId FROM {}".format(filtro)
            )
        }
        ids = [i for i in ids if (i in asignados) != negado]

    return ids


def _guardar_lista(ids):
    token = uuid.uuid4().hex
    with _cerrojo:
        _listas[token] = ids
        while len(_listas) > MAX_LISTAS:
            _listas.popitem(last=False)
    return token


def _pagina_de_lista(org, token, numero, reales):
    """Una pagina de una lista resuelta en Python, con las columnas pedidas."""
    with _cerrojo:
        ids = _listas.get(token)
    if ids is None:
        raise ValueError("La paginación ha caducado (se reinició el servidor o hay búsquedas más nuevas). "
                         "Vuelve a la primera página.")

    tramo = ids[numero * TAMANO_PAGINA:(numero + 1) * TAMANO_PAGINA]
    campos = ", ".join(["Id"] + [c for c in reales if c != "Id"])
    por_id = {}
    for inicio in range(0, len(tramo), LOTE_IDS):
        lote = ", ".join(_texto(i) for i in tramo[inicio:inicio + LOTE_IDS])
        for registro in org.consultar("SELECT {} FROM User WHERE Id IN ({})".format(campos, lote)):
            por_id[registro["Id"]] = registro

    hay_mas = (numero + 1) * TAMANO_PAGINA < len(ids)
    return {
        "registros": [por_id[i] for i in tramo if i in por_id],
        "total": len(ids),
        "siguiente": "py:{}:{}".format(token, numero + 1) if hay_mas else None,
    }


def _valor(registro, columna):
    """Valor de una columna, siguiendo la relacion si es `Profile.Name`."""
    actual = registro
    for parte in columna.split("."):
        if not isinstance(actual, dict):
            return None
        actual = actual.get(parte)
    return actual


def _asignaciones(org, ids):
    """{usuario: (grupos, permission sets)} asignados a cada usuario, por etiqueta."""
    resultado = {i: ([], []) for i in ids}
    for inicio in range(0, len(ids), LOTE_IDS):
        lote = ", ".join(_texto(i) for i in ids[inicio:inicio + LOTE_IDS])
        registros = org.consultar(
            "SELECT AssigneeId, PermissionSetGroupId, PermissionSetGroup.MasterLabel, PermissionSet.Label "
            "FROM PermissionSetAssignment "
            "WHERE AssigneeId IN ({}) AND PermissionSet.IsOwnedByProfile = false".format(lote)
        )
        for registro in registros:
            grupos, permisos = resultado[registro["AssigneeId"]]
            if registro.get("PermissionSetGroupId"):
                grupos.append((registro.get("PermissionSetGroup") or {}).get("MasterLabel") or "")
            else:
                permisos.append((registro.get("PermissionSet") or {}).get("Label") or "")

    return {i: (sorted(g, key=str.lower), sorted(p, key=str.lower)) for i, (g, p) in resultado.items()}


# Tope de asignaciones por peticion: usuarios por permisos. Por encima casi
# seguro es un error de seleccion, y deshacerlo seria a mano.
MAX_ASIGNACIONES = 5000


def _validar_cambio(datos):
    """Tipo, usuarios y permisos de una asignacion o retirada, ya validados."""
    tipo = datos.get("tipo")
    if tipo not in ("permisos", "grupos"):
        raise ValueError("Tipo de asignación no válido: {}".format(tipo))
    usuarios = [i for i in datos.get("usuarios") or [] if i]
    permisos = [i for i in datos.get("permisos") or [] if i]
    _ids(usuarios, "usuario")
    _ids(permisos, "permission set" if tipo == "permisos" else "permission set group")
    if not usuarios or not permisos:
        raise ValueError("Hacen falta usuarios y permisos para asignar.")
    if len(usuarios) * len(permisos) > MAX_ASIGNACIONES:
        raise ValueError("Son {} asignaciones; el tope por vez es {}.".format(
            len(usuarios) * len(permisos), MAX_ASIGNACIONES
        ))
    return tipo, usuarios, permisos


def _con_nombres(org, errores):
    """Añade a cada error el nombre del usuario, para leer la lista sin ir Id a Id."""
    nombres = {}
    afectados = sorted({e["usuario"] for e in errores})
    for inicio in range(0, len(afectados), LOTE_IDS):
        lote = ", ".join(_texto(i) for i in afectados[inicio:inicio + LOTE_IDS])
        for registro in org.consultar("SELECT Id, Name FROM User WHERE Id IN ({})".format(lote)):
            nombres[registro["Id"]] = registro["Name"]
    for error in errores:
        error["nombre"] = nombres.get(error["usuario"], error["usuario"])
    return errores


def _mensaje(fallos):
    return "; ".join(f.get("message") or f.get("statusCode") or "" for f in fallos or []) or "sin mensaje"


def cambiar_permisos(org, datos):
    """
    Asigna o quita permission sets o permission set groups a una lista de
    usuarios: las unicas escrituras de las paginas en vivo.

    `datos` trae `accion` («asignar» o «quitar»), `tipo` («permisos» o «grupos»),
    `usuarios` y `permisos`, listas de Ids.
    """
    if datos.get("accion") == "quitar":
        return _quitar(org, datos)
    if datos.get("accion") != "asignar":
        raise ValueError("Acción no válida: {}".format(datos.get("accion")))
    return _asignar(org, datos)


def _asignar(org, datos):
    """Lo que el usuario ya tenia no es un error: se cuenta aparte."""
    tipo, usuarios, permisos = _validar_cambio(datos)
    campo = "PermissionSetId" if tipo == "permisos" else "PermissionSetGroupId"
    pares = [(usuario, permiso) for usuario in usuarios for permiso in permisos]
    resultados = org.crear_registros([
        {"attributes": {"type": "PermissionSetAssignment"}, "AssigneeId": usuario, campo: permiso}
        for usuario, permiso in pares
    ])

    creadas, ya_tenian, errores = 0, 0, []
    for (usuario, permiso), resultado in zip(pares, resultados):
        if resultado.get("success"):
            creadas += 1
            continue
        fallos = resultado.get("errors") or []
        if any(f.get("statusCode") == "DUPLICATE_VALUE" for f in fallos):
            ya_tenian += 1
            continue
        errores.append({"usuario": usuario, "permiso": permiso, "mensaje": _mensaje(fallos)})

    return {
        "accion": "asignar", "total": len(pares), "hechas": creadas,
        "sinCambio": ya_tenian, "errores": _con_nombres(org, errores),
    }


def _quitar(org, datos):
    """
    Borra las asignaciones directas. Un permission set que el usuario recibe
    dentro de un grupo no tiene asignacion propia, asi que no se puede quitar
    aqui: hay que quitarle el grupo.
    """
    tipo, usuarios, permisos = _validar_cambio(datos)
    campo = "PermissionSetId" if tipo == "permisos" else "PermissionSetGroupId"

    existentes = []
    lista_permisos = ", ".join(_texto(p) for p in permisos)
    for inicio in range(0, len(usuarios), LOTE_IDS):
        lote = ", ".join(_texto(i) for i in usuarios[inicio:inicio + LOTE_IDS])
        existentes.extend(org.consultar(
            "SELECT Id, AssigneeId, {0} FROM PermissionSetAssignment "
            "WHERE AssigneeId IN ({1}) AND {0} IN ({2})".format(campo, lote, lista_permisos)
        ))

    resultados = org.borrar_registros([r["Id"] for r in existentes]) if existentes else []
    quitadas, errores = 0, []
    for registro, resultado in zip(existentes, resultados):
        if resultado.get("success"):
            quitadas += 1
        else:
            errores.append({
                "usuario": registro["AssigneeId"], "permiso": registro[campo],
                "mensaje": _mensaje(resultado.get("errors")),
            })

    total = len(usuarios) * len(permisos)
    return {
        "accion": "quitar", "total": total, "hechas": quitadas,
        "sinCambio": total - len(existentes), "errores": _con_nombres(org, errores),
    }


def _recuento_asignaciones(org, usuarios):
    """
    Cuantos de esos usuarios tienen asignado directamente cada permission set y
    cada grupo: es lo que se puede quitar. Como mucho LOTE_IDS usuarios por vez.
    """
    usuarios = [i for i in usuarios or [] if i]
    if len(usuarios) > LOTE_IDS:
        raise ValueError("Como mucho {} usuarios por petición.".format(LOTE_IDS))
    lote = _ids(usuarios, "usuario")
    permisos, grupos = {}, {}
    if lote:
        for registro in org.consultar(
            "SELECT PermissionSetId, PermissionSetGroupId FROM PermissionSetAssignment "
            "WHERE AssigneeId IN ({}) AND PermissionSet.IsOwnedByProfile = false".format(lote)
        ):
            # La asignacion de un grupo apunta tambien al permission set que lo
            # agrega por dentro: cuenta como grupo, no como permission set.
            if registro.get("PermissionSetGroupId"):
                grupos[registro["PermissionSetGroupId"]] = grupos.get(registro["PermissionSetGroupId"], 0) + 1
            else:
                permisos[registro["PermissionSetId"]] = permisos.get(registro["PermissionSetId"], 0) + 1
    return {"permisos": permisos, "grupos": grupos}


def detalle(org, cual):
    """
    Una pagina del listado. `cual` es un JSON con `filtros`, `columnas` y, para
    pasar de pagina, el `cursor` que devolvio la anterior.
    """
    peticion = json.loads(cual or "{}")
    if "asignacionesDe" in peticion:
        return _recuento_asignaciones(org, peticion["asignacionesDe"])
    disponibles = _columnas(org)

    columnas = [c for c in peticion.get("columnas") or COLUMNAS_POR_DEFECTO if c in disponibles]
    if not columnas:
        columnas = ["Name"]
    virtuales = [c for c in columnas if c.startswith("__")]
    reales = [c for c in columnas if not c.startswith("__")]

    cursor = peticion.get("cursor")
    propio = PATRON_CURSOR_PROPIO.match(cursor or "")
    try:
        if propio:
            pagina = _pagina_de_lista(org, propio.group(1), int(propio.group(2)), reales)
        elif cursor:
            if not PATRON_CURSOR.match(cursor):
                raise ValueError("Cursor de paginación no válido.")
            pagina = org.consultar_pagina(cursor=cursor)
        else:
            filtros = peticion.get("filtros") or {}
            definiciones = {campo["name"]: campo for campo in _campos(org)}
            permisos = _filtros_permisos(org, filtros)

            if len(permisos) > MAX_SUBCONSULTAS:
                token = _guardar_lista(_resolver_en_python(org, filtros, definiciones, permisos))
                pagina = _pagina_de_lista(org, token, 0, reales)
            else:
                soql = "SELECT {} FROM User{} ORDER BY Name, Id".format(
                    ", ".join(["Id"] + [c for c in reales if c != "Id"]),
                    _where(filtros, definiciones, permisos),
                )
                pagina = org.consultar_pagina(soql=soql, tamano=TAMANO_PAGINA)
    except ErrorCli as error:
        if "INVALID_QUERY_LOCATOR" in str(error):
            raise ValueError("La paginación ha caducado (15 minutos sin usarla). Vuelve a la primera página.")
        raise

    registros = pagina["registros"]
    filas = [dict({"Id": r["Id"]}, **{c: _valor(r, c) for c in reales}) for r in registros]

    if virtuales and filas:
        asignaciones = _asignaciones(org, [f["Id"] for f in filas])
        for fila in filas:
            grupos, permisos = asignaciones.get(fila["Id"], ([], []))
            if COLUMNA_GRUPOS in virtuales:
                fila[COLUMNA_GRUPOS] = grupos
            if COLUMNA_PERMISOS in virtuales:
                fila[COLUMNA_PERMISOS] = permisos

    return {
        "columnas": columnas,
        "filas": filas,
        "total": pagina["total"],
        "siguiente": pagina["siguiente"],
    }
