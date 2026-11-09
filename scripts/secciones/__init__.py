"""
Registro de las paginas en vivo del documentator.

Cada pagina (`/vivo/<id>`) apila sus secciones en el orden en que aparecen aqui.

Cada modulo de seccion expone:
    ID       identificador usado como ancla y como clave del renderizador en JS
    TITULO   texto de la cabecera de la seccion
    recoger(org) -> dict serializable con los datos de la seccion
y, si sabe listar algo bajo demanda, `detalle(org, cual)`.

Una pagina con `guardar` no consulta el org en cada carga: enseña lo ultimo
guardado en disco de ese org, y solo «Actualizar» vuelve a consultarlo. Es para
las que tardan demasiado en recogerse.
"""

from . import usuarios
from . import visor_grupos
from . import visor_permisos
from . import visor_flows
from . import licencias
from . import omnichannel

PAGINAS = [
    {"id": "usuarios", "titulo": "Gestión de usuarios", "modulos": [usuarios, visor_grupos, visor_permisos]},
    {"id": "licencias", "titulo": "Licencias", "modulos": [licencias]},
    {"id": "flows", "titulo": "Flows", "modulos": [visor_flows]},
    {"id": "omnichannel", "titulo": "Omni-Channel", "modulos": [omnichannel], "guardar": True},
]

MODULOS = [modulo for pagina in PAGINAS for modulo in pagina["modulos"]]


def pagina(identificador):
    """Devuelve la pagina con ese id, o None si no se reconoce."""
    for candidata in PAGINAS:
        if candidata["id"] == identificador:
            return candidata
    return None


def con_detalle(identificador):
    """Modulo de esa seccion, si ofrece listados bajo demanda."""
    for modulo in MODULOS:
        if modulo.ID == identificador and hasattr(modulo, "detalle"):
            return modulo
    raise ValueError("La sección '{}' no ofrece listados.".format(identificador))
