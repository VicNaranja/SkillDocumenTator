/* ------------------------------------------------------------------ *
 * Paginas en vivo del documentator: usuarios, licencias y flows.
 *
 * Cada pagina apila sus secciones bajo un selector de org. Los datos los
 * sirve servir_documentator.py, que consulta el org en cada carga.
 *
 * Para anadir una seccion: registra su funcion en RENDERIZADORES con la
 * misma clave que el ID del modulo Python.
 * ------------------------------------------------------------------ */

let DATOS = null;

/* ----------------------------- Utilidades ------------------------- */

function el(etiqueta, propiedades, hijos) {
  const nodo = document.createElement(etiqueta);
  Object.assign(nodo, propiedades || {});
  (hijos || []).forEach(function (hijo) {
    if (hijo === null || hijo === undefined) return;
    nodo.appendChild(typeof hijo === 'string' ? document.createTextNode(hijo) : hijo);
  });
  return nodo;
}

const num = function (valor) { return (valor || 0).toLocaleString('es-ES'); };

function pct(parte, total) {
  if (!total) return '—';
  return (100 * parte / total).toFixed(parte / total >= 0.995 || parte === 0 ? 0 : 1) + ' %';
}

function fechaCorta(iso) {
  if (!iso) return '';
  const p = iso.split('-');
  return p[2] + '/' + p[1] + '/' + p[0].slice(2);
}

/*
 * Igual, pero partiendo de un instante ISO completo en vez de un dia suelto, en
 * la zona horaria del navegador: cortar el ISO por caracteres daria el dia en UTC.
 */
function fechaCortaInstante(iso) {
  if (!iso) return '';
  const momento = new Date(iso);
  if (isNaN(momento.getTime())) return '';
  const dia = String(momento.getDate()).padStart(2, '0');
  const mes = String(momento.getMonth() + 1).padStart(2, '0');
  return dia + '/' + mes + '/' + String(momento.getFullYear()).slice(2);
}

/* ------------------------------ Tooltip --------------------------- */

const pista = document.getElementById('pista');

function conPista(nodo, texto) {
  nodo.addEventListener('mousemove', function (evento) {
    pista.textContent = typeof texto === 'function' ? texto() : texto;
    pista.style.left = evento.clientX + 'px';
    pista.style.top = evento.clientY + 'px';
    pista.classList.add('visible');
  });
  nodo.addEventListener('mouseleave', function () { pista.classList.remove('visible'); });
  return nodo;
}

/* ------------------------- Barras horizontales -------------------- */

function barras(filas, series, maximoFijo) {
  // Un máximo fijo evita que una escala de porcentaje se reescale sola: sin él,
  // el mayor valor siempre llenaría la barra y un 12 % parecería un 100 %.
  const maximo = maximoFijo || Math.max.apply(null, filas.map(function (f) {
    return Math.max.apply(null, series.map(function (s) { return f[s.clave] || 0; }));
  }).concat([1]));

  return el('div', { className: 'barras' }, filas.map(function (fila) {
    const pistas = series.map(function (serie) {
      const relleno = el('div', { className: 'barra-relleno' + (serie.secundaria ? ' secundaria' : '') });
      relleno.style.width = Math.max(2, 100 * (fila[serie.clave] || 0) / maximo) + '%';
      // Una fila puede pedir su propio color cuando significa algo distinto del
      // resto (un hueco, un error), no cuando solo es mas grande o mas pequena.
      if (fila.color) relleno.style.background = fila.color;
      const pistaBarra = el('div', { className: 'barra-pista' }, [el('div', { className: 'barra-fondo' }), relleno]);
      pistaBarra.style.height = series.length > 1 ? '11px' : '22px';
      conPista(pistaBarra, serie.etiqueta + ': ' + num(fila[serie.clave]) + ' — ' + fila.etiqueta);
      return pistaBarra;
    });

    return el('div', { className: 'barra-fila' }, [
      conPista(el('div', { className: 'barra-etiqueta', title: fila.etiqueta }, [fila.etiqueta]), fila.etiqueta),
      el('div', {}, pistas),
      el('div', { className: 'barra-valor' }, [
        fila.valorTexto || series.map(function (s) { return num(fila[s.clave]); }).join(' / ')
      ])
    ]);
  }));
}

/* ------------------------------ Vacio ----------------------------- */

function vacio(titulo, detalle) {
  return el('div', { className: 'vacio' }, [el('strong', {}, [titulo]), detalle]);
}

function fechaHora(iso) {
  if (!iso) return '';
  const momento = new Date(iso);
  if (isNaN(momento.getTime())) return '';
  return fechaCortaInstante(iso) + ' ' + String(momento.getHours()).padStart(2, '0') + ':' +
    String(momento.getMinutes()).padStart(2, '0');
}

/* ------------------------ Gestión de usuarios ---------------------- */

/*
 * Estado del listado de usuarios. Vive fuera del render porque cambiar de org o
 * pulsar «Actualizar» repinta la sección, y se llevaría por delante filtros y páginas. Se
 * reinicia al cambiar de org: los Ids de perfiles y permission sets no valen en otro.
 */
let USUARIOS = null;

const CLAVE_COLUMNAS_USUARIOS = 'documentator.usuarios.columnas';
const TIPOS_TEXTO_USUARIOS = ['string', 'email', 'phone', 'url', 'picklist', 'combobox', 'textarea', 'multipicklist'];

// Condiciones de perfil y de permisos: las claves de los permisos son las de las
// columnas de asignaciones del módulo Python.
const CONDICIONES_PERMISOS = {
  '__perfiles': { etiqueta: 'Perfil', modos: [['tiene', 'es'], ['no_tiene', 'no es']] },
  // La licencia de usuario es la del perfil; las de permission set se asignan aparte.
  '__licencias': { etiqueta: 'Licencia de usuario', modos: [['tiene', 'tiene'], ['no_tiene', 'no tiene']] },
  '__licenciasPs': { etiqueta: 'Licencia de permission set', modos: [['tiene', 'tiene'], ['no_tiene', 'no tiene']] },
  '__permisos': {
    etiqueta: 'Permission set',
    modos: [['tiene', 'tiene'], ['no_tiene', 'no tiene'], ['contiene', 'contiene'], ['no_contiene', 'no contiene']]
  },
  '__grupos': { etiqueta: 'Permission set group', modos: [['tiene', 'tiene'], ['no_tiene', 'no tiene']] }
};

function columnasGuardadas(seccion) {
  const disponibles = seccion.columnas.map(function (c) { return c.campo; });
  try {
    const guardadas = JSON.parse(localStorage.getItem(CLAVE_COLUMNAS_USUARIOS) || 'null');
    const validas = (guardadas || []).filter(function (c) { return disponibles.indexOf(c) !== -1; });
    if (validas.length) return validas;
  } catch (error) { /* sin persistencia */ }
  return seccion.columnasPorDefecto.slice();
}

function estadoUsuarios(seccion) {
  if (!USUARIOS || USUARIOS.org !== DATOS.org) {
    USUARIOS = {
      org: DATOS.org,
      filtros: {
        // Por defecto, los usuarios de trabajo: activos y de Salesforce (Standard).
        texto: '', activos: true, perfilAdmin: false, modificarTodo: false,
        tipos: seccion.tiposUsuario.some(function (t) { return t.id === 'Standard'; }) ? ['Standard'] : [],
        perfiles: [], roles: [], licencias: [], licenciasPs: [], grupos: [], permisos: [], condiciones: [],
        // Modo de cada desplegable. Los «no_…» restan: no cumplir ninguna de las marcadas.
        modos: {
          tipos: 'tiene', perfiles: 'tiene', roles: 'tiene', licencias: 'tiene', licenciasPs: 'tiene',
          grupos: 'tiene', permisos: 'tiene'
        }
      },
      columnas: columnasGuardadas(seccion),
      paginas: [],
      cursores: [null],
      // Lo que se está viendo: la página y su posición. Mientras viaja la
      // siguiente se queda a la vista, atenuada, en vez de vaciar la tabla.
      vista: null,
      // Usuarios marcados, por Id. Sobreviven al cambio de página y de filtros:
      // así se puede ir juntando una lista con varias búsquedas.
      seleccion: {},
      exportando: '',
      pintar: function () {},
      error: '',
      turno: 0,
      cargando: false
    };
  }

  // Un payload nuevo del mismo org es un «Actualizar»: se conservan filtros y
  // columnas, y el listado se vuelve a pedir. Uno sacado de la caché es el
  // mismo objeto, así que volver a un org ya consultado no lo dispara.
  if (USUARIOS.datos !== DATOS) {
    USUARIOS.datos = DATOS;
    USUARIOS.paginas = [];
    USUARIOS.cursores = [null];
    USUARIOS.error = '';
  }
  return USUARIOS;
}

// Un desplegable abierto se cierra al pulsar fuera o con Esc. Un solo listener
// para todos, registrado una vez: la sección se repinta a menudo.
document.addEventListener('click', function (evento) {
  document.querySelectorAll('.multi.abierto').forEach(function (multi) {
    if (!multi.contains(evento.target)) multi.classList.remove('abierto');
  });
});
document.addEventListener('keydown', function (evento) {
  if (evento.key !== 'Escape') return;
  document.querySelectorAll('.multi.abierto').forEach(function (multi) { multi.classList.remove('abierto'); });
});

/*
 * Desplegable de selección múltiple con buscador. `seleccion` es un array de ids que
 * se modifica en el sitio, en el orden en que se marcan; `alCambiar()` se llama en
 * cada marca. `porDefecto`, si viene, añade un botón que la restablece. `modo`, si
 * viene, añade un selector de modo encima de la lista: { opciones: [{ valor, texto }],
 * estado, clave }, con el modo vigente en `estado[clave]`.
 */
function multiSeleccion(etiqueta, opciones, seleccion, alCambiar, porDefecto, modo) {
  const contador = el('span', { className: 'multi-contador' });
  const boton = el('button', { type: 'button', className: 'desplegar multi-boton' }, [etiqueta, contador, ' ▾']);
  const filtro = el('input', { type: 'search', placeholder: 'Filtrar ' + opciones.length + ' opciones…' });
  const lista = el('div', { className: 'multi-lista' });
  const pie = el('div', { className: 'multi-pie' });
  const interruptor = modo ? el('div', { className: 'interruptor', role: 'group' }) : null;
  const panel = el('div', { className: 'multi-panel' }, [interruptor, filtro, lista, pie]);
  const caja = el('div', { className: 'multi' }, [boton, panel]);

  function modoActual() {
    return modo ? modo.opciones.filter(function (o) { return o.valor === modo.estado[modo.clave]; })[0] : null;
  }

  function actualizarContador() {
    const actual = modoActual();
    // El primer modo es el de siempre y no se nombra; los demás sí, para que se lean en el botón.
    const nombrado = actual && actual !== modo.opciones[0];
    contador.textContent = seleccion.length
      ? ' · ' + (nombrado ? actual.texto.toLowerCase() + ' ' : '') + seleccion.length : '';
    caja.classList.toggle('con-seleccion', seleccion.length > 0);
    caja.classList.toggle('excluye', seleccion.length > 0 && !!actual && actual.valor.indexOf('no_') === 0);
  }

  if (modo) {
    modo.opciones.forEach(function (opcion) {
      const control = el('button', { type: 'button', className: opcion.valor.indexOf('no_') === 0 ? 'resta' : '' }, [opcion.texto]);
      control.setAttribute('aria-pressed', modo.estado[modo.clave] === opcion.valor ? 'true' : 'false');
      control.addEventListener('click', function () {
        if (modo.estado[modo.clave] === opcion.valor) return;
        modo.estado[modo.clave] = opcion.valor;
        interruptor.querySelectorAll('button').forEach(function (b) { b.setAttribute('aria-pressed', 'false'); });
        control.setAttribute('aria-pressed', 'true');
        actualizarContador();
        // Sin nada marcado el modo no cambia el resultado: no hace falta consultar.
        if (seleccion.length) alCambiar();
      });
      interruptor.appendChild(control);
    });
  }

  function pintarLista() {
    lista.textContent = '';
    const texto = filtro.value.trim().toLowerCase();
    const visibles = opciones.filter(function (o) {
      return !texto || (o.etiqueta + ' ' + (o.detalle || '')).toLowerCase().indexOf(texto) !== -1;
    });
    visibles.forEach(function (opcion) {
      const casilla = el('input', { type: 'checkbox', checked: seleccion.indexOf(opcion.id) !== -1 });
      casilla.addEventListener('change', function () {
        const posicion = seleccion.indexOf(opcion.id);
        if (casilla.checked && posicion === -1) seleccion.push(opcion.id);
        if (!casilla.checked && posicion !== -1) seleccion.splice(posicion, 1);
        actualizarContador();
        alCambiar();
      });
      lista.appendChild(el('label', { className: 'multi-opcion' }, [
        casilla,
        el('span', {}, [opcion.etiqueta, opcion.detalle ? el('span', { className: 'codigo' }, [opcion.detalle]) : null])
      ]));
    });
    if (!visibles.length) lista.appendChild(el('p', { className: 'nota' }, ['Nada coincide.']));
  }

  const limpiar = el('button', { type: 'button', className: 'desplegar' }, ['Desmarcar todo']);
  limpiar.addEventListener('click', function () {
    seleccion.splice(0, seleccion.length);
    pintarLista();
    actualizarContador();
    alCambiar();
  });
  pie.appendChild(limpiar);

  if (porDefecto) {
    const restablecer = el('button', { type: 'button', className: 'desplegar' }, ['Por defecto']);
    restablecer.addEventListener('click', function () {
      seleccion.splice.apply(seleccion, [0, seleccion.length].concat(porDefecto));
      pintarLista();
      actualizarContador();
      alCambiar();
    });
    pie.appendChild(restablecer);
  }

  boton.addEventListener('click', function () {
    const abrir = !caja.classList.contains('abierto');
    document.querySelectorAll('.multi.abierto').forEach(function (m) { m.classList.remove('abierto'); });
    if (abrir) {
      caja.classList.add('abierto');
      pintarLista();
      // Cerca del borde derecho se abre hacia la izquierda: si no, se sale de la
      // pantalla y arrastra la página entera a lo ancho.
      caja.classList.remove('hacia-izquierda');
      if (panel.getBoundingClientRect().right > document.documentElement.clientWidth - 8) {
        caja.classList.add('hacia-izquierda');
      }
      filtro.focus();
    }
  });
  filtro.addEventListener('input', pintarLista);

  actualizarContador();
  return caja;
}

