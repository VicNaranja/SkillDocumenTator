"""
Seccion "Visor de permission sets y perfiles": eliges uno y muestra lo que
concede — acceso a objetos y campos, permisos de sistema y acceso a clases,
paginas, custom permissions, apps y pestanas —, que en Salesforce hay que ir
mirando pantalla a pantalla.

Un perfil es por dentro un permission set (`IsOwnedByProfile`), y un permission
set group tiene otro, calculado, con la suma de lo que dan sus miembros ya
descontado el silenciado. Las dos cosas se leen igual que un permission set
normal, asi que este visor sirve para las tres.

Al abrir la pestana solo se recoge la lista. La ficha se pide por /api/detalle
al elegir uno, y los campos de cada objeto, al desplegar ese objeto: un perfil
de administrador tiene permisos sobre miles de campos.
"""

import concurrent.futures
import json
import re

ID = "visor-permisos"
TITULO = "Visor de permission sets y perfiles"

PATRON_ID = re.compile(r"^[a-zA-Z0-9]{15,18}$")
PATRON_OBJETO = re.compile(r"^[A-Za-z0-9_]+$")

# Campos de permisos de sistema por consulta: son casi novecientos y no caben en
# la URL de una sola.
LOTE_CAMPOS = 150
LOTE_IDS = 300

PERMISOS_OBJETO = [
    ("PermissionsRead", "leer"),
    ("PermissionsCreate", "crear"),
    ("PermissionsEdit", "editar"),
    ("PermissionsDelete", "borrar"),
    ("PermissionsViewAllRecords", "verTodos"),
    ("PermissionsModifyAllRecords", "modificarTodos"),
]

# Accesos de SetupEntityAccess que se pueden nombrar: (titulo, objeto, campo de
# nombre, tooling). Los demas tipos salen solo con su recuento.
ENTIDADES = {
    "ApexClass": ("Clases Apex", "ApexClass", "Name", False),
    "ApexPage": ("Páginas Visualforce", "ApexPage", "Name", False),
    "CustomPermission": ("Custom permissions", "CustomPermission", "MasterLabel", False),
    "ConnectedApplication": ("Connected apps", "ConnectedApplication", "Name", False),
    "TabSet": ("Apps", "AppMenuItem", "Label", False),
    "FlowDefinition": ("Flows", "FlowDefinitionView", "Label", False),
    "CustomEntityDefinition": ("Custom settings y custom metadata", "CustomObject", "DeveloperName", True),
    "ServicePresenceStatus": ("Estados de presencia de Omni-Channel", "ServicePresenceStatus", "MasterLabel", False),
    "BotDefinition": ("Agentes y bots", "BotDefinition", "MasterLabel", False),
    "ExternalCredentialParameter": ("Parámetros de credenciales externas", None, None, False),
}

# Campo por el que se buscan los registros de cada tipo: AppMenuItem y
# FlowDefinitionView no se buscan por Id.
CLAVE_ENTIDAD = {"AppMenuItem": "ApplicationId", "FlowDefinitionView": "DurableId"}

# AppMenuItem no admite filtrar por ApplicationId: se trae entero, que son pocas.
SIN_FILTRO = {"AppMenuItem": "Type = 'TabSet'"}


def _texto(valor):
    return "'" + str(valor).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _nombre_api(registro, campo):
    prefijo = registro.get("NamespacePrefix")
    return (prefijo + "__" if prefijo else "") + (registro.get(campo) or "")


def recoger(org):
    """Lista de permission sets y perfiles, con cuantos usuarios tiene cada uno."""
    registros = org.consultar(
        "SELECT Id, Label, Name, NamespacePrefix, IsOwnedByProfile, Profile.Name, License.Name "
        "FROM PermissionSet WHERE PermissionSetGroupId = null AND Type != 'Muting' ORDER BY Label"
    )
    usuarios = {
        r["PermissionSetId"]: r["n"] for r in org.consultar(
            "SELECT PermissionSetId, COUNT(Id) n FROM PermissionSetAssignment "
            "WHERE PermissionSetGroupId = null GROUP BY PermissionSetId"
        )
    }

    lista = []
    for registro in registros:
        perfil = registro.get("IsOwnedByProfile")
        lista.append({
            "id": registro["Id"],
            "etiqueta": (registro.get("Profile") or {}).get("Name") if perfil else registro.get("Label"),
            "nombre": "" if perfil else _nombre_api(registro, "Name"),
            "tipo": "perfil" if perfil else "permiso",
            "licencia": (registro.get("License") or {}).get("Name") or "",
            "usuarios": usuarios.get(registro["Id"], 0),
        })
    lista.sort(key=lambda p: (p["etiqueta"] or "").lower())

    return {
        "id": ID,
        "titulo": TITULO,
        "descripcion": (
            "Elige un permission set o un perfil para ver lo que concede. Los objetos se despliegan "
            "para ver sus campos. Un campo que no aparece no tiene acceso concedido aquí."
        ),
        "permisos": lista,
    }


