"""
Seccion "Visor de permission set groups": eliges un grupo y muestra los
permission sets que lo forman, incluido el de silenciado si lo tiene.

Al abrir la pestana solo se recoge la lista de grupos, con cuantos usuarios
tiene asignado cada uno. La composicion de un grupo se pide aparte, por
/api/detalle, cuando se elige.
"""

import json
import re

ID = "visor-grupos"
TITULO = "Visor de permission set groups"

PATRON_ID = re.compile(r"^[a-zA-Z0-9]{15,18}$")


def _nombre_api(registro, campo):
    prefijo = registro.get("NamespacePrefix")
    return (prefijo + "__" if prefijo else "") + (registro.get(campo) or "")


def recoger(org):
    """Lista de grupos con su estado y sus usuarios asignados."""
    grupos = org.consultar(
        "SELECT Id, MasterLabel, DeveloperName, NamespacePrefix, Status, Description "
        "FROM PermissionSetGroup ORDER BY MasterLabel"
    )
    asignados = {
        r["PermissionSetGroupId"]: r["n"] for r in org.consultar(
            "SELECT PermissionSetGroupId, COUNT(Id) n FROM PermissionSetAssignment "
            "WHERE PermissionSetGroupId != null GROUP BY PermissionSetGroupId"
        )
    }

    return {
        "id": ID,
        "titulo": TITULO,
        "descripcion": (
            "Elige un grupo para ver los permission sets que lo forman. Un permission set de "
            "silenciado (muting) quita permisos a los demás del grupo en vez de darlos."
        ),
        "grupos": [
            {
                "id": g["Id"],
                "etiqueta": g["MasterLabel"],
                "nombre": _nombre_api(g, "DeveloperName"),
                "estado": g.get("Status") or "",
                "descripcion": g.get("Description") or "",
                "usuarios": asignados.get(g["Id"], 0),
            }
            for g in grupos
        ],
    }


def detalle(org, cual):
    """Permission sets de un grupo. `cual` es un JSON con `grupo`, su Id."""
    grupo = (json.loads(cual or "{}").get("grupo") or "").strip()
    if not PATRON_ID.match(grupo):
        raise ValueError("Id de permission set group no válido: {}".format(grupo))

    componentes = org.consultar(
        "SELECT PermissionSetId, PermissionSet.Label, PermissionSet.Name, PermissionSet.NamespacePrefix, "
        "PermissionSet.Type, PermissionSet.Description, PermissionSet.License.Name "
        "FROM PermissionSetGroupComponent WHERE PermissionSetGroupId = '{}'".format(grupo)
    )

    permisos = []
    for componente in componentes:
        permiso = componente.get("PermissionSet") or {}
        permisos.append({
            "id": componente["PermissionSetId"],
            "etiqueta": permiso.get("Label") or "",
            "nombre": _nombre_api(permiso, "Name"),
            "silenciado": permiso.get("Type") == "Muting",
            "licencia": (permiso.get("License") or {}).get("Name") or "",
            "descripcion": permiso.get("Description") or "",
        })

    # El de silenciado va el ultimo: se lee como una excepcion a todo lo de encima.
    permisos.sort(key=lambda p: (p["silenciado"], p["etiqueta"].lower()))

    # Permission set que Salesforce calcula con todo lo que da el grupo, ya
    # descontado el silenciado: es lo que se abre en el visor de permisos.
    agregado = org.consultar("SELECT Id FROM PermissionSet WHERE PermissionSetGroupId = '{}'".format(grupo))
    return {"grupo": grupo, "permisos": permisos, "agregado": agregado[0]["Id"] if agregado else None}