/*
 * Ficha del usuario en Configuración (perfil, rol, permission sets…). El enlace
 * directo al Id abre su perfil de Chatter, que no es lo que se viene a mirar aquí.
 */
function urlDetalleUsuario(id) {
  const destino = '/' + id + '?noredirect=1&isUserEntityOverride=1';
  return DATOS.urlInstancia + '/lightning/setup/ManageUsers/page?address=' + encodeURIComponent(destino);
}

function valorCeldaUsuario(valor, tipo) {
  if (valor === null || valor === undefined || valor === '') return '';
  if (tipo === 'boolean') return valor ? '✓' : '';
  if (tipo === 'datetime') return fechaHora(valor);
  if (tipo === 'date') return fechaCorta(valor);
  if (tipo === 'lista') return valor.join(', ');
  return String(valor);
}

/*
 * Valor de una celda para el CSV. Las fechas van completas y en un formato que
 * Excel reconoce; las asignaciones, separadas por « | » porque «;» es el
 * separador de columnas.
 */
function valorCsvUsuario(valor, tipo) {
  if (valor === null || valor === undefined) return '';
  if (tipo === 'boolean') return valor ? 'Sí' : 'No';
  if (tipo === 'lista') return valor.join(' | ');
  if (tipo === 'datetime') {
    const momento = new Date(valor);
    if (isNaN(momento.getTime())) return String(valor);
    const dos = function (n) { return String(n).padStart(2, '0'); };
    return momento.getFullYear() + '-' + dos(momento.getMonth() + 1) + '-' + dos(momento.getDate()) + ' ' +
      dos(momento.getHours()) + ':' + dos(momento.getMinutes());
  }
  return String(valor);
}

/*
 * Descarga un CSV para Excel en español: separador «;» y BOM, sin el cual Excel
 * lee el UTF-8 como si fuera Latin-1 y rompe los acentos.
 */