def _validar(identificador):
    identificador = (identificador or "").strip()
    if not PATRON_ID.match(identificador):
        raise ValueError("Id no válido: {}".format(identificador))
    return identificador


def detalle(org, cual):
    """
    Ficha de un permission set. `cual` es un JSON con `permiso` y, para los campos
    de un objeto, `objeto`.
    """
    peticion = json.loads(cual or "{}")
    permiso = _validar(peticion.get("permiso"))
    if peticion.get("objeto"):
        return _campos_de_objeto(org, permiso, peticion["objeto"])
    return _ficha(org, permiso)


def _ficha(org, permiso):
    cabecera = org.consultar(
        "SELECT Id, Label, Name, NamespacePrefix, Description, IsOwnedByProfile, Profile.Name, "
        "License.Name, Type, PermissionSetGroupId, PermissionSetGroup.MasterLabel "
        "FROM PermissionSet WHERE Id = '{}'".format(permiso)
    )
    if not cabecera:
        raise ValueError("No existe el permission set {}.".format(permiso))
    cabecera = cabecera[0]

    if cabecera.get("PermissionSetGroupId"):
        tipo, etiqueta = "grupo", (cabecera.get("PermissionSetGroup") or {}).get("MasterLabel") or ""
    elif cabecera.get("IsOwnedByProfile"):
        tipo, etiqueta = "perfil", (cabecera.get("Profile") or {}).get("Name") or ""
    else:
        tipo, etiqueta = "permiso", cabecera.get("Label") or ""

    grupos = [
        {"id": r["PermissionSetGroupId"], "etiqueta": (r.get("PermissionSetGroup") or {}).get("MasterLabel") or ""}
        for r in org.consultar(
            "SELECT PermissionSetGroupId, PermissionSetGroup.MasterLabel FROM PermissionSetGroupComponent "
            "WHERE PermissionSetId = '{}'".format(permiso)
        )
    ]
    usuarios = org.consultar(
        "SELECT COUNT(Id) n FROM PermissionSetAssignment WHERE PermissionSetId = '{}'".format(permiso)
    )

    # Las cuatro partes no dependen unas de otras: en paralelo, el coste es el de
    # la mas lenta. Con un perfil de administrador son miles de registros.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ejecutor:
        objetos, sistema, accesos, pestanas = [
            ejecutor.submit(funcion, org, permiso) for funcion in (_objetos, _sistema, _accesos, _pestanas)
        ]

    return {
        "id": permiso,
        "tipo": tipo,
        "etiqueta": etiqueta,
        "nombre": "" if tipo != "permiso" else _nombre_api(cabecera, "Name"),
        "descripcion": cabecera.get("Description") or "",
        "licencia": (cabecera.get("License") or {}).get("Name") or "",
        "usuarios": usuarios[0]["n"] if usuarios else 0,
        "grupos": grupos,
        "objetos": objetos.result(),
        "sistema": sistema.result(),
        "accesos": accesos.result(),
        "pestanas": pestanas.result(),
    }


def _objetos(org, permiso):
    """Objetos con algun permiso, con su etiqueta y cuantos campos lee y edita."""
    campos = {}
    for registro in org.consultar(
        "SELECT SobjectType, PermissionsEdit, COUNT(Id) n FROM FieldPermissions "
        "WHERE ParentId = '{}' GROUP BY SobjectType, PermissionsEdit".format(permiso)
    ):
        cuenta = campos.setdefault(registro["SobjectType"], {"lectura": 0, "edicion": 0})
        cuenta["edicion" if registro["PermissionsEdit"] else "lectura"] += registro["n"]

    etiquetas = org.etiquetas_objetos()
    objetos = []
    for registro in org.consultar(
        "SELECT SobjectType, {} FROM ObjectPermissions WHERE ParentId = '{}'".format(
            ", ".join(campo for campo, _ in PERMISOS_OBJETO), permiso
        )
    ):
        nombre = registro["SobjectType"]
        fila = {"objeto": nombre, "etiqueta": etiquetas.get(nombre, nombre).strip()}
        fila.update({clave: bool(registro.get(campo)) for campo, clave in PERMISOS_OBJETO})
        fila["campos"] = campos.pop(nombre, {"lectura": 0, "edicion": 0})
        objetos.append(fila)

    # Campos con permiso en objetos sin permiso de objeto: no dan acceso, pero
    # existen, y esconderlos haria creer que no estan.
    for nombre, cuenta in campos.items():
        fila = {"objeto": nombre, "etiqueta": etiquetas.get(nombre, nombre).strip(), "campos": cuenta}
        fila.update({clave: False for _, clave in PERMISOS_OBJETO})
        objetos.append(fila)

    objetos.sort(key=lambda o: o["etiqueta"].lower())
    return objetos