function descargarCsv(nombre, cabeceras, filas) {
  const celda = function (texto) {
    const valor = String(texto === null || texto === undefined ? '' : texto);
    return /[;"\r\n]/.test(valor) ? '"' + valor.replace(/"/g, '""') + '"' : valor;
  };
  const lineas = [cabeceras].concat(filas).map(function (fila) { return fila.map(celda).join(';'); });
  const blob = new Blob(['\ufeff' + lineas.join('\r\n')], { type: 'text/csv;charset=utf-8' });
  const enlace = el('a', { href: URL.createObjectURL(blob), download: nombre });
  document.body.appendChild(enlace);
  enlace.click();
  setTimeout(function () { URL.revokeObjectURL(enlace.href); enlace.remove(); }, 1000);
}

/*
 * Ventana para asignar o quitar permission sets o grupos a los usuarios marcados.
 * Son las únicas escrituras del documentator, así que piden confirmación, y en producción
 * además que se escriba el alias del org. `alTerminar()` se llama si ha cambiado algo.
 */
function abrirAsignacion(seccion, usuarios, alTerminar) {
  const estado = {
    accion: 'asignar', tipo: 'permisos', elegidos: { permisos: [], grupos: [] }, enviando: false,
    // Para quitar: cuántos de los marcados tiene cada permiso asignado directamente.
    // null mientras se consulta; se pide una vez, al pasar a «Quitar».
    tenidos: null, errorTenidos: ''
  };
  const TEXTOS = {
    asignar: { verbo: 'Asignar', titulo: 'Asignar a', enCurso: 'Asignando…', hechas: 'asignadas', sinCambio: 'ya las tenían',
      nota: 'Lo que ya tengan se deja como está.' },
    quitar: { verbo: 'Quitar', titulo: 'Quitar a', enCurso: 'Quitando…', hechas: 'quitadas', sinCambio: 'no las tenían',
      nota: 'Solo se quitan las asignaciones directas: un permission set que llega dentro de un grupo se quita quitando el grupo.' }
  };
  const fondo = el('div', { className: 'modal-fondo' });
  const caja = el('div', { className: 'modal', role: 'dialog' });
  caja.setAttribute('aria-modal', 'true');
  fondo.appendChild(caja);

  function cerrar() {
    if (estado.enviando) return;
    fondo.remove();
    document.removeEventListener('keydown', alTeclear);
  }
  function alTeclear(evento) { if (evento.key === 'Escape') cerrar(); }
  document.addEventListener('keydown', alTeclear);
  fondo.addEventListener('click', function (evento) { if (evento.target === fondo) cerrar(); });

  /*
   * Al quitar solo se ofrece lo que tiene al menos uno de los marcados, y cuántos
   * lo tienen: ofrecer todo invita a quitar algo que nadie tiene.
   */
  function opciones() {
    const todas = estado.tipo === 'permisos' ? seccion.permisos : seccion.grupos;
    if (estado.accion !== 'quitar') return todas;
    const cuantos = (estado.tenidos || {})[estado.tipo] || {};
    return todas.filter(function (o) { return cuantos[o.id]; }).map(function (o) {
      return { id: o.id, etiqueta: o.etiqueta, detalle: o.detalle + ' · lo tienen ' + num(cuantos[o.id]) + ' de ' + num(usuarios.length) };
    });
  }

  function consultarTenidos() {
    const lotes = [];
    for (let i = 0; i < usuarios.length; i += 300) lotes.push(usuarios.slice(i, i + 300));
    const suma = { permisos: {}, grupos: {} };
    Promise.all(lotes.map(function (lote) {
      const parametros = new URLSearchParams({
        org: DATOS.org, seccion: seccion.id,
        cual: JSON.stringify({ asignacionesDe: lote })
      });
      return fetch('/api/detalle?' + parametros.toString(), { cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (datos) {
          if (datos.error) throw new Error(datos.error);
          ['permisos', 'grupos'].forEach(function (clave) {
            Object.keys(datos[clave]).forEach(function (id) { suma[clave][id] = (suma[clave][id] || 0) + datos[clave][id]; });
          });
        });
    }))
      .then(function () { estado.tenidos = suma; })
      .catch(function (error) { estado.errorTenidos = String(error.message || error); estado.tenidos = { permisos: {}, grupos: {} }; })
      .finally(function () { if (!estado.enviando && fondo.isConnected) pintarEleccion(); });
  }

  function pintarEleccion() {
    caja.textContent = '';
    const elegidos = estado.elegidos[estado.tipo];
    const textos = TEXTOS[estado.accion];

    const acciones = el('div', { className: 'interruptor' }, [['asignar', 'Asignar'], ['quitar', 'Quitar']].map(function (par) {
      const boton = el('button', { type: 'button', className: par[0] === 'quitar' ? 'resta' : '' }, [par[1]]);
      boton.setAttribute('aria-pressed', estado.accion === par[0] ? 'true' : 'false');
      boton.addEventListener('click', function () {
        estado.accion = par[0];
        // Lo marcado para asignar no tiene por qué ser quitable, y al revés: se empieza de cero.
        estado.elegidos = { permisos: [], grupos: [] };
        if (par[0] === 'quitar' && !estado.tenidos) consultarTenidos();
        pintarEleccion();
      });
      return boton;
    }));

    const pestanas = el('div', { className: 'interruptor' }, [['permisos', 'Permission sets'], ['grupos', 'Permission set groups']].map(function (par) {
      const boton = el('button', { type: 'button' }, [par[1]]);
      boton.setAttribute('aria-pressed', estado.tipo === par[0] ? 'true' : 'false');
      boton.addEventListener('click', function () { estado.tipo = par[0]; pintarEleccion(); });
      return boton;
    }));

    const filtro = el('input', { type: 'search', placeholder: 'Filtrar ' + opciones().length + ' opciones…' });
    const lista = el('div', { className: 'multi-lista modal-lista' });
    const resumen = el('p', { className: 'modal-resumen' });
    const confirmar = el('button', {
      type: 'button', className: 'accion-principal' + (estado.accion === 'quitar' ? ' peligro' : '')
    }, [textos.verbo]);

    const produccion = !!DATOS.esProduccion;
    const alias = el('input', { type: 'text', placeholder: 'Escribe ' + DATOS.org + ' para confirmar' });

    function actualizar() {
      const n = elegidos.length;
      const que = estado.tipo === 'permisos' ? (n === 1 ? 'permission set' : 'permission sets') : (n === 1 ? 'permission set group' : 'permission set groups');
      const verbo = estado.accion === 'quitar' ? 'quitará' : 'asignará';
      resumen.textContent = n
        ? 'Se ' + verbo + (n === 1 ? ' ' : 'n ') + num(n) + ' ' + que + (estado.accion === 'quitar' ? ' de ' : ' a ') +
          num(usuarios.length) + (usuarios.length === 1 ? ' usuario' : ' usuarios') + ' (' + num(n * usuarios.length) +
          (n * usuarios.length === 1 ? ' asignación' : ' asignaciones') + '). ' + textos.nota
        : 'Marca uno o varios.';
      confirmar.disabled = !n || (produccion && alias.value.trim() !== DATOS.org);
    }

    function pintarLista() {
      lista.textContent = '';
      if (estado.accion === 'quitar' && !estado.tenidos) {
        lista.appendChild(el('p', { className: 'nota' }, ['Consultando qué tienen asignado los marcados…']));
        return;
      }
      if (estado.errorTenidos) {
        lista.appendChild(el('p', { className: 'nota' }, ['No se pudo consultar: ' + estado.errorTenidos]));
        return;
      }
      if (!opciones().length) {
        lista.appendChild(el('p', { className: 'nota' }, [estado.accion === 'quitar'
          ? 'Ninguno de los marcados tiene ' + (estado.tipo === 'permisos' ? 'permission sets' : 'permission set groups') + ' asignados directamente.'
          : 'No hay opciones.']));
        return;
      }
      const texto = filtro.value.trim().toLowerCase();
      opciones().filter(function (o) {
        return !texto || (o.etiqueta + ' ' + o.detalle).toLowerCase().indexOf(texto) !== -1;
      }).forEach(function (opcion) {
        const casilla = el('input', { type: 'checkbox', checked: elegidos.indexOf(opcion.id) !== -1 });
        casilla.addEventListener('change', function () {
          const posicion = elegidos.indexOf(opcion.id);
          if (casilla.checked && posicion === -1) elegidos.push(opcion.id);
          if (!casilla.checked && posicion !== -1) elegidos.splice(posicion, 1);
          actualizar();
        });
        lista.appendChild(el('label', { className: 'multi-opcion' }, [
          casilla, el('span', {}, [opcion.etiqueta, el('span', { className: 'codigo' }, [opcion.detalle])])
        ]));
      });
    }

    filtro.addEventListener('input', pintarLista);
    alias.addEventListener('input', actualizar);

    const cancelar = el('button', { type: 'button', className: 'desplegar' }, ['Cancelar']);
    cancelar.addEventListener('click', cerrar);
    confirmar.addEventListener('click', function () { enviar(elegidos.slice()); });

    // append() escribe «null» como texto: el aviso de producción va por el filtro.
    caja.append.apply(caja, [
      el('h3', {}, [textos.titulo + ' ' + num(usuarios.length) + (usuarios.length === 1 ? ' usuario' : ' usuarios')]),
      el('p', { className: 'nota' }, ['Org ', el('b', {}, [DATOS.org]),
        produccion ? el('span', { className: 'marca-pro' }, ['PRODUCCIÓN']) : null]),
      acciones, pestanas, filtro, lista, resumen,
      produccion ? el('label', { className: 'modal-confirmacion' }, [
        'Es producción. Para ' + textos.verbo.toLowerCase() + ', escribe el alias del org:', alias
      ]) : null,
      el('div', { className: 'modal-botones' }, [cancelar, confirmar])
    ].filter(Boolean));
    pintarLista();
    actualizar();
    filtro.focus();
  }

  function enviar(permisos) {
    estado.enviando = true;
    caja.textContent = '';
    caja.append(el('h3', {}, [TEXTOS[estado.accion].enCurso]), el('p', { className: 'nota' }, [
      num(permisos.length * usuarios.length) + ' asignaciones en ' + DATOS.org + '. No cierres la página.'
    ]));

    fetch('/api/permisos', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Documentator': '1' },
      body: JSON.stringify({ org: DATOS.org, accion: estado.accion, tipo: estado.tipo, usuarios: usuarios, permisos: permisos })
    })
      .then(function (r) { return r.json(); })
      .then(function (datos) {
        if (datos.error) throw new Error(datos.error);
        pintarResultado(datos);
        if (datos.hechas) alTerminar();
      })
      .catch(function (error) {
        pintarResultado({ error: String(error.message || error) });
      })
      .finally(function () { estado.enviando = false; });
  }

  function pintarResultado(datos) {
    caja.textContent = '';
    const etiquetas = {};
    seccion.permisos.concat(seccion.grupos).forEach(function (o) { etiquetas[o.id] = o.etiqueta; });
    const cerrarBoton = el('button', { type: 'button', className: 'accion-principal' }, ['Cerrar']);
    cerrarBoton.addEventListener('click', cerrar);

    const textos = TEXTOS[estado.accion];
    if (datos.error) {
      caja.append(el('h3', {}, ['No se pudo ' + textos.verbo.toLowerCase()]), vacio('Error', datos.error));
    } else {
      caja.append(
        el('h3', {}, [estado.accion === 'quitar' ? 'Retirada terminada' : 'Asignación terminada']),
        el('div', { className: 'chips' }, [
          el('span', { className: 'chip' }, [num(datos.hechas) + ' ' + textos.hechas]),
          el('span', { className: 'chip' }, [num(datos.sinCambio) + ' ' + textos.sinCambio]),
          el('span', { className: 'chip' + (datos.errores.length ? ' error' : '') }, [num(datos.errores.length) + ' con error'])
        ])
      );
      if (datos.errores.length) {
        caja.appendChild(el('div', { className: 'tabla-caja acotada usuarios-tabla' }, [
          el('table', { className: 'datos' }, [
            el('thead', {}, [el('tr', {}, ['Usuario', 'Permiso', 'Motivo'].map(function (t) { return el('th', {}, [t]); }))]),
            el('tbody', {}, datos.errores.map(function (e) {
              return el('tr', {}, [
                el('td', {}, [el('div', { className: 'celda', title: e.nombre }, [e.nombre])]),
                el('td', {}, [el('div', { className: 'celda', title: etiquetas[e.permiso] || e.permiso }, [etiquetas[e.permiso] || e.permiso])]),
                el('td', { className: 'motivo' }, [e.mensaje])
              ]);
            }))
          ])
        ]));
      }
    }
    caja.appendChild(el('div', { className: 'modal-botones' }, [cerrarBoton]));
    cerrarBoton.focus();
  }

  document.body.appendChild(fondo);
  pintarEleccion();
}

/*
 * Copia una tabla al portapapeles separada por tabuladores, que es lo que Excel
 * reparte en celdas al pegar. Un valor con tabulador, salto o comillas va entre
 * comillas, como lo espera Excel.
 */
function copiarParaExcel(cabeceras, filas) {
  const celda = function (texto) {
    const valor = String(texto === null || texto === undefined ? '' : texto);
    return /[\t\r\n"]/.test(valor) ? '"' + valor.replace(/"/g, '""') + '"' : valor;
  };
  const texto = [cabeceras].concat(filas).map(function (fila) { return fila.map(celda).join('\t'); }).join('\r\n');
  // Tras una consulta larga el navegador puede negar el portapapeles (la pestaña
  // perdió el foco o ya no cuenta como acción del usuario): entonces se ofrece un
  // botón para copiar, que sí es una acción del usuario.
  return navigator.clipboard.writeText(texto).catch(function () {
    return new Promise(function (resolver, rechazar) {
      const fondo = el('div', { className: 'modal-fondo' });
      const copiar = el('button', { type: 'button', className: 'accion-principal' }, ['Copiar']);
      const cancelar = el('button', { type: 'button', className: 'desplegar' }, ['Cancelar']);
      fondo.appendChild(el('div', { className: 'modal', role: 'dialog' }, [
        el('h3', {}, ['Listo para copiar']),
        el('p', { className: 'nota' }, [num(filas.length) + ' filas. Pulsa Copiar y pégalas en Excel.']),
        el('div', { className: 'modal-botones' }, [cancelar, copiar])
      ]));
      copiar.addEventListener('click', function () {
        navigator.clipboard.writeText(texto).then(function () { fondo.remove(); resolver(); }, function (error) { fondo.remove(); rechazar(error); });
      });
      cancelar.addEventListener('click', function () { fondo.remove(); rechazar(new Error('copia cancelada')); });
      document.body.appendChild(fondo);
      copiar.focus();
    });
  });
}

function renderGestionUsuarios(seccion) {
  const estado = estadoUsuarios(seccion);
  const filtros = estado.filtros;
  const porCampo = {};
  seccion.columnas.forEach(function (c) { porCampo[c.campo] = c; });
  const filtrables = {};
  seccion.filtrables.forEach(function (f) { filtrables[f.campo] = f; });

  const contenedor = document.createDocumentFragment();
  const recuento = el('span', { className: 'recuento' });
  const zonaTabla = el('div', { className: 'usuarios-zona' });
  const zonaCondiciones = el('div', { className: 'condiciones' });

  let espera = null;
  function programar(inmediato) {
    clearTimeout(espera);
    espera = setTimeout(function () { pedir(0, true); }, inmediato ? 0 : 400);
  }

  /*
   * Pide la página `indice`. Con `reiniciar`, los filtros han cambiado y se empieza
   * de cero: las páginas ya traídas y sus cursores dejan de valer.
   */
  function pedir(indice, reiniciar) {
    if (reiniciar) {
      estado.paginas = [];
      estado.cursores = [null];
      indice = 0;
    }
    if (estado.paginas[indice]) {
      estado.vista = { pagina: estado.paginas[indice], indice: indice };
      estado.pintar();
      return;
    }

    const turno = ++estado.turno;
    estado.cargando = true;
    estado.error = '';
    estado.pintar();

    const cuerpo = { columnas: estado.columnas };
    if (estado.cursores[indice]) cuerpo.cursor = estado.cursores[indice];
    else cuerpo.filtros = filtrosParaEnviar();

    const parametros = new URLSearchParams({
      org: estado.org, seccion: seccion.id, cual: JSON.stringify(cuerpo)
    });

    fetch('/api/detalle?' + parametros.toString(), { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (datos) {
        if (turno !== estado.turno) return;
        if (datos.error) throw new Error(datos.error);
        estado.paginas[indice] = datos;
        estado.cursores[indice + 1] = datos.siguiente;
        estado.vista = { pagina: datos, indice: indice };
      })
      .catch(function (error) {
        if (turno !== estado.turno) return;
        estado.error = String(error.message || error);
      })
      .finally(function () {
        if (turno !== estado.turno) return;
        estado.cargando = false;
        // El de la sección pintada ahora, que no tiene por qué ser la que pidió:
        // un cambio de org mientras viaja la vuelve a construir.
        estado.pintar();
      });
  }

  function filtrosParaEnviar() {
    return {
      texto: filtros.texto,
      activos: filtros.activos,
      tipos: filtros.tipos,
      perfilAdmin: filtros.perfilAdmin,
      modificarTodo: filtros.modificarTodo,
      modos: filtros.modos,
      perfiles: filtros.perfiles,
      roles: filtros.roles,
      licencias: filtros.licencias,
      licenciasPs: filtros.licenciasPs,
      grupos: filtros.grupos,
      permisos: filtros.permisos,
      // Una condición sin campo, o de permisos sin nada marcado, aún se está escribiendo: no filtra.
      condiciones: filtros.condiciones.filter(function (c) {
        if (CONDICIONES_PERMISOS[c.campo]) return (c.valores || []).length > 0;
        return c.campo && (c.operador === 'igual' || c.operador === 'distinto' || (c.valor || '').trim());
      })
    };
  }

  /* --- Barra de filtros --- */

  const buscador = el('input', { type: 'search', placeholder: 'Buscar por nombre, usuario o email', value: filtros.texto });
  buscador.addEventListener('input', function () { filtros.texto = buscador.value; programar(); });

  // Botón que enciende y apaga un filtro sí/no de `filtros`.
  function conmutador(texto, clave, ayuda) {
    const boton = el('button', { type: 'button', className: 'desplegar conmutador', title: ayuda || '' }, [texto]);
    boton.setAttribute('aria-pressed', filtros[clave] ? 'true' : 'false');
    boton.addEventListener('click', function () {
      filtros[clave] = !filtros[clave];
      boton.setAttribute('aria-pressed', filtros[clave] ? 'true' : 'false');
      programar(true);
    });
    return boton;
  }

  const activos = conmutador('Solo activos', 'activos');
  const perfilAdmin = conmutador('Perfil administrador', 'perfilAdmin',
    'Perfil con Modify All Data: el System Administrator y sus copias.');
  const modificarTodo = conmutador('Modify All Data', 'modificarTodo',
    'Con Modify All Data por cualquier vía: perfil, permission set o permission set group.');

  const nuevaCondicion = el('button', { type: 'button', className: 'desplegar' }, ['+ Condición']);
  nuevaCondicion.addEventListener('click', function () {
    filtros.condiciones.push({ campo: '', operador: 'igual', valor: '' });
    pintarCondiciones();
  });

  const limpiar = el('button', { type: 'button', className: 'desplegar' }, ['Quitar filtros']);
  limpiar.addEventListener('click', function () {
    filtros.texto = '';
    filtros.activos = false;
    filtros.perfilAdmin = false;
    filtros.modificarTodo = false;
    Object.keys(filtros.modos).forEach(function (clave) { filtros.modos[clave] = 'tiene'; });
    ['tipos', 'perfiles', 'roles', 'licencias', 'licenciasPs', 'grupos', 'permisos', 'condiciones'].forEach(function (clave) { filtros[clave].splice(0); });
    pintarSecciones();
    programar(true);
  });

  const columnas = multiSeleccion('Columnas', seccion.columnas.map(function (c) {
    return { id: c.campo, etiqueta: c.etiqueta, detalle: c.campo.indexOf('__') === 0 ? 'asignaciones' : c.campo };
  }), estado.columnas, function () {
    try { localStorage.setItem(CLAVE_COLUMNAS_USUARIOS, JSON.stringify(estado.columnas)); } catch (error) { /* sin persistencia */ }
    programar();
  }, seccion.columnasPorDefecto);
  columnas.classList.add('multi-derecha');

  contenedor.appendChild(el('div', { className: 'buscador usuarios-filtros' }, [
    activos,
    perfilAdmin,
    modificarTodo,
    // «Standard» son los usuarios de Salesforce; el resto, comunidad, invitados y de sistema.
    multiSeleccion('Tipo de usuario', seccion.tiposUsuario, filtros.tipos, programar, null, {
      estado: filtros.modos, clave: 'tipos',
      opciones: [{ valor: 'tiene', texto: 'Es' }, { valor: 'no_tiene', texto: 'No es' }]
    }),
    multiSeleccion('Perfil', seccion.perfiles, filtros.perfiles, programar, null, {
      estado: filtros.modos, clave: 'perfiles',
      opciones: [{ valor: 'tiene', texto: 'Es' }, { valor: 'no_tiene', texto: 'No es' }]
    }),
    multiSeleccion('Rol', seccion.roles, filtros.roles, programar, null, {
      estado: filtros.modos, clave: 'roles',
      opciones: [{ valor: 'tiene', texto: 'Es' }, { valor: 'no_tiene', texto: 'No es' }]
    }),
    multiSeleccion('Permission set group', seccion.grupos, filtros.grupos, programar, null, {
      estado: filtros.modos, clave: 'grupos',
      opciones: [{ valor: 'tiene', texto: 'Tiene' }, { valor: 'no_tiene', texto: 'No tiene' }]
    }),
    // «Tiene» es la asignación directa; «Contiene» suma la que llega dentro de un grupo.
    multiSeleccion('Permission set', seccion.permisos, filtros.permisos, programar, null, {
      estado: filtros.modos, clave: 'permisos',
      opciones: [
        { valor: 'tiene', texto: 'Tiene' }, { valor: 'no_tiene', texto: 'No tiene' },
        { valor: 'contiene', texto: 'Contiene' }, { valor: 'no_contiene', texto: 'No contiene' }
      ]
    }),
    columnas
  ]));
  contenedor.appendChild(zonaCondiciones);
  // Debajo de los filtros y de sus condiciones: añaden o quitan filtros, no filtran.
  contenedor.appendChild(el('div', { className: 'buscador usuarios-pie-filtros' }, [nuevaCondicion, limpiar]));

  /* --- Condiciones sobre un campo cualquiera --- */

  function textual(operador) { return operador === 'contiene' || operador === 'no_contiene'; }

  function controlValor(condicion, definicion) {
    let control;
    const valores = definicion && definicion.tipo === 'boolean'
      ? [{ valor: 'true', etiqueta: 'Sí' }, { valor: 'false', etiqueta: 'No' }]
      : (definicion && definicion.valores.length && !textual(condicion.operador) ? definicion.valores : null);

    if (valores) {
      control = el('select', {}, [el('option', { value: '' }, ['(vacío)'])].concat(valores.map(function (v) {
        return el('option', { value: v.valor }, [v.etiqueta === v.valor ? v.valor : v.etiqueta + ' (' + v.valor + ')']);
      })));
      control.value = condicion.valor;
      control.addEventListener('change', function () { condicion.valor = control.value; programar(true); });
      return control;
    }

    const tipo = definicion ? definicion.tipo : '';
    control = el('input', {
      type: tipo === 'date' || tipo === 'datetime' ? 'date' : 'text',
      placeholder: condicion.operador === 'igual' ? 'Vacío = sin valor'
        : (condicion.operador === 'distinto' ? 'Vacío = con valor' : 'Texto'),
      value: condicion.valor,
      disabled: !definicion
    });
    control.addEventListener(control.type === 'date' ? 'change' : 'input', function () {
      condicion.valor = control.value;
      programar(control.type === 'date');
    });
    return control;
  }

  function pintarCondiciones() {
    zonaCondiciones.textContent = '';
    filtros.condiciones.forEach(function (condicion, posicion) {
      const definicion = filtrables[condicion.campo];
      const dePermisos = CONDICIONES_PERMISOS[condicion.campo];

      const campo = el('select', {}, [
        el('option', { value: '' }, ['Elige un campo…']),
        el('optgroup', { label: 'Perfil, licencias y permisos' }, Object.keys(CONDICIONES_PERMISOS).map(function (clave) {
          return el('option', { value: clave }, [CONDICIONES_PERMISOS[clave].etiqueta]);
        })),
        el('optgroup', { label: 'Campos del usuario' }, seccion.filtrables.map(function (f) {
          return el('option', { value: f.campo }, [f.etiqueta + ' (' + f.campo + ')']);
        }))
      ]);
      campo.value = condicion.campo;
      campo.addEventListener('change', function () {
        const eraDePermisos = !!CONDICIONES_PERMISOS[condicion.campo];
        condicion.campo = campo.value;
        condicion.valor = '';
        condicion.valores = [];
        if (CONDICIONES_PERMISOS[condicion.campo]) {
          condicion.operador = 'tiene';
        } else {
          const nueva = filtrables[condicion.campo];
          const admiteTexto = nueva && TIPOS_TEXTO_USUARIOS.indexOf(nueva.tipo) !== -1;
          if (eraDePermisos || (!admiteTexto && textual(condicion.operador))) condicion.operador = 'igual';
        }
        pintarCondiciones();
        programar(true);
      });

      const deTexto = definicion && TIPOS_TEXTO_USUARIOS.indexOf(definicion.tipo) !== -1;
      const operador = el('select', {}, dePermisos
        ? dePermisos.modos.map(function (modo) { return el('option', { value: modo[0] }, [modo[1]]); })
        : [
          el('option', { value: 'igual' }, ['es igual a']),
          el('option', { value: 'distinto' }, ['es distinto de']),
          el('option', { value: 'contiene', disabled: !deTexto }, ['contiene']),
          el('option', { value: 'no_contiene', disabled: !deTexto }, ['no contiene'])
        ]);
      operador.value = condicion.operador;
      operador.addEventListener('change', function () {
        condicion.operador = operador.value;
        pintarCondiciones();
        programar(true);
      });

      const quitar = el('button', { type: 'button', className: 'desplegar', title: 'Quitar condición' }, ['✕']);
      quitar.addEventListener('click', function () {
        filtros.condiciones.splice(posicion, 1);
        pintarCondiciones();
        programar(true);
      });

      const valor = dePermisos
        ? multiSeleccion('Elegir', {
          __perfiles: seccion.perfiles, __licencias: seccion.licencias, __licenciasPs: seccion.licenciasPs,
          __permisos: seccion.permisos, __grupos: seccion.grupos
        }[condicion.campo],
          condicion.valores || (condicion.valores = []), programar)
        : controlValor(condicion, definicion);

      zonaCondiciones.appendChild(el('div', { className: 'buscador condicion' }, [campo, operador, valor, quitar]));
    });
  }

  /* --- Tabla y paginación --- */

  function pintar() {
    zonaTabla.textContent = '';
    zonaTabla.classList.toggle('consultando', estado.cargando);
    const pagina = estado.vista && estado.vista.pagina;
    const indice = estado.vista ? estado.vista.indice : 0;

    if (estado.error) {
      recuento.textContent = '';
      zonaTabla.appendChild(vacio('No se pudo consultar', estado.error));
      return;
    }
    if (!pagina) {
      recuento.textContent = estado.cargando ? 'Consultando el org…' : '';
      return;
    }

    const primero = indice * seccion.tamanoPagina;
    const total = pagina.total;
    recuento.textContent = estado.cargando ? 'Consultando el org…'
      : (total ? num(primero + 1) + '–' + num(primero + pagina.filas.length) + ' de ' + num(total) + ' usuarios' : 'Ningún usuario');

    const anterior = el('button', { type: 'button', className: 'desplegar', disabled: indice === 0 || estado.cargando }, ['← Anterior']);
    anterior.addEventListener('click', function () { pedir(indice - 1); });
    const siguiente = el('button', {
      type: 'button', className: 'desplegar', disabled: !estado.cursores[indice + 1] || estado.cargando
    }, ['Siguiente →']);
    siguiente.addEventListener('click', function () { pedir(indice + 1); });
    const paginas = Math.max(1, Math.ceil(total / seccion.tamanoPagina));
    const paginacion = el('div', { className: 'paginacion' }, [
      anterior, el('span', { className: 'nota' }, ['Página ' + (indice + 1) + ' de ' + paginas]), siguiente
    ]);

    if (!pagina.filas.length) {
      zonaTabla.appendChild(vacio('Ningún usuario cumple los filtros', 'Quita alguno para ampliar la búsqueda.'));
      return;
    }

    // Las columnas de la página que se está viendo, no las marcadas: si se
    // acaban de cambiar, la página nueva aún viaja.
    const columnasPagina = pagina.columnas;
    const cuerpo = el('tbody');

    // Marca o desmarca la página entera, no el resultado: lo que no se ve no se marca.
    const todasPagina = el('input', { type: 'checkbox', title: 'Marcar todos los de esta página' });
    todasPagina.addEventListener('change', function () {
      pagina.filas.forEach(function (fila) {
        if (todasPagina.checked) estado.seleccion[fila.Id] = true;
        else delete estado.seleccion[fila.Id];
      });
      cuerpo.querySelectorAll('tr').forEach(function (tr) {
        tr.classList.toggle('fila-activa', todasPagina.checked);
        tr.querySelector('td.marca input').checked = todasPagina.checked;
      });
      actualizarSeleccion();
    });

    pagina.filas.forEach(function (fila) {
      const casilla = el('input', { type: 'checkbox', checked: !!estado.seleccion[fila.Id], title: 'Marcar usuario' });
      const tr = el('tr', { className: estado.seleccion[fila.Id] ? 'fila-activa' : '' }, [el('td', { className: 'marca' }, [casilla])]);
      casilla.addEventListener('change', function () {
        if (casilla.checked) estado.seleccion[fila.Id] = true;
        else delete estado.seleccion[fila.Id];
        tr.classList.toggle('fila-activa', casilla.checked);
        actualizarSeleccion();
      });
      columnasPagina.forEach(function (campo) {
        const definicion = porCampo[campo] || {};
        const texto = valorCeldaUsuario(fila[campo], definicion.tipo);
        // El valor va en una caja con ancho tope: lo que no cabe se corta con
        // puntos suspensivos y se lee entero al pasar el ratón.
        const contenido = campo === 'Name' && DATOS.urlInstancia
          ? el('a', { href: urlDetalleUsuario(fila.Id), target: '_blank', rel: 'noopener' }, [texto])
          : texto;
        // Las asignaciones van una por línea: separadas por comas, el corte de la
        // celda se comería casi todas.
        const lineas = definicion.tipo === 'lista' && fila[campo]
          ? fila[campo].map(function (nombre) { return el('div', { className: 'celda', title: nombre }, [nombre]); })
          : [el('div', { className: 'celda', title: texto }, [contenido])];
        tr.appendChild(el('td', { className: definicion.tipo === 'boolean' ? 'num' : '' }, lineas));
      });
      cuerpo.appendChild(tr);
    });

    zonaTabla.appendChild(el('div', { className: 'tabla-caja acotada usuarios-tabla' }, [
      el('table', { className: 'datos' }, [
        el('thead', {}, [el('tr', {}, [el('th', { className: 'marca' }, [todasPagina])].concat(columnasPagina.map(function (campo) {
          const definicion = porCampo[campo] || {};
          const etiqueta = definicion.etiqueta || campo;
          return el('th', { title: etiqueta + ' (' + campo + ')' }, [el('div', { className: 'celda' }, [etiqueta])]);
        })))]),
        cuerpo
      ])
    ]));
    if (paginas > 1) zonaTabla.appendChild(paginacion);
    actualizarSeleccion();
  }

  /* --- Selección y exportación --- */

  const marcados = el('span', { className: 'nota usuarios-marcados' });
  const quitarSeleccion = el('button', { type: 'button', className: 'desplegar' }, ['Quitar selección']);
  quitarSeleccion.addEventListener('click', function () {
    estado.seleccion = {};
    pintar();
  });

  function contarSeleccion() { return Object.keys(estado.seleccion).length; }

  // Sin repintar la tabla: con 2.000 filas, marcar una casilla no puede costar eso.
  function actualizarSeleccion() {
    const n = contarSeleccion();
    marcados.textContent = estado.exportando || (n ? num(n) + (n === 1 ? ' usuario marcado' : ' usuarios marcados') : '');
    quitarSeleccion.hidden = !n || !!estado.exportando;
    asignarPermisos.disabled = !n || !!estado.exportando;
    asignarPermisos.title = n ? '' : 'Marca antes los usuarios en la tabla';
    const ocupado = !!estado.exportando && /…$/.test(estado.exportando);
    [opcionSeleccion, copiarSeleccion].forEach(function (b) {
      b.disabled = !n || ocupado;
      b.textContent = 'Usuarios marcados (' + num(n) + ')';
    });
    const pagina = estado.vista && estado.vista.pagina;
    [opcionTodo, copiarTodo].forEach(function (b) {
      b.disabled = !pagina || !pagina.total || ocupado;
      b.textContent = 'Todo el resultado (' + num(pagina ? pagina.total : 0) + ')';
    });
    const todas = zonaTabla.querySelector('thead input[type=checkbox]');
    if (todas && pagina) {
      const enPagina = pagina.filas.filter(function (f) { return estado.seleccion[f.Id]; }).length;
      todas.checked = enPagina > 0 && enPagina === pagina.filas.length;
      todas.indeterminate = enPagina > 0 && enPagina < pagina.filas.length;
    }
  }

  /*
   * Trae del org las filas a exportar con las columnas marcadas ahora: las de los
   * marcados, en lotes de Ids, o todas las páginas del resultado.
   */
  function filasParaExportar(soloMarcados) {
    const columnasAhora = estado.columnas.slice();
    const pedirUna = function (cuerpo) {
      const parametros = new URLSearchParams({
        org: estado.org, seccion: seccion.id,
        cual: JSON.stringify(Object.assign({ columnas: columnasAhora }, cuerpo))
      });
      return fetch('/api/detalle?' + parametros.toString(), { cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (datos) { if (datos.error) throw new Error(datos.error); return datos; });
    };

    const filas = [];
    if (soloMarcados) {
      const ids = Object.keys(estado.seleccion);
      const lotes = [];
      for (let i = 0; i < ids.length; i += 300) lotes.push(ids.slice(i, i + 300));
      return lotes.reduce(function (cadena, lote) {
        return cadena.then(function () {
          return pedirUna({ filtros: { ids: lote } }).then(function (datos) {
            filas.push.apply(filas, datos.filas);
            estado.exportando = 'Exportando ' + num(filas.length) + ' de ' + num(ids.length) + '…';
            actualizarSeleccion();
          });
        });
      }, Promise.resolve()).then(function () { return { columnas: columnasAhora, filas: filas }; });
    }

    const siguiente = function (cuerpo) {
      return pedirUna(cuerpo).then(function (datos) {
        filas.push.apply(filas, datos.filas);
        estado.exportando = 'Exportando ' + num(filas.length) + ' de ' + num(datos.total) + '…';
        actualizarSeleccion();
        return datos.siguiente ? siguiente({ cursor: datos.siguiente }) : { columnas: columnasAhora, filas: filas };
      });
    };
    return siguiente({ filtros: filtrosParaEnviar() });
  }

  // `copiar`: al portapapeles para pegar en Excel en vez de descargar un CSV.
  function exportar(soloMarcados, copiar) {
    menuExportar.classList.remove('abierto');
    estado.exportando = 'Preparando la exportación…';
    actualizarSeleccion();

    filasParaExportar(soloMarcados)
      .then(function (resultado) {
        const cabeceras = ['Id'].concat(resultado.columnas.map(function (campo) {
          return (porCampo[campo] || {}).etiqueta || campo;
        }));
        const filas = resultado.filas.map(function (fila) {
          return [fila.Id].concat(resultado.columnas.map(function (campo) {
            return valorCsvUsuario(fila[campo], (porCampo[campo] || {}).tipo);
          }));
        });
        if (copiar) {
          return copiarParaExcel(cabeceras, filas).then(function () {
            estado.exportando = 'Copiados ' + num(filas.length) + (filas.length === 1 ? ' usuario' : ' usuarios') + ': pégalos en Excel.';
            setTimeout(function () { estado.exportando = ''; actualizarSeleccion(); }, 5000);
          });
        }
        const hoy = new Date().toISOString().slice(0, 10);
        descargarCsv('usuarios-' + estado.org + '-' + hoy + '.csv', cabeceras, filas);
        estado.exportando = '';
      })
      .catch(function (error) {
        estado.exportando = 'No se pudo exportar: ' + (error.message || error);
        setTimeout(function () { estado.exportando = ''; actualizarSeleccion(); }, 8000);
      })
      .finally(actualizarSeleccion);
  }

  const opcionSeleccion = el('button', { type: 'button', className: 'menu-opcion' });
  opcionSeleccion.addEventListener('click', function () { exportar(true); });
  const opcionTodo = el('button', { type: 'button', className: 'menu-opcion' });
  opcionTodo.addEventListener('click', function () { exportar(false); });
  const copiarSeleccion = el('button', { type: 'button', className: 'menu-opcion' });
  copiarSeleccion.addEventListener('click', function () { exportar(true, true); });
  const copiarTodo = el('button', { type: 'button', className: 'menu-opcion' });
  copiarTodo.addEventListener('click', function () { exportar(false, true); });
  const botonExportar = el('button', { type: 'button', className: 'desplegar multi-boton' }, ['Exportar ▾']);
  const menuExportar = el('div', { className: 'multi multi-derecha' }, [
    botonExportar,
    el('div', { className: 'multi-panel menu' }, [
      el('div', { className: 'menu-titulo' }, ['Descargar CSV']),
      opcionSeleccion, opcionTodo,
      el('div', { className: 'menu-titulo' }, ['Copiar para pegar en Excel']),
      copiarSeleccion, copiarTodo,
      el('p', { className: 'nota' }, ['Con las columnas marcadas ahora, más el Id.'])
    ])
  ]);
  botonExportar.addEventListener('click', function () {
    const abrir = !menuExportar.classList.contains('abierto');
    document.querySelectorAll('.multi.abierto').forEach(function (m) { m.classList.remove('abierto'); });
    if (abrir) menuExportar.classList.add('abierto');
  });

  // La acción que escribe en el org: relleno y aparte, para que no se pulse por error.
  const asignarPermisos = el('button', { type: 'button', className: 'accion-principal', disabled: true }, ['Asignar o quitar permisos…']);
  asignarPermisos.addEventListener('click', function () {
    abrirAsignacion(seccion, Object.keys(estado.seleccion), function () {
      // Lo asignado cambia las columnas de asignaciones: se vuelve a pedir la página.
      pedir(0, true);
    });
  });

  // Fila de arriba: el buscador a la izquierda y, a la derecha, lo que actúa sobre
  // el listado. Así la barra de debajo queda entera para los filtros rápidos.
  contenedor.insertBefore(
    el('div', { className: 'buscador usuarios-acciones' }, [buscador, asignarPermisos, columnas, menuExportar]),
    contenedor.querySelector('.usuarios-filtros')
  );

  contenedor.appendChild(el('div', { className: 'usuarios-cabecera' }, [marcados, quitarSeleccion, recuento]));
  contenedor.appendChild(zonaTabla);

  estado.pintar = pintar;
  pintarCondiciones();
  if (!estado.paginas.length && !estado.cargando && !estado.error) pedir(0, true);
  else pintar();

  return contenedor;
}

/* ------------------ Visor de permission set groups ----------------- */

/*
 * Un nombre API con puntos de corte tras cada guion bajo, para que baje de línea
 * por ahí y no a mitad de palabra. Con <wbr> y no con un espacio invisible: así,
 * al copiarlo, el nombre sale tal cual.
 */
function conCortes(texto) {
  const fragmento = document.createDocumentFragment();
  String(texto || '').split(/(?<=_)/).forEach(function (trozo, i) {
    if (i) fragmento.appendChild(document.createElement('wbr'));
    fragmento.appendChild(document.createTextNode(trozo));
  });
  return fragmento;
}

/*
 * Grupo elegido y composiciones ya traídas, por org. Fuera del render por lo mismo
 * que el listado de usuarios: cambiar de org repinta la sección.
 */
let VISOR_GRUPOS = null;

function urlConfiguracion(pagina, id) {
  return DATOS.urlInstancia + '/lightning/setup/' + pagina + '/page?address=' + encodeURIComponent('/' + id);
}

function renderVisorGrupos(seccion) {
  if (!VISOR_GRUPOS || VISOR_GRUPOS.org !== DATOS.org) {
    VISOR_GRUPOS = { org: DATOS.org, elegido: '', composiciones: {}, filtro: '' };
  }
  const estado = VISOR_GRUPOS;
  const porId = {};
  seccion.grupos.forEach(function (g) { porId[g.id] = g; });

  const buscador = el('input', { type: 'search', placeholder: 'Filtrar ' + seccion.grupos.length + ' grupos…', value: estado.filtro });
  const lista = el('div', { className: 'visor-lista' });
  const ficha = el('div', { className: 'visor-ficha' });

  function pintarLista() {
    lista.textContent = '';
    const texto = estado.filtro.trim().toLowerCase();
    const visibles = seccion.grupos.filter(function (g) {
      return !texto || (g.etiqueta + ' ' + g.nombre).toLowerCase().indexOf(texto) !== -1;
    });
    visibles.forEach(function (grupo) {
      const boton = el('button', { type: 'button', className: 'visor-grupo' + (grupo.id === estado.elegido ? ' activo' : '') }, [
        el('span', { className: 'visor-grupo-nombre' }, [grupo.etiqueta, el('span', { className: 'codigo' }, [grupo.nombre])]),
        el('span', { className: 'visor-grupo-usuarios', title: 'Usuarios con el grupo asignado' }, [num(grupo.usuarios)])
      ]);
      boton.addEventListener('click', function () { elegir(grupo.id); });
      lista.appendChild(boton);
    });
    if (!visibles.length) lista.appendChild(el('p', { className: 'nota' }, ['Ningún grupo coincide.']));
  }

  function elegir(id) {
    estado.elegido = id;
    pintarLista();
    if (estado.composiciones[id]) { pintarFicha(); return; }

    pintarFicha('Consultando el org…');
    const parametros = new URLSearchParams({
      org: estado.org, seccion: seccion.id, cual: JSON.stringify({ grupo: id })
    });
    fetch('/api/detalle?' + parametros.toString(), { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (datos) {
        if (datos.error) throw new Error(datos.error);
        estado.composiciones[id] = datos;
      })
      .catch(function (error) {
        estado.composiciones[id] = { error: String(error.message || error) };
      })
      .finally(function () {
        // Otro grupo elegido mientras este viajaba: su respuesta queda guardada, pero no se pinta.
        if (estado.elegido === id && VISOR_GRUPOS === estado) pintarFicha();
      });
  }

  function pintarFicha(aviso) {
    ficha.textContent = '';
    const grupo = porId[estado.elegido];
    if (!grupo) {
      ficha.appendChild(vacio('Ningún grupo elegido', 'Elige uno de la lista para ver sus permission sets.'));
      return;
    }

    const actualizado = grupo.estado === 'Updated';
    ficha.appendChild(el('div', { className: 'visor-cabecera' }, [
      el('h3', {}, [
        DATOS.urlInstancia
          ? el('a', { href: urlConfiguracion('PermSetGroups', grupo.id), target: '_blank', rel: 'noopener' }, [grupo.etiqueta])
          : grupo.etiqueta
      ]),
      el('span', { className: 'codigo' }, [grupo.nombre]),
      el('div', { className: 'chips' }, [
        el('span', { className: 'chip' }, [num(grupo.usuarios) + (grupo.usuarios === 1 ? ' usuario' : ' usuarios')]),
        // Un grupo que no está «Updated» aún no aplica sus últimos cambios a los usuarios.
        el('span', { className: 'chip' + (actualizado ? '' : ' aviso'), title: 'Estado del cálculo de permisos del grupo' }, [
          'Estado: ' + (grupo.estado || '—')
        ])
      ]),
      grupo.descripcion ? el('p', { className: 'nota' }, [grupo.descripcion]) : null
    ]));

    const composicion = estado.composiciones[grupo.id];
    if (aviso || !composicion) { ficha.appendChild(el('p', { className: 'nota' }, [aviso || ''])); return; }
    if (composicion.error) { ficha.appendChild(vacio('No se pudo consultar', composicion.error)); return; }
    if (!composicion.permisos.length) { ficha.appendChild(vacio('Grupo vacío', 'No tiene ningún permission set.')); return; }

    if (composicion.agregado) {
      const efectivo = el('button', { type: 'button', className: 'desplegar' }, ['Ver todo lo que concede el grupo →']);
      efectivo.addEventListener('click', function () { abrirEnVisorPermisos(composicion.agregado); });
      ficha.appendChild(el('div', { className: 'visor-acciones' }, [
        efectivo, el('span', { className: 'nota' }, ['La suma de sus permission sets, ya descontado el silenciado.'])
      ]));
    }

    const normales = composicion.permisos.filter(function (p) { return !p.silenciado; }).length;
    ficha.appendChild(el('p', { className: 'nota' }, [
      num(normales) + (normales === 1 ? ' permission set' : ' permission sets') +
      (composicion.permisos.length > normales ? ' y uno de silenciado' : '')
    ]));

    ficha.appendChild(el('div', { className: 'tabla-caja usuarios-tabla' }, [
      el('table', { className: 'datos' }, [
        el('thead', {}, [el('tr', {}, ['Permission set', 'Nombre API', 'Licencia', 'Descripción'].map(function (t) {
          return el('th', {}, [t]);
        }))]),
        el('tbody', {}, composicion.permisos.map(function (permiso) {
          const nombre = el('span', {}, [
            el('a', {
              href: '#visor-permisos', title: 'Ver lo que concede',
              onclick: function (evento) { evento.preventDefault(); abrirEnVisorPermisos(permiso.id); }
            }, [permiso.etiqueta]),
            DATOS.urlInstancia ? el('a', {
              href: urlConfiguracion('PermSets', permiso.id), target: '_blank', rel: 'noopener',
              className: 'enlace-config', title: 'Abrir en Configuración'
            }, [' ↗']) : null
          ]);
          return el('tr', { className: permiso.silenciado ? 'fila-silenciado' : '' }, [
            el('td', {}, [el('div', { className: 'celda', title: permiso.etiqueta }, [nombre]),
              permiso.silenciado ? el('span', { className: 'chip aviso' }, ['Silenciado']) : null]),
            el('td', {}, [el('div', { className: 'celda', title: permiso.nombre }, [conCortes(permiso.nombre)])]),
            el('td', {}, [el('div', { className: 'celda', title: permiso.licencia }, [permiso.licencia || '—'])]),
            el('td', {}, [el('div', { className: 'celda descripcion-larga', title: permiso.descripcion }, [permiso.descripcion])])
          ]);
        }))
      ])
    ]));
  }

  buscador.addEventListener('input', function () { estado.filtro = buscador.value; pintarLista(); });

  pintarLista();
  pintarFicha();
  if (estado.elegido && !estado.composiciones[estado.elegido]) elegir(estado.elegido);

  return el('div', { className: 'visor' }, [
    el('div', { className: 'visor-izquierda' }, [buscador, lista]),
    ficha
  ]);
}

/* ------------- Visor de permission sets y perfiles ----------------- */

/*
 * Elegido, fichas y campos ya traídos, por org. `abrir` lo pone la sección al
 * pintarse, para que el visor de grupos pueda mandar aquí un permission set.
 */
let VISOR_PERMISOS = null;

const COLUMNAS_OBJETO = [
  ['leer', 'Leer'], ['crear', 'Crear'], ['editar', 'Editar'], ['borrar', 'Borrar'],
  ['verTodos', 'Ver todos'], ['modificarTodos', 'Modificar todos']
];

function abrirEnVisorPermisos(id) {
  if (VISOR_PERMISOS && VISOR_PERMISOS.abrir) VISOR_PERMISOS.abrir(id);
  const seccion = document.getElementById('visor-permisos');
  if (seccion) seccion.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// Lista de chips con buscador, para lo que se cuenta por cientos (permisos de
// sistema, clases Apex…): leer cientos de filas no sirve, buscar en ellas sí.
function nubeConBuscador(elementos, texto) {
  const caja = el('div', { className: 'nube' });
  const filtro = el('input', { type: 'search', placeholder: 'Buscar en ' + num(elementos.length) + '…' });
  const lista = el('div', { className: 'chips nube-lista' });
  function pintar() {
    lista.textContent = '';
    const buscado = filtro.value.trim().toLowerCase();
    elementos.filter(function (e) { return !buscado || texto(e).toLowerCase().indexOf(buscado) !== -1; })
      .forEach(function (e) { lista.appendChild(el('span', { className: 'chip', title: e.titulo || '' }, [texto(e)])); });
  }
  filtro.addEventListener('input', pintar);
  pintar();
  if (elementos.length > 12) caja.appendChild(filtro);
  caja.appendChild(lista);
  return caja;
}

function plegable(titulo, total, contenido, abierto) {
  const detalles = el('details', { className: 'plegable', open: !!abierto }, [
    el('summary', {}, [titulo, el('span', { className: 'recuento-chip' }, [num(total)])])
  ]);
  // El contenido se construye al abrir: con un perfil de administrador son miles de chips.
  let hecho = false;
  function construir() {
    if (hecho || !detalles.open) return;
    hecho = true;
    detalles.appendChild(contenido());
  }
  detalles.addEventListener('toggle', construir);
  construir();
  return detalles;
}

function renderVisorPermisos(seccion) {
  if (!VISOR_PERMISOS || VISOR_PERMISOS.org !== DATOS.org) {
    VISOR_PERMISOS = { org: DATOS.org, elegido: '', fichas: {}, campos: {}, filtro: '', tipo: 'todos', abiertos: {} };
  }
  const estado = VISOR_PERMISOS;

  const buscador = el('input', { type: 'search', placeholder: 'Filtrar…', value: estado.filtro });
  const tipos = el('div', { className: 'interruptor' });
  const lista = el('div', { className: 'visor-lista' });
  const ficha = el('div', { className: 'visor-ficha' });

  [['todos', 'Todos'], ['permiso', 'Permission sets'], ['perfil', 'Perfiles']].forEach(function (par) {
    const boton = el('button', { type: 'button' }, [par[1]]);
    boton.setAttribute('aria-pressed', estado.tipo === par[0] ? 'true' : 'false');
    boton.addEventListener('click', function () {
      estado.tipo = par[0];
      tipos.querySelectorAll('button').forEach(function (b) { b.setAttribute('aria-pressed', 'false'); });
      boton.setAttribute('aria-pressed', 'true');
      pintarLista();
    });
    tipos.appendChild(boton);
  });

  function pintarLista() {
    lista.textContent = '';
    const texto = estado.filtro.trim().toLowerCase();
    const visibles = seccion.permisos.filter(function (p) {
      return (estado.tipo === 'todos' || p.tipo === estado.tipo) &&
        (!texto || (p.etiqueta + ' ' + p.nombre).toLowerCase().indexOf(texto) !== -1);
    });
    buscador.placeholder = 'Filtrar ' + num(visibles.length) + '…';
    visibles.forEach(function (permiso) {
      const boton = el('button', { type: 'button', className: 'visor-grupo' + (permiso.id === estado.elegido ? ' activo' : '') }, [
        el('span', { className: 'visor-grupo-nombre' }, [
          permiso.etiqueta,
          el('span', { className: 'codigo' }, [permiso.tipo === 'perfil' ? 'Perfil · ' + (permiso.licencia || 'sin licencia') : permiso.nombre])
        ]),
        el('span', { className: 'visor-grupo-usuarios', title: 'Usuarios asignados' }, [num(permiso.usuarios)])
      ]);
      boton.addEventListener('click', function () { elegir(permiso.id); });
      lista.appendChild(boton);
    });
    if (!visibles.length) lista.appendChild(el('p', { className: 'nota' }, ['Nada coincide.']));
  }

  function pedir(cuerpo) {
    const parametros = new URLSearchParams({
      org: estado.org, seccion: seccion.id, cual: JSON.stringify(cuerpo)
    });
    return fetch('/api/detalle?' + parametros.toString(), { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (datos) { if (datos.error) throw new Error(datos.error); return datos; });
  }

  function elegir(id) {
    estado.elegido = id;
    estado.abiertos = {};
    pintarLista();
    if (estado.fichas[id]) { pintarFicha(); return; }
    pintarFicha('Consultando el org…');
    pedir({ permiso: id })
      .then(function (datos) { estado.fichas[id] = datos; })
      .catch(function (error) { estado.fichas[id] = { error: String(error.message || error) }; })
      .finally(function () { if (estado.elegido === id && VISOR_PERMISOS === estado) pintarFicha(); });
  }
  estado.abrir = elegir;

  /* --- Objetos y campos --- */

  function marca(valor, fuerte) {
    return valor
      ? el('span', { className: 'marca-permiso' + (fuerte ? ' fuerte' : ''), title: 'Concedido' }, ['✓'])
      : el('span', { className: 'marca-permiso no' }, ['·']);
  }

  function filaCampos(datos, objeto, columnas) {
    const celda = el('td', { colSpan: columnas });
    const clave = datos.id + '|' + objeto.objeto;
    const pintar = function () {
      celda.textContent = '';
      const campos = estado.campos[clave];
      if (!campos) { celda.appendChild(el('p', { className: 'nota' }, ['Consultando los campos…'])); return; }
      if (campos.error) { celda.appendChild(vacio('No se pudieron consultar', campos.error)); return; }
      const edicion = campos.campos.filter(function (c) { return c.editar; });
      const lectura = campos.campos.filter(function (c) { return !c.editar; });
      const grupo = function (titulo, lista, clase) {
        return el('div', { className: 'campos-grupo' }, [
          el('div', { className: 'campos-titulo ' + clase }, [titulo + ' (' + num(lista.length) + ')']),
          lista.length
            ? nubeConBuscador(lista.map(function (c) { return { nombre: c.etiqueta, titulo: c.campo }; }), function (c) { return c.nombre; })
            : el('p', { className: 'nota' }, ['Ninguno.'])
        ]);
      };
      celda.append(
        grupo('Puede editar', edicion, 'edicion'),
        grupo('Solo lectura', lectura, 'lectura'),
        el('p', { className: 'nota' }, ['Pasa el ratón por un campo para ver su nombre API.'])
      );
    };
    if (!estado.campos[clave]) {
      pedir({ permiso: datos.id, objeto: objeto.objeto })
        .then(function (r) { estado.campos[clave] = r; })
        .catch(function (error) { estado.campos[clave] = { error: String(error.message || error) }; })
        .finally(pintar);
    }
    pintar();
    return el('tr', { className: 'acordeon' }, [celda]);
  }

  function tablaObjetos(datos) {
    const caja = el('div');
    const filtro = el('input', { type: 'search', placeholder: 'Buscar objeto…' });
    const soloEscritura = el('input', { type: 'checkbox' });
    const cuerpo = el('tbody');
    const columnas = COLUMNAS_OBJETO.length + 2;

    function pintar() {
      cuerpo.textContent = '';
      const texto = filtro.value.trim().toLowerCase();
      datos.objetos.filter(function (o) {
        return (!texto || (o.etiqueta + ' ' + o.objeto).toLowerCase().indexOf(texto) !== -1) &&
          (!soloEscritura.checked || o.crear || o.editar || o.borrar || o.modificarTodos || o.campos.edicion);
      }).forEach(function (objeto) {
        const total = objeto.campos.edicion + objeto.campos.lectura;
        const abierto = !!estado.abiertos[objeto.objeto];
        const boton = total
          ? el('button', { type: 'button', className: 'desplegar campos-boton' + (abierto ? ' abierto' : '') }, [
            el('span', { className: 'edicion' }, [num(objeto.campos.edicion) + ' editar']),
            ' · ',
            el('span', {}, [num(objeto.campos.lectura) + ' leer']),
            abierto ? ' ▴' : ' ▾'
          ])
          : el('span', { className: 'nota' }, ['—']);
        const fila = el('tr', { className: abierto ? 'abierto' : '' }, [
          el('td', {}, [el('div', { className: 'celda', title: objeto.objeto }, [objeto.etiqueta]),
            objeto.etiqueta !== objeto.objeto ? el('span', { className: 'codigo' }, [objeto.objeto]) : null])
        ].concat(COLUMNAS_OBJETO.map(function (columna, i) {
          return el('td', { className: 'centro' }, [marca(objeto[columna[0]], i >= 4)]);
        })).concat([el('td', {}, [boton])]));
        cuerpo.appendChild(fila);
        // Toda la fila despliega sus campos, no solo el botón: es lo que se va a pulsar.
        if (total) {
          fila.classList.add('desplegable');
          fila.addEventListener('click', function () {
            estado.abiertos[objeto.objeto] = !estado.abiertos[objeto.objeto];
            pintar();
          });
        }
        if (abierto) cuerpo.appendChild(filaCampos(datos, objeto, columnas));
      });
      if (!cuerpo.children.length) {
        cuerpo.appendChild(el('tr', {}, [el('td', { colSpan: columnas }, [el('p', { className: 'nota' }, ['Ningún objeto coincide.'])])]));
      }
    }

    filtro.addEventListener('input', pintar);
    soloEscritura.addEventListener('change', pintar);
    caja.append(
      el('div', { className: 'buscador' }, [
        filtro,
        el('label', { className: 'casilla' }, [soloEscritura, 'Solo los que puede modificar']),
        el('span', { className: 'recuento' }, ['«Ver todos» y «Modificar todos» se saltan la compartición: van en ámbar.'])
      ]),
      el('div', { className: 'tabla-caja acotada usuarios-tabla matriz' }, [
        el('table', { className: 'datos' }, [
          el('thead', {}, [el('tr', {}, [el('th', {}, ['Objeto'])].concat(COLUMNAS_OBJETO.map(function (c) {
            return el('th', { className: 'centro' }, [c[1]]);
          })).concat([el('th', {}, ['Campos'])]))]),
          cuerpo
        ])
      ])
    );
    pintar();
    return caja;
  }

  /* --- Ficha --- */

  function pintarFicha(aviso) {
    ficha.textContent = '';
    if (!estado.elegido) {
      ficha.appendChild(vacio('Nada elegido', 'Elige un permission set o un perfil de la lista.'));
      return;
    }
    const datos = estado.fichas[estado.elegido];
    if (aviso || !datos) { ficha.appendChild(el('p', { className: 'nota' }, [aviso || ''])); return; }
    if (datos.error) { ficha.appendChild(vacio('No se pudo consultar', datos.error)); return; }

    const tipoTexto = { permiso: 'Permission set', perfil: 'Perfil', grupo: 'Todo lo que concede el grupo' }[datos.tipo];
    const camposTotales = datos.objetos.reduce(function (s, o) { return s + o.campos.edicion + o.campos.lectura; }, 0);
    const titulo = datos.tipo === 'permiso' && DATOS.urlInstancia
      ? el('a', { href: urlConfiguracion('PermSets', datos.id), target: '_blank', rel: 'noopener' }, [datos.etiqueta])
      : datos.etiqueta;

    ficha.appendChild(el('div', { className: 'visor-cabecera' }, [
      el('h3', {}, [titulo]),
      datos.nombre ? el('span', { className: 'codigo' }, [datos.nombre]) : null,
      el('div', { className: 'chips' }, [
        el('span', { className: 'chip' }, [tipoTexto]),
        datos.licencia ? el('span', { className: 'chip' }, ['Licencia: ' + datos.licencia]) : null,
        // En un permission set solo cuentan las asignaciones directas: quien lo recibe por grupo no suma aquí.
        datos.tipo === 'perfil' ? el('span', { className: 'chip' }, [num(datos.usuarios) + (datos.usuarios === 1 ? ' usuario con este perfil' : ' usuarios con este perfil')])
          : datos.tipo === 'permiso' ? el('span', { className: 'chip' }, [num(datos.usuarios) + ' asignados directamente']) : null
      ].concat(datos.grupos.map(function (g) {
        return el('span', { className: 'chip', title: 'Forma parte de este permission set group' }, ['Grupo: ' + g.etiqueta]);
      }))),
      datos.descripcion ? el('p', { className: 'nota' }, [datos.descripcion]) : null
    ]));

    // Resumen en cifras: de un vistazo, cuánto da y dónde mirar.
    const cifras = [
      ['Objetos que puede leer', datos.objetos.filter(function (o) { return o.leer; }).length],
      ['Campos', camposTotales],
      ['Permisos de sistema', datos.sistema.length]
    ].concat(datos.accesos.map(function (a) { return [a.titulo, a.total]; }))
      .concat([['Pestañas', datos.pestanas.length]]);
    ficha.appendChild(el('div', { className: 'cifras' }, cifras.map(function (c) {
      return el('div', { className: 'cifra' }, [el('b', {}, [num(c[1])]), el('span', {}, [c[0]])]);
    })));

    ficha.appendChild(plegable('Objetos y campos', datos.objetos.length, function () { return tablaObjetos(datos); }, true));
    ficha.appendChild(plegable('Permisos de sistema', datos.sistema.length, function () {
      return nubeConBuscador(datos.sistema.map(function (p) { return { nombre: p.etiqueta, titulo: p.campo }; }), function (p) { return p.nombre; });
    }));
    datos.accesos.forEach(function (acceso) {
      ficha.appendChild(plegable(acceso.titulo, acceso.total, function () {
        return acceso.nombres.length
          ? nubeConBuscador(acceso.nombres.map(function (n) { return { nombre: n }; }), function (n) { return n.nombre; })
          : el('p', { className: 'nota' }, ['Salesforce no deja consultar sus nombres; solo el recuento.']);
      }));
    });
    if (datos.pestanas.length) {
      ficha.appendChild(plegable('Pestañas', datos.pestanas.length, function () {
        return nubeConBuscador(datos.pestanas.map(function (p) {
          return { nombre: p.nombre + ' · ' + p.visibilidad };
        }), function (p) { return p.nombre; });
      }));
    }
  }

  buscador.addEventListener('input', function () { estado.filtro = buscador.value; pintarLista(); });

  pintarLista();
  pintarFicha();
  if (estado.elegido && !estado.fichas[estado.elegido]) elegir(estado.elegido);

  return el('div', { className: 'visor' }, [
    el('div', { className: 'visor-izquierda' }, [tipos, buscador, lista]),
    ficha
  ]);
}

/* ------------------------- Visor de flows -------------------------- */

/*
 * Búsqueda, filtros, orden y grupos desplegados, por org. Fuera del render por lo
 * mismo que los otros visores: cambiar de org repinta la sección.
 */
let VISOR_FLOWS = null;

const COLUMNAS_FLOWS = [
  { clave: 'etiqueta', titulo: 'Flow' },
  { clave: 'nombre', titulo: 'Nombre API' },
  { clave: 'tipoEtiqueta', titulo: 'Tipo' },
  { clave: 'disparadorEtiqueta', titulo: 'Disparador' },
  { clave: 'objeto', titulo: 'Objeto' },
  // Trigger Order de los flows de registro: entre los del mismo objeto, cuál va antes.
  { clave: 'ordenEjecucion', titulo: 'Orden', numerica: true },
  { clave: 'contexto', titulo: 'Contexto' },
  { clave: 'activo', titulo: 'Estado' },
  { clave: 'version', titulo: 'Versión', numerica: true },
  { clave: 'api', titulo: 'API', numerica: true },
  // Lo reciente primero al pulsarla: es lo que se viene a buscar.
  { clave: 'modificado', titulo: 'Modificado', descendente: true },
  { clave: 'modificadoPor', titulo: 'Modificado por' },
  { clave: 'paquete', titulo: 'Paquete' },
  { clave: 'descripcion', titulo: 'Descripción' }
];

/*
 * Grupos de filtro. `valor(flow)` da la opción en la que cae cada flow. Los que se
 * cuentan por decenas van en `desplegable`, con buscador, en vez de en botones.
 */
const GRUPOS_FLOWS = [
  {
    clave: 'estado', titulo: 'Estado',
    valor: function (f) { return f.activo ? 'activo' : 'inactivo'; },
    etiquetas: { activo: 'Activo', inactivo: 'Inactivo' }
  },
  {
    clave: 'tipo', titulo: 'Tipo de proceso',
    valor: function (f) { return f.tipoEtiqueta || 'Sin tipo'; }
  },
  {
    clave: 'disparador', titulo: 'Disparador',
    valor: function (f) { return f.disparadorEtiqueta || 'Sin disparador'; }
  },
  {
    clave: 'objeto', titulo: 'Objeto', desplegable: true,
    valor: function (f) { return f.objeto || 'Sin objeto'; }
  },
  {
    // Solo de la versión activa: un inactivo no corre en ningún contexto.
    clave: 'contexto', titulo: 'Contexto de ejecución',
    valor: function (f) { return f.contexto || (f.activo ? 'Sin dato (paquete)' : 'Inactivo'); }
  },
  {
    clave: 'usuario', titulo: 'Modificado por', desplegable: true,
    valor: function (f) { return f.modificadoPor || 'Sin dato'; }
  },
  {
    clave: 'origen', titulo: 'Origen',
    valor: function (f) { return f.paquete ? 'paquete' : 'propio'; },
    etiquetas: { propio: 'Propio', paquete: 'De paquete' }
  }
];

function renderVisorFlows(seccion) {
  if (!VISOR_FLOWS || VISOR_FLOWS.org !== DATOS.org) {
    const filtros = {};
    GRUPOS_FLOWS.forEach(function (g) { filtros[g.clave] = []; });
    // Lo que se viene a mirar: lo propio y lo que se está ejecutando.
    filtros.estado.push('activo');
    filtros.origen.push('propio');
    VISOR_FLOWS = {
      org: DATOS.org, texto: '', sinActivar: false, filtros: filtros,
      orden: [{ clave: 'modificado', ascendente: false }]
    };
  }
  const estado = VISOR_FLOWS;
  const flows = seccion.flows;

  const buscador = el('input', { type: 'search', placeholder: 'Buscar por nombre o nombre API', value: estado.texto });
  const recuento = el('span', { className: 'recuento' });
  const zonaFiltros = el('div', { className: 'flows-filtros' });
  const zonaDesplegables = el('span', { className: 'flows-desplegables' });
  // Opciones de cada desplegable, por grupo. Se construyen una vez y solo se les
  // actualiza el recuento: rehacer el desplegable lo cerraría a mitad de marcar.
  const opcionesDesplegables = {};
  const zonaOrden = el('div', { className: 'flows-orden nota' });
  const zonaTabla = el('div');

  function pasaTexto(flow) {
    const texto = estado.texto.trim().toLowerCase();
    return !texto || (flow.etiqueta + ' ' + flow.nombre).toLowerCase().indexOf(texto) !== -1;
  }

  // `salvo` deja fuera un grupo: los contadores de sus botones dicen cuántos
  // saldrían al marcarlo con el resto de filtros como están.
  function pasa(flow, salvo) {
    if (!pasaTexto(flow)) return false;
    if (estado.sinActivar && salvo !== 'sinActivar' && !flow.sinActivar) return false;
    return GRUPOS_FLOWS.every(function (grupo) {
      const marcados = estado.filtros[grupo.clave];
      return grupo.clave === salvo || !marcados.length || marcados.indexOf(grupo.valor(flow)) !== -1;
    });
  }

  function alternar(lista, valor) {
    const posicion = lista.indexOf(valor);
    if (posicion === -1) lista.push(valor); else lista.splice(posicion, 1);
    pintar();
  }

  function contar(grupo) {
    const cuenta = {};
    flows.forEach(function (f) {
      const valor = grupo.valor(f);
      if (!(valor in cuenta)) cuenta[valor] = 0;
      if (pasa(f, grupo.clave)) cuenta[valor]++;
    });
    return cuenta;
  }

  // Los de más flows primero.
  function ordenarOpciones(cuenta) {
    return Object.keys(cuenta).sort(function (a, b) {
      return cuenta[b] - cuenta[a] || a.localeCompare(b, 'es');
    });
  }

  function construirDesplegables() {
    zonaDesplegables.textContent = '';
    GRUPOS_FLOWS.filter(function (g) { return g.desplegable; }).forEach(function (grupo) {
      const cuenta = contar(grupo);
      opcionesDesplegables[grupo.clave] = ordenarOpciones(cuenta).map(function (valor) {
        return { id: valor, etiqueta: valor, detalle: '' };
      });
      zonaDesplegables.appendChild(multiSeleccion(grupo.titulo, opcionesDesplegables[grupo.clave],
        estado.filtros[grupo.clave], pintar));
    });
  }

  function pintarFiltros() {
    zonaFiltros.textContent = '';

    GRUPOS_FLOWS.forEach(function (grupo) {
      const marcados = estado.filtros[grupo.clave];
      const cuenta = contar(grupo);

      // El recuento de un desplegable se ve al abrirlo. No cambia mientras se marca
      // en él, porque el de cada grupo descuenta sus propios filtros.
      if (grupo.desplegable) {
        opcionesDesplegables[grupo.clave].forEach(function (opcion) {
          opcion.detalle = num(cuenta[opcion.id]) + (cuenta[opcion.id] === 1 ? ' flow' : ' flows');
        });
        return;
      }

      const botones = ordenarOpciones(cuenta).map(function (valor) {
        const boton = el('button', {
          type: 'button', className: 'desplegar conmutador' + (cuenta[valor] ? '' : ' sin-flows')
        }, [(grupo.etiquetas && grupo.etiquetas[valor]) || valor, el('span', { className: 'flows-cuenta' }, [num(cuenta[valor])])]);
        boton.setAttribute('aria-pressed', marcados.indexOf(valor) !== -1 ? 'true' : 'false');
        boton.addEventListener('click', function () { alternar(marcados, valor); });
        return boton;
      });

      if (grupo.clave === 'estado') {
        const n = flows.filter(function (f) { return f.sinActivar && pasa(f, 'sinActivar'); }).length;
        const boton = el('button', {
          type: 'button', className: 'desplegar conmutador' + (n ? '' : ' sin-flows'),
          title: 'Tiene una versión posterior a la activa: lo que se ve en el builder no es lo que se ejecuta.'
        }, ['Versión sin activar', el('span', { className: 'flows-cuenta' }, [num(n)])]);
        boton.setAttribute('aria-pressed', estado.sinActivar ? 'true' : 'false');
        boton.addEventListener('click', function () { estado.sinActivar = !estado.sinActivar; pintar(); });
        botones.push(boton);
      }

      zonaFiltros.appendChild(el('div', { className: 'flows-grupo' }, [
        el('span', { className: 'flows-grupo-titulo' }, [grupo.titulo]),
        el('div', { className: 'flows-botones' }, botones)
      ]));
    });
  }

  function comparar(a, b) {
    for (let i = 0; i < estado.orden.length; i++) {
      const criterio = estado.orden[i];
      const columna = COLUMNAS_FLOWS.filter(function (c) { return c.clave === criterio.clave; })[0] || {};
      const va = a[criterio.clave], vb = b[criterio.clave];
      // Los vacíos van al final en los dos sentidos: arriba no dicen nada.
      const vacioA = va === '' || va === null || va === undefined || (columna.numerica && !va);
      const vacioB = vb === '' || vb === null || vb === undefined || (columna.numerica && !vb);
      if (vacioA !== vacioB) return vacioA ? 1 : -1;
      if (vacioA) continue;
      let diferencia;
      if (typeof va === 'string') diferencia = va.localeCompare(vb, 'es', { sensitivity: 'base', numeric: true });
      else diferencia = va < vb ? -1 : (va > vb ? 1 : 0);
      if (diferencia) return criterio.ascendente ? diferencia : -diferencia;
    }
    return 0;
  }

  // Pulsar ordena solo por esa columna (y si ya era el único criterio, invierte el
  // sentido); con Mayúsculas se añade como criterio siguiente o invierte el suyo.
  function ordenarPor(columna, anadir) {
    const posicion = estado.orden.map(function (o) { return o.clave; }).indexOf(columna.clave);
    if (anadir) {
      if (posicion === -1) estado.orden.push({ clave: columna.clave, ascendente: !columna.descendente });
      else estado.orden[posicion].ascendente = !estado.orden[posicion].ascendente;
    } else if (posicion === 0 && estado.orden.length === 1) {
      estado.orden[0].ascendente = !estado.orden[0].ascendente;
    } else {
      estado.orden = [{ clave: columna.clave, ascendente: !columna.descendente }];
    }
    pintarTabla();
  }

  function pintarOrden() {
    zonaOrden.textContent = '';
    zonaOrden.appendChild(document.createTextNode('Ordenado por '));
    estado.orden.forEach(function (criterio, i) {
      const columna = COLUMNAS_FLOWS.filter(function (c) { return c.clave === criterio.clave; })[0];
      const quitar = el('button', { type: 'button', className: 'enlace-boton', title: 'Quitar este criterio' }, ['✕']);
      quitar.addEventListener('click', function () {
        estado.orden.splice(i, 1);
        if (!estado.orden.length) estado.orden.push({ clave: 'modificado', ascendente: false });
        pintarTabla();
      });
      zonaOrden.appendChild(el('span', { className: 'chip flows-criterio' }, [
        (i + 1) + '. ' + columna.titulo + ' ' + (criterio.ascendente ? '↑' : '↓'),
        estado.orden.length > 1 ? quitar : null
      ]));
    });
    zonaOrden.appendChild(document.createTextNode(' · Mayúsculas + clic en una cabecera añade un criterio.'));
  }

  function pintarTabla() {
    const filas = flows.filter(function (f) { return pasa(f); }).sort(comparar);
    recuento.textContent = num(filas.length) + ' de ' + num(flows.length) + ' flows';
    pintarOrden();
    zonaTabla.textContent = '';

    if (!filas.length) {
      zonaTabla.appendChild(vacio('Ningún flow cumple los filtros', 'Quita alguno para ampliar la búsqueda.'));
      return;
    }

    const cabeceras = COLUMNAS_FLOWS.map(function (columna) {
      const posicion = estado.orden.map(function (o) { return o.clave; }).indexOf(columna.clave);
      const criterio = estado.orden[posicion];
      const flecha = el('span', { className: 'flecha' }, [
        criterio ? (estado.orden.length > 1 ? (posicion + 1) + ' ' : '') + (criterio.ascendente ? '↑' : '↓') : ''
      ]);
      const celda = el('th', { title: 'Ordenar · Mayúsculas + clic para añadir como criterio siguiente' }, [
        el('div', { className: 'celda' }, [columna.titulo + ' ', flecha])
      ]);
      celda.addEventListener('click', function (evento) { ordenarPor(columna, evento.shiftKey); });
      return celda;
    });

    const celda = function (texto, extra) {
      return el('td', {}, [el('div', { className: 'celda' + (extra || ''), title: texto || '' }, [texto || '—'])]);
    };

    const cuerpo = el('tbody', {}, filas.map(function (f) {
      const nombre = DATOS.urlInstancia
        ? el('a', { href: urlConfiguracion('Flows', f.id), target: '_blank', rel: 'noopener' }, [f.etiqueta])
        : f.etiqueta;
      return el('tr', { className: f.activo ? '' : 'flow-inactivo' }, [
        el('td', {}, [el('div', { className: 'celda', title: f.etiqueta }, [nombre])]),
        celda(f.nombre, ' codigo'),
        celda(f.tipoEtiqueta),
        el('td', {}, [
          el('div', { className: 'celda', title: f.disparadorEtiqueta }, [f.disparadorEtiqueta || '—']),
          f.operacion ? el('div', { className: 'nota-celda' }, [f.operacion]) : null
        ]),
        celda(f.objeto),
        el('td', { className: 'num' }, [f.ordenEjecucion ? String(f.ordenEjecucion) : '—']),
        celda(f.contexto),
        el('td', {}, [
          el('span', { className: 'chip' + (f.activo ? '' : ' inactivo') }, [f.activo ? 'Activo' : 'Inactivo']),
          f.sinActivar ? el('span', {
            className: 'chip aviso', title: 'Hay una versión posterior a la activa'
          }, ['Versión sin activar']) : null
        ]),
        el('td', { className: 'num' }, [f.version ? 'v' + f.version : '—']),
        el('td', { className: 'num' }, [f.api ? String(f.api) : '—']),
        el('td', { className: 'num', title: f.modificado ? new Date(f.modificado).toLocaleString('es-ES') : '' }, [fechaCortaInstante(f.modificado) || '—']),
        celda(f.modificadoPor),
        celda(f.paquete),
        celda(f.descripcion, ' descripcion-larga')
      ]);
    }));

    zonaTabla.appendChild(el('div', { className: 'tabla-caja acotada usuarios-tabla flows-tabla' }, [
      el('table', { className: 'datos' }, [el('thead', {}, [el('tr', {}, cabeceras)]), cuerpo])
    ]));
  }

  function pintar() {
    pintarFiltros();
    pintarTabla();
  }

  let espera = null;
  buscador.addEventListener('input', function () {
    estado.texto = buscador.value;
    clearTimeout(espera);
    espera = setTimeout(pintar, 150);
  });

  const limpiar = el('button', { type: 'button', className: 'desplegar' }, ['Quitar filtros']);
  limpiar.addEventListener('click', function () {
    estado.texto = '';
    buscador.value = '';
    estado.sinActivar = false;
    GRUPOS_FLOWS.forEach(function (g) { estado.filtros[g.clave].splice(0); });
    // El contador del botón del desplegable solo se actualiza desde dentro de él.
    construirDesplegables();
    pintar();
  });

  construirDesplegables();
  pintar();

  return el('div', {}, [
    el('div', { className: 'buscador flows-buscador' }, [buscador, zonaDesplegables, limpiar, recuento]),
    zonaFiltros,
    zonaOrden,
    zonaTabla
  ]);
}

/* ---------------------------- Licencias ---------------------------- */

// Barra de uso dentro de una celda. Por encima del 100 % el org gasta más de lo que
// tiene contratado: la barra se llena y cambia de color, el número dice cuánto.
function celdaUso(usadas, total) {
  const proporcion = total > 0 ? usadas / total : (usadas > 0 ? Infinity : 0);
  const relleno = el('div', { className: 'peso-relleno' });
  relleno.style.width = (100 * Math.min(1, proporcion)).toFixed(1) + '%';
  relleno.style.background = proporcion > 1 ? 'var(--estado-critico)' : 'var(--serie-1)';
  return el('td', {}, [
    el('div', { className: 'peso' }, [
      el('div', { className: 'peso-pista' }, [relleno]),
      el('span', { className: 'num' }, [total > 0 ? pct(usadas, total) : (usadas > 0 ? 'sin total' : '—')])
    ])
  ]);
}

function tablaLicencias(cabeceras, filas, acotada) {
  return el('div', { className: 'tabla-caja' + (acotada ? ' acotada' : '') }, [
    el('table', { className: 'datos' }, [
      el('thead', {}, [el('tr', {}, cabeceras.map(function (texto) { return el('th', {}, [texto]); }))]),
      el('tbody', {}, filas)
    ])
  ]);
}

function estadoLicencia(estado) {
  return estado === 'Active' ? 'Activa' : (estado === 'Disabled' ? 'Desactivada' : (estado || ''));
}

function fechaLicencia(iso) {
  return iso ? fechaCorta(iso) : '—';
}

function renderLicencias(seccion) {
  const contenedor = document.createDocumentFragment();
  const dias = seccion.diasSinLogin;

  const porLicencia = seccion.porLicencia || [];
  const activos = porLicencia.reduce(function (s, f) { return s + (Number(f.activos) || 0); }, 0);
  const sinLogin = porLicencia.reduce(function (s, f) { return s + (Number(f.sinLogin) || 0); }, 0);
  const usuario = (seccion.usuario || []).filter(function (f) { return f.total > 0 || f.usadas > 0; });
  const permissionSet = (seccion.permissionSet || []).filter(function (f) { return f.total > 0 || f.usadas > 0; });
  const paquete = seccion.paquete || [];

  const kpis = [
    { etiqueta: 'Usuarios activos', valor: num(activos), pie: 'todos los tipos de usuario' },
    { etiqueta: 'Activos sin entrar en ' + dias + ' días', valor: num(sinLogin), pie: pct(sinLogin, activos) + ' de los activos, incluidos los que nunca han entrado' },
    { etiqueta: 'Licencias de usuario', valor: num(usuario.length), pie: 'con algún total o alguna usada' },
    { etiqueta: 'Licencias de permission set', valor: num(permissionSet.length), pie: 'con algún total o alguna usada' },
    { etiqueta: 'Licencias de paquetes', valor: num(paquete.length), pie: 'una por fila de PackageLicense' }
  ];
  const rejilla = el('div', { className: 'kpis en-fila' }, kpis.map(function (kpi) {
    return el('div', { className: 'kpi' }, [
      el('div', { className: 'kpi-etiqueta' }, [kpi.etiqueta]),
      el('div', { className: 'kpi-valor' }, [kpi.valor]),
      el('div', { className: 'kpi-pie' }, [kpi.pie])
    ]);
  }));
  rejilla.style.setProperty('--columnas', kpis.length);
  contenedor.appendChild(rejilla);

  const actualizado = usuario.map(function (f) { return f.actualizado; }).filter(Boolean).sort().pop();
  contenedor.appendChild(el('div', { className: 'tarjeta' }, [
    el('h3', {}, ['Licencias de usuario']),
    el('p', { className: 'nota' }, [
      'Uso de cada licencia sobre su total. En rojo, las que tienen más usadas que contratadas. ' +
      'Las que no tienen ni total ni usadas no salen.' +
      (actualizado ? ' Recuento de Salesforce del ' + fechaCortaInstante(actualizado) + '.' : '')
    ]),
    usuario.length
      ? tablaLicencias(['Licencia', 'Estado', 'Total', 'Usadas', 'Disponibles', 'Uso'], usuario.map(function (f) {
          return el('tr', {}, [
            el('td', {}, [f.nombre]),
            el('td', {}, [estadoLicencia(f.estado)]),
            el('td', { className: 'num' }, [num(f.total)]),
            el('td', { className: 'num' }, [num(f.usadas)]),
            el('td', { className: 'num' }, [num(f.total - f.usadas)]),
            celdaUso(f.usadas, f.total)
          ]);
        }))
      : vacio('Sin licencias de usuario', 'El org no devuelve ninguna licencia de usuario con total o uso.')
  ]));

  contenedor.appendChild(el('div', { className: 'rejilla-2' }, [
    el('div', { className: 'tarjeta' }, [
      el('h3', {}, ['Usuarios activos por licencia']),
      el('p', { className: 'nota' }, [
        'La licencia de un usuario es la de su perfil. En azul, los activos; en naranja, de ellos, los que no ' +
        'han entrado en ' + dias + ' días o nunca. «Sin licencia de perfil» son usuarios internos sin perfil visible, ' +
        'como Automated Process o Cloud Integration User.'
      ]),
      porLicencia.length
        ? barras(porLicencia.map(function (f) {
            return { etiqueta: f.licencia, activos: Number(f.activos) || 0, sinLogin: Number(f.sinLogin) || 0 };
          }), [
            { clave: 'activos', etiqueta: 'Activos' },
            { clave: 'sinLogin', etiqueta: 'Sin entrar en ' + dias + ' días', secundaria: true }
          ])
        : vacio('Sin usuarios activos', '')
    ]),
    el('div', { className: 'tarjeta' }, [
      el('h3', {}, ['Usuarios activos por perfil']),
      el('p', { className: 'nota' }, ['Cada perfil con su licencia, de más a menos usuarios activos.']),
      (seccion.porPerfil || []).length
        ? tablaLicencias(['Perfil', 'Licencia', 'Activos', 'Sin entrar en ' + dias + ' días'], seccion.porPerfil.map(function (f) {
            return el('tr', {}, [
              el('td', {}, [f.perfil]),
              el('td', {}, [f.licencia]),
              el('td', { className: 'num' }, [num(f.activos)]),
              el('td', { className: 'num' }, [num(f.sinLogin) + ' (' + pct(f.sinLogin, f.activos) + ')'])
            ]);
          }), true)
        : vacio('Sin usuarios activos', '')
    ])
  ]));

  const ocultas = (seccion.permissionSet || []).length - permissionSet.length;
  contenedor.appendChild(el('div', { className: 'tarjeta' }, [
    el('h3', {}, ['Licencias de permission set']),
    el('p', { className: 'nota' }, [
      'Se asignan aparte del perfil. «Desactivada» es como Salesforce marca una licencia caducada. ' +
      (ocultas ? num(ocultas) + ' más sin total ni usadas no salen.' : '')
    ]),
    permissionSet.length
      ? tablaLicencias(['Licencia', 'Estado', 'Total', 'Usadas', 'Disponibles', 'Uso', 'Caduca'], permissionSet.map(function (f) {
          return el('tr', {}, [
            el('td', {}, [f.nombre]),
            el('td', {}, [estadoLicencia(f.estado)]),
            el('td', { className: 'num' }, [num(f.total)]),
            el('td', { className: 'num' }, [num(f.usadas)]),
            el('td', { className: 'num' }, [num(f.total - f.usadas)]),
            celdaUso(f.usadas, f.total),
            el('td', { className: 'num' }, [fechaLicencia(f.caduca)])
          ]);
        }), true)
      : vacio('Sin licencias de permission set', 'El org no tiene ninguna con total o uso.')
  ]));

  contenedor.appendChild(el('div', { className: 'tarjeta' }, [
    el('h3', {}, ['Licencias de paquetes instalados']),
    el('p', { className: 'nota' }, [
      'Una fila por licencia de paquete gestionado, identificada por su namespace, que puede repetirse. ' +
      'Permitidas −1 es lo que devuelve Salesforce en los paquetes con licencia para todo el org o gratuitos: ' +
      'no limita por usuario. Los paquetes sin licencia propia no salen.'
    ]),
    paquete.length
      ? tablaLicencias(['Namespace', 'Estado', 'Permitidas', 'Usadas', 'Uso', 'Caduca'], paquete.map(function (f) {
          const ilimitada = f.permitidas === -1;
          return el('tr', {}, [
            el('td', {}, [f.namespace || '(sin namespace)']),
            el('td', {}, [f.estado || '']),
            el('td', { className: 'num' }, [ilimitada ? 'Sin límite (−1)' : num(f.permitidas)]),
            el('td', { className: 'num' }, [num(f.usadas)]),
            ilimitada ? el('td', {}, ['—']) : celdaUso(f.usadas, Number(f.permitidas) || 0),
            el('td', { className: 'num' }, [fechaLicencia(f.caduca)])
          ]);
        }), true)
      : vacio('Sin licencias de paquetes', 'Ningún paquete instalado tiene licencia propia.')
  ]));

  return contenedor;
}

const RENDERIZADORES = {
  'gestion-usuarios': renderGestionUsuarios,
  'visor-grupos': renderVisorGrupos,
  'visor-permisos': renderVisorPermisos,
  'licencias': renderLicencias,
  'visor-flows': renderVisorFlows
};

/* ---------------------------- Arranque ---------------------------- */

// La página que se está viendo, que el servidor deja en el body.
const PAGINA = document.body.dataset.pagina;

const selectorOrg = document.getElementById('org');
const botonRecargar = document.getElementById('recargar');
const nota = document.getElementById('filtros-nota');
const contenedorSecciones = document.getElementById('secciones');

// Numero de la ultima consulta lanzada. Una respuesta que llega cuando ya se ha
// cambiado de org mientras viajaba se guarda en cache pero no se pinta: si no,
// el org lento acabaria encima del que se esta mirando.
let consultaVigente = 0;

// Payload de cada org ya consultado. Volver a un org no vuelve a consultarlo;
// «Actualizar» si.
const CACHE = {};

// El org elegido sobrevive a la recarga: --org solo decide con cual se abre la
// primera vez. localStorage puede estar bloqueado (modo privado), y entonces se
// vuelve al org por defecto en vez de romper el arranque.
const CLAVE_ORG = 'documentator.org';

function orgGuardado() {
  try {
    return localStorage.getItem(CLAVE_ORG);
  } catch (error) {
    return null;
  }
}

function guardarOrg(alias) {
  try {
    localStorage.setItem(CLAVE_ORG, alias);
  } catch (error) {
    /* sin persistencia; la pagina funciona igual */
  }
}

function finCarga() {
  document.body.classList.remove('cargando');
  botonRecargar.disabled = false;
}

function pintarSecciones() {
  contenedorSecciones.textContent = '';

  DATOS.secciones.forEach(function (seccion) {
    const render = RENDERIZADORES[seccion.id];
    const nodo = el('section', { className: 'seccion', id: seccion.id }, [
      el('h2', {}, [seccion.titulo]),
      seccion.descripcion ? el('p', { className: 'descripcion' }, [seccion.descripcion]) : null
    ]);
    nodo.appendChild(render ? render(seccion)
      : vacio('Sección sin renderizador', 'Falta la función para «' + seccion.id + '» en vivo.js.'));
    contenedorSecciones.appendChild(nodo);
  });
}

function pintarSello() {
  const sello = document.getElementById('sello');
  sello.textContent = '';
  sello.append(
    el('span', {}, [
      'Org ',
      el('b', {}, [DATOS.org]),
      // El aviso va pegado al nombre del org, no suelto en la barra: es el dato
      // que cualifica, y separarlo deja leer el alias sin verlo.
      DATOS.esProduccion ? el('span', { className: 'marca-pro', title: 'Este org no es un sandbox' }, ['PRODUCCIÓN']) : null
    ]),
    el('span', {}, ['Consultado ', el('b', {}, [DATOS.consultado])])
  );

  document.body.classList.toggle('es-produccion', !!DATOS.esProduccion);
}

function consultar(forzar) {
  const turno = ++consultaVigente;
  const clave = selectorOrg.value;

  if (!forzar && CACHE[clave]) {
    DATOS = CACHE[clave];
    pintarSello();
    pintarSecciones();
    nota.textContent = '';
    finCarga();
    return;
  }

  // Se mantiene el render anterior atenuado en vez de vaciarlo: sin salto de
  // maquetacion y sin perder de vista lo que habia.
  document.body.classList.add('cargando');
  botonRecargar.disabled = true;
  nota.textContent = 'Consultando el org…';

  const parametros = new URLSearchParams({ org: selectorOrg.value, pagina: PAGINA });

  fetch('/api/datos?' + parametros.toString(), { cache: 'no-store' })
    .then(function (respuesta) { return respuesta.json(); })
    .then(function (respuesta) {
      if (respuesta.error) throw new Error(respuesta.error);
      CACHE[clave] = respuesta;
      if (turno !== consultaVigente) return;
      DATOS = respuesta;
      pintarSello();
      pintarSecciones();
      nota.textContent = '';
    })
    .catch(function (error) {
      if (turno !== consultaVigente) return;
      nota.textContent = '';
      contenedorSecciones.textContent = '';
      contenedorSecciones.appendChild(el('section', { className: 'seccion' }, [
        vacio('No se pudieron consultar los datos', String(error.message || error))
      ]));
    })
    .finally(function () {
      if (turno === consultaVigente) finCarga();
    });
}

// El mismo interruptor de tema que el indice y las paginas de objeto, con la misma
// clave: el tema elegido en una vale para todas.
function interruptorTema() {
  const boton = document.getElementById('dm-toggle');
  const raiz = document.documentElement;
  boton.textContent = raiz.getAttribute('data-theme') === 'dark' ? '☀️' : '🌙';
  boton.addEventListener('click', function () {
    const oscuro = raiz.getAttribute('data-theme') !== 'dark';
    if (oscuro) raiz.setAttribute('data-theme', 'dark'); else raiz.removeAttribute('data-theme');
    boton.textContent = oscuro ? '☀️' : '🌙';
    try { localStorage.setItem('sf-docs-theme', oscuro ? 'dark' : 'light'); } catch (error) { /* sin persistencia */ }
  });
}

function arrancar() {
  interruptorTema();
  fetch('/api/config', { cache: 'no-store' })
    .then(function (respuesta) { return respuesta.json(); })
    .then(function (config) {
      const alias = config.orgs.length ? config.orgs : [config.org];
      alias.forEach(function (nombre) {
        selectorOrg.appendChild(el('option', { value: nombre }, [nombre]));
      });
      // Solo se recupera si la CLI sigue teniendo ese alias autenticado: si no,
      // el selector quedaria en un valor que el servidor no sabe resolver.
      const preferido = orgGuardado();
      selectorOrg.value = alias.indexOf(preferido) !== -1 ? preferido : config.org;

      selectorOrg.addEventListener('change', function () {
        guardarOrg(selectorOrg.value);
        consultar();
      });
      botonRecargar.addEventListener('click', function () { consultar(true); });

      consultar();
    });
}

arrancar();