def _campos_de_objeto(org, permiso, objeto):
    """Campos de un objeto con permiso, con su etiqueta, para desplegar su fila."""
    if not PATRON_OBJETO.match(objeto or ""):
        raise ValueError("Objeto no válido: {}".format(objeto))

    try:
        etiquetas = {c["name"]: c.get("label") or c["name"] for c in org.describir(objeto)}
    except Exception:
        # Hay objetos sin describe para este usuario; los campos salen con su nombre.
        etiquetas = {}

    campos = []
    for registro in org.consultar(
        "SELECT Field, PermissionsRead, PermissionsEdit FROM FieldPermissions "
        "WHERE ParentId = '{}' AND SobjectType = '{}'".format(permiso, objeto)
    ):
        nombre = registro["Field"].split(".", 1)[-1]
        campos.append({
            "campo": nombre,
            "etiqueta": etiquetas.get(nombre, nombre),
            "editar": bool(registro.get("PermissionsEdit")),
        })
    campos.sort(key=lambda c: c["etiqueta"].lower())
    return {"objeto": objeto, "campos": campos}


def _sistema(org, permiso):
    """Permisos de sistema concedidos, con su etiqueta, ordenados."""
    definiciones = [
        c for c in org.describir("PermissionSet")
        if c["name"].startswith("Permissions") and c.get("type") == "boolean"
    ]
    concedidos = []
    for inicio in range(0, len(definiciones), LOTE_CAMPOS):
        lote = definiciones[inicio:inicio + LOTE_CAMPOS]
        registro = org.consultar("SELECT {} FROM PermissionSet WHERE Id = '{}'".format(
            ", ".join(c["name"] for c in lote), permiso
        ))[0]
        concedidos.extend(
            {"campo": c["name"], "etiqueta": c.get("label") or c["name"]}
            for c in lote if registro.get(c["name"])
        )
    concedidos.sort(key=lambda c: c["etiqueta"].lower())
    return concedidos


def _accesos(org, permiso):
    """Clases, paginas, custom permissions, apps y demas, agrupados por tipo."""
    por_tipo = {}
    for registro in org.consultar(
        "SELECT SetupEntityId, SetupEntityType FROM SetupEntityAccess WHERE ParentId = '{}'".format(permiso)
    ):
        por_tipo.setdefault(registro["SetupEntityType"], []).append(registro["SetupEntityId"])

    grupos = []
    for tipo, ids in por_tipo.items():
        titulo, objeto, campo, tooling = ENTIDADES.get(tipo, (tipo, None, None, False))
        nombres = []
        if objeto:
            clave = CLAVE_ENTIDAD.get(objeto, "Id")
            encontrados = {}
            if objeto in SIN_FILTRO:
                consultas = ["SELECT {}, {} FROM {} WHERE {}".format(clave, campo, objeto, SIN_FILTRO[objeto])]
            else:
                consultas = [
                    "SELECT {0}, {1} FROM {2} WHERE {0} IN ({3})".format(
                        clave, campo, objeto, ", ".join(_texto(i) for i in ids[inicio:inicio + LOTE_IDS])
                    )
                    for inicio in range(0, len(ids), LOTE_IDS)
                ]
            try:
                for consulta in consultas:
                    for r in org.consultar(consulta, tooling=tooling):
                        encontrados[r[clave][:15]] = (r.get(campo) or r[clave]).strip()
            except Exception:
                # Un tipo que este usuario no puede consultar sale con sus Ids.
                encontrados = {}
            # Lo que no se encuentra (apps internas que no salen en el menu, por
            # ejemplo) se lista con su Id para que el recuento cuadre.
            nombres = [encontrados.get(i[:15]) or "(sin nombre) " + i for i in ids]
        grupos.append({
            "tipo": tipo,
            "titulo": titulo,
            "total": len(ids),
            "nombres": sorted(nombres, key=str.lower),
        })
    grupos.sort(key=lambda g: g["titulo"].lower())
    return grupos


def _pestanas(org, permiso):
    """Pestanas con visibilidad configurada: «Visible», «Disponible» u «Oculta»."""
    textos = {"DefaultOn": "Visible", "Visible": "Visible", "DefaultOff": "Disponible", "Available": "Disponible"}
    pestanas = [
        {"nombre": r["Name"], "visibilidad": textos.get(r.get("Visibility"), r.get("Visibility") or "")}
        for r in org.consultar(
            "SELECT Name, Visibility FROM PermissionSetTabSetting WHERE ParentId = '{}'".format(permiso)
        )
    ]
    pestanas.sort(key=lambda p: p["nombre"].lower())
    return pestanas
