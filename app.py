import streamlit as st
import ee
import folium
from streamlit_folium import st_folium
import pandas as pd
import requests
import os
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from google.oauth2 import service_account
from datetime import datetime, timedelta

# -----------------------------------------------------------------------------
# 1. CONFIGURACIÓN DE PÁGINA
# -----------------------------------------------------------------------------
st.set_page_config(page_title="SAT Río Cahabón - En Vivo", page_icon="🌊", layout="wide")

st.markdown("""
    <style>
        .block-container { padding-top: 1rem; padding-bottom: 0rem; }
        .stMetric { background-color: #f8f9fa; padding: 10px; border-radius: 8px; }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 2. AUTENTICACIÓN SECRETA DE GEE
# -----------------------------------------------------------------------------
@st.cache_resource
def init_earth_engine():
    try:
        service_account_info = dict(st.secrets["gee_service_account"])
        if "private_key" in service_account_info:
            pk = service_account_info["private_key"].replace("\\n", "\n").strip("'\"")
            service_account_info["private_key"] = pk
            
        scopes = ["https://www.googleapis.com/auth/earthengine", "https://www.googleapis.com/auth/devstorage.full_control"]
        credentials = service_account.Credentials.from_service_account_info(service_account_info, scopes=scopes)
        project_id = service_account_info.get("project_id")
        ee.Initialize(credentials, project=project_id)
    except Exception as e:
        st.error(f"🚨 Error crítico de conexión a GEE: {e}")
        st.stop()

init_earth_engine()

# -----------------------------------------------------------------------------
# 3. CARGA DE LA CUENCA Y DEM
# -----------------------------------------------------------------------------
HYBAS_ID = 7080868090 

@st.cache_data
def get_basin_and_dem(hybas_id):
    basins = ee.FeatureCollection("WWF/HydroSHEDS/v1/Basins/hybas_8")
    cuenca = basins.filter(ee.Filter.eq('HYBAS_ID', hybas_id)).first()
    geom = cuenca.geometry()
    dem = ee.Image("USGS/SRTMGL1_003").clip(geom)
    centroid = geom.centroid().coordinates().getInfo()
    return cuenca, dem, centroid

try:
    cuenca_feat, dem_cuenca, centroid = get_basin_and_dem(HYBAS_ID)
    lat_center, lon_center = centroid[1], centroid[0]
except Exception as err:
    st.error(f"Error al cargar la cuenca en GEE: {err}")
    st.stop()

# -----------------------------------------------------------------------------
# 4. CARGA DE HISTÓRICO Y UMBRALES (CHIRPS)
# -----------------------------------------------------------------------------
@st.cache_data
def cargar_datos():
    if os.path.exists('serie_lluvia_cahabon.csv'):
        df = pd.read_csv('serie_lluvia_cahabon.csv')
        if 'Unnamed: 0' in df.columns:
            df = df.drop(columns=['Unnamed: 0'])
        df['fecha'] = pd.to_datetime(df['fecha']).dt.date # Guardar como date
        
        precip_only = df[df['lluvia_mm'] > 2]['lluvia_mm']
        p90 = precip_only.quantile(0.90)
        p95 = precip_only.quantile(0.95)
        p99 = precip_only.quantile(0.99)
        
        fecha_min = df['fecha'].min()
        fecha_max = df['fecha'].max()
        
        return df, p90, p95, p99, fecha_min, fecha_max
    else:
        return pd.DataFrame(columns=['fecha', 'lluvia_mm']), 25.0, 45.0, 70.0, datetime.today().date(), datetime.today().date()

df_historico, umbral_amarilla, umbral_naranja, umbral_roja, fecha_min_hist, fecha_max_hist = cargar_datos()

# -----------------------------------------------------------------------------
# 5. PANEL LATERAL: MÁQUINA DEL TIEMPO
# -----------------------------------------------------------------------------
with st.sidebar:
    st.image("https://catie.ac.cr/wp-content/uploads/2023/04/Logo-CATIE-2023.png", use_container_width=True)
    st.title("⚙️ Controles del SAT")
    
    st.markdown("### ⏱️ Máquina del Tiempo")
    hoy = datetime.today().date()
    fecha_seleccionada = st.date_input(
        "Seleccione fecha a simular:",
        value=hoy,
        min_value=fecha_min_hist,
        max_value=hoy,
        help="Elige 'Hoy' para pronóstico real o una fecha pasada para simular eventos históricos."
    )
    
    es_simulacion = fecha_seleccionada < hoy

    st.divider()
    st.markdown("### 📊 Umbrales Críticos")
    st.caption("Percentiles Históricos (CHIRPS)")
    st.write(f"🟡 **P90:** {umbral_amarilla:.1f} mm")
    st.write(f"🟠 **P95:** {umbral_naranja:.1f} mm")
    st.write(f"🔴 **P99:** {umbral_roja:.1f} mm")
    dem_opacity = st.slider("Opacidad del DEM en el Mapa", 0.0, 1.0, 0.70)

# -----------------------------------------------------------------------------
# 6. MOTOR DE PRONÓSTICO (REAL O SIMULADO)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=3600)
def obtener_pronostico_5_dias(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=precipitation_sum&timezone=America%2FGuatemala&forecast_days=5"
        respuesta = requests.get(url).json()
        fechas = respuesta['daily']['time']
        lluvias = respuesta['daily']['precipitation_sum']
        df_pronostico = pd.DataFrame({'Fecha': pd.to_datetime(fechas).date, 'Lluvia Diaria (mm)': lluvias})
        return df_pronostico
    except Exception:
        return pd.DataFrame()

# Lógica condicional: ¿Es hoy o el pasado?
if es_simulacion:
    # MODO SIMULADOR: Extraer 5 días de CHIRPS desde la fecha seleccionada
    fecha_fin_sim = fecha_seleccionada + timedelta(days=4)
    df_simulacion = df_historico[(df_historico['fecha'] >= fecha_seleccionada) & (df_historico['fecha'] <= fecha_fin_sim)].copy()
    
    # Adaptar los nombres de columna al formato del gráfico
    df_simulacion = df_simulacion.rename(columns={'fecha': 'Fecha', 'lluvia_mm': 'Lluvia Diaria (mm)'})
    
    # Llenar días faltantes si el dataset se corta
    if len(df_simulacion) < 5:
        fechas_esperadas = [fecha_seleccionada + timedelta(days=i) for i in range(5)]
        df_simulacion = df_simulacion.set_index('Fecha').reindex(fechas_esperadas).fillna(0).reset_index(names='Fecha')

    df_pronostico = df_simulacion
    lluvia_hoy = df_pronostico.iloc[0]['Lluvia Diaria (mm)'] if len(df_pronostico) > 0 else 0
    lluvia_manana = df_pronostico.iloc[1]['Lluvia Diaria (mm)'] if len(df_pronostico) > 1 else 0
else:
    # MODO REAL: API de Open Meteo
    df_pronostico = obtener_pronostico_5_dias(lat_center, lon_center)
    if not df_pronostico.empty:
        lluvia_hoy = df_pronostico.iloc[0]['Lluvia Diaria (mm)']
        lluvia_manana = df_pronostico.iloc[1]['Lluvia Diaria (mm)']
    else:
        lluvia_hoy, lluvia_manana = 0, 0

# Calcular acumulados (sin importar si es real o simulado)
if not df_pronostico.empty:
    df_pronostico['Acumulado (mm)'] = df_pronostico['Lluvia Diaria (mm)'].cumsum()
    saturacion_5d = df_pronostico['Acumulado (mm)'].max()
    alerta_trigger = max(lluvia_hoy, lluvia_manana)
else:
    saturacion_5d, alerta_trigger = 0, 0
    
precip_pronostico = alerta_trigger

# -----------------------------------------------------------------------------
# 7. LÓGICA DE ALERTA OPERATIVA
# -----------------------------------------------------------------------------
if precip_pronostico < umbral_amarilla:
    estado, color_hex = "ESTADO NORMAL", "#28B463"
    mensaje = "Riesgo hidrológico bajo. Los niveles de lluvia no superan los umbrales de peligro."
    protocolo = "🟢 **Monitoreo de rutina.** Revisar drenajes y cuerpos de agua, pero no se requieren acciones extraordinarias."
elif umbral_amarilla <= precip_pronostico < umbral_naranja:
    estado, color_hex = "ALERTA AMARILLA", "#F1C40F"
    mensaje = f"Precipitación de {precip_pronostico:.1f}mm supera el Percentil 90 histórico. Incremento de caudales."
    protocolo = "🟡 **Aviso de Prevención.** Notificar a las Coordinadoras Locales de Cobán y San Pedro Carchá. Restringir actividades en la orilla del Río Cahabón."
elif umbral_naranja <= precip_pronostico < umbral_roja:
    estado, color_hex = "ALERTA NARANJA", "#E67E22"
    mensaje = f"Precipitación de {precip_pronostico:.1f}mm supera el Percentil 95 histórico. Riesgo elevado."
    protocolo = "🟠 **Preparación y Alistamiento.** Identificar y preparar albergues temporales. Monitoreo constante en puentes y zonas bajas propensas a desbordamientos."
else:
    estado, color_hex = "ALERTA ROJA (EXTREMA)", "#E74C3C"
    mensaje = f"¡PELIGRO EXTREMO! Lluvia de {precip_pronostico:.1f}mm supera el Percentil 99 histórico."
    protocolo = "🔴 **Evacuación Preventiva.** Activación inmediata de sistemas de sirenas y evacuación prioritaria en las comunidades vulnerables de la cuenca baja."

# Actualizar el panel lateral con la lluvia del modo actual
with st.sidebar:
    st.write(f"**Lluvia Día 1:** {lluvia_hoy:.1f} mm")
    st.write(f"**Lluvia Día 2:** {lluvia_manana:.1f} mm")

# -----------------------------------------------------------------------------
# 8. ENCABEZADO Y SEMÁFORO
# -----------------------------------------------------------------------------
st.title("🛰️ Sistema de Alerta Temprana - Río Cahabón")

modo_texto = f"📅 MODO SIMULACIÓN HISTÓRICA: {fecha_seleccionada.strftime('%Y-%m-%d')}" if es_simulacion else "🔴 EN VIVO: PRONÓSTICO METEOROLÓGICO"
st.caption(f"**{modo_texto}**")

st.markdown(f"""
<div style="background-color: {color_hex}; padding: 15px; border-radius: 8px; color: white; text-align: center; text-shadow: 1px 1px 2px #000000;">
    <h2 style="margin:0;">🚦 {estado}</h2>
    <p style="font-size: 18px; margin-top:5px; margin-bottom:0px;">{mensaje}</p>
</div>
<br>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 9. PESTAÑAS (TABS) DEL SISTEMA
# -----------------------------------------------------------------------------
tab1, tab2, tab3, tab4 = st.tabs([
    "🗺️ Visor de Riesgo y Alerta", 
    "📊 Análisis Climatológico e Histórico", 
    "🛡️ Plan de Respuesta Operativa", 
    "🔬 Ficha Técnica y Modelos"
])

# ----- PESTAÑA 1: MAPA Y PRONÓSTICO -----
with tab1:
    m1, m2, m3 = st.columns(3)
    m1.metric("Cuenca de Monitoreo", "Río Cahabón (L8)")
    m2.metric("Lluvia de Alerta (Día 1/2)", f"{precip_pronostico:.1f} mm")
    m3.metric("Saturación Total (5 Días)", f"{saturacion_5d:.1f} mm")
    st.write("")

    col_mapa, col_grafico = st.columns([3, 2])
    
    with col_mapa:
        m = folium.Map(location=[lat_center, lon_center], zoom_start=10, tiles="CartoDB positron")
        def get_ee_url(ee_image, vis_params):
            try:
                return ee.Image(ee_image).getMapId(vis_params)['tile_fetcher'].url_format
            except: return ""

        dem_vis = {'min': 200, 'max': 2500, 'palette': ['006600', '002200', 'fff700', 'ab0000', 'b8b8b8', 'ffffff']}
        dem_url = get_ee_url(dem_cuenca, dem_vis)
        if dem_url:
            folium.TileLayer(tiles=dem_url, attr='GEE', name='DEM SRTM', overlay=True, opacity=dem_opacity).add_to(m)

        borde_cuenca = ee.Image().byte().paint(featureCollection=ee.FeatureCollection([cuenca_feat]), color=1, width=4)
        borde_url = get_ee_url(borde_cuenca, {'palette': [color_hex]})
        if borde_url:
            folium.TileLayer(tiles=borde_url, attr='GEE', name=f'Límite ({estado})', overlay=True).add_to(m)

        folium.Marker([lat_center, lon_center], popup=f"Estado: {estado}", icon=folium.Icon(color="red" if color_hex=="#E74C3C" else "blue")).add_to(m)
        folium.LayerControl().add_to(m)
        st_folium(m, width="100%", height=480)

    with col_grafico:
        titulo_grafico = "Simulación Histórica (5 Días)" if es_simulacion else "Pronóstico Extendido (5 Días)"
        st.markdown(f"<h4 style='text-align: center;'>🌧️ {titulo_grafico}</h4>", unsafe_allow_html=True)
        st.caption("Evalúa el impacto de la lluvia diaria y la lluvia acumulada (saturación).")
        
        if not df_pronostico.empty:
            fig_5d = make_subplots(specs=[[{"secondary_y": True}]])
            
            fig_5d.add_trace(
                go.Bar(x=df_pronostico['Fecha'], y=df_pronostico['Lluvia Diaria (mm)'], 
                       name="Lluvia Diaria", marker_color='#3498DB', opacity=0.7),
                secondary_y=False,
            )
            
            fig_5d.add_trace(
                go.Scatter(x=df_pronostico['Fecha'], y=df_pronostico['Acumulado (mm)'], 
                           name="Acumulado (Saturación)", mode='lines+markers',
                           line=dict(color='#E74C3C', width=3), marker=dict(size=8)),
                secondary_y=False,
            )
            
            fig_5d.add_hline(y=umbral_amarilla, line_dash="dash", line_color="#F1C40F", annotation_text=f"P90 ({umbral_amarilla:.1f})")
            fig_5d.add_hline(y=umbral_naranja, line_dash="dash", line_color="#E67E22", annotation_text=f"P95 ({umbral_naranja:.1f})")
            fig_5d.add_hline(y=umbral_roja, line_dash="dash", line_color="#E74C3C", annotation_text=f"P99 ({umbral_roja:.1f})")

            fig_5d.update_layout(
                height=420, showlegend=True, 
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
                template="plotly_white", margin=dict(l=0, r=0, t=30, b=0)
            )
            max_y = max(df_pronostico['Acumulado (mm)'].max(), umbral_roja) + 15
            fig_5d.update_yaxes(title_text="Precipitación (mm)", range=[0, max_y], secondary_y=False)
            
            # Formatear el eje X para que siempre se vea bien como fecha
            fig_5d.update_xaxes(type='category', tickformat="%Y-%m-%d")
            
            st.plotly_chart(fig_5d, use_container_width=True)

# ----- PESTAÑA 2: HISTÓRICO Y DESCARGAS -----
with tab2:
    if not df_historico.empty:
        c_agg1, c_agg2 = st.columns([1, 3])
        with c_agg1:
            agg_temporal = st.radio("Agregación Temporal de la Gráfica:", ["Diario", "Mensual", "Anual"], horizontal=True)
        
        # Transformar para graficar
        df_limpio = df_historico.copy()
        df_limpio['fecha'] = pd.to_datetime(df_limpio['fecha'])
        df_limpio = df_limpio.rename(columns={'fecha': 'Fecha', 'lluvia_mm': 'Precipitación (mm)'})
        
        if agg_temporal == "Diario":
            df_plot = df_limpio.copy()
        elif agg_temporal == "Mensual":
            df_plot = df_limpio.resample('ME', on='Fecha').sum().reset_index()
        elif agg_temporal == "Anual":
            df_plot = df_limpio.resample('YE', on='Fecha').sum().reset_index()

        fig_ts = px.line(df_plot, x='Fecha', y='Precipitación (mm)', title=f"Registro Histórico {agg_temporal}")
        fig_ts.update_traces(line_color='#2E86C1')
        
        # Umbrales solo se muestran en la vista diaria
        if agg_temporal == "Diario":
            fig_ts.add_hline(y=umbral_amarilla, line_dash="dot", line_color="#F1C40F", annotation_text="P90")
            fig_ts.add_hline(y=umbral_naranja, line_dash="dot", line_color="#E67E22", annotation_text="P95")
            fig_ts.add_hline(y=umbral_roja, line_dash="dot", line_color="#E74C3C", annotation_text="P99")
        
        st.plotly_chart(fig_ts, use_container_width=True)
        st.divider()

        c_tbl1, c_tbl2 = st.columns([2, 1])
        with c_tbl1:
            st.markdown("#### 🚨 Top 10 Eventos Extremos en la Cuenca")
            top_10 = df_limpio.nlargest(10, 'Precipitación (mm)')
            top_10['Fecha'] = top_10['Fecha'].dt.strftime('%Y-%m-%d')
            st.dataframe(top_10, hide_index=True, use_container_width=True)
            
        with c_tbl2:
            st.markdown("#### 📥 Exportar Base de Datos")
            st.info("Descargue la serie temporal completa original para análisis externo en QGIS o Excel.")
            csv_data = df_limpio.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Descargar Dataset CHIRPS (CSV)", 
                data=csv_data, 
                file_name='historico_lluvia_cahabon.csv', 
                mime='text/csv'
            )

# ----- PESTAÑA 3: PROTOCOLO OPERATIVO -----
with tab3:
    st.subheader("🛡️ Protocolo de Actuación Municipal (CONRED)")
    st.info(f"**Estado del Sistema:** {estado}")
    st.markdown(f"### Acción Recomendada:\n{protocolo}")
    
    st.divider()
    st.markdown("""
    #### 📍 Zonas Prioritarias de Monitoreo (Río Cahabón)
    El comportamiento de las inundaciones en esta cuenca requiere vigilar tres áreas clave:
    1. **Cuenca Alta (Áreas escarpadas):** Riesgo primario de deslizamientos de tierra que pueden bloquear el flujo de agua o afectar carreteras locales.
    2. **Cuenca Media (Zona Urbana):** Paso del río por los municipios densamente poblados de **San Pedro Carchá** y **Cobán**. El crecimiento de la huella urbana reduce la infiltración y acelera la crecida.
    3. **Cuenca Baja (Planicies):** Comunidades en la ruta hacia Santa María Cahabón, altamente vulnerables a inundaciones repentinas y pérdida de cultivos.
    """)

# ----- PESTAÑA 4: FICHA TÉCNICA -----
with tab4:
    st.subheader("🔬 Metodología y Ficha Técnica del Geoportal")
    st.markdown("""
    Este Sistema de Alerta Temprana ha sido desarrollado como proyecto integrador del programa MCHV-513 del CATIE.
    
    * **Motor Geoespacial:** Se utiliza la API de **Google Earth Engine** y librerías de Python en la nube para procesar y recortar el Modelo Digital de Elevación (DEM-SRTM).
    * **Análisis Estadístico:** Los umbrales de peligro (P90, P95, P99) fueron parametrizados extrayendo y promediando espacialmente **44 años de imágenes satelitales diarias** de la colección **CHIRPS v2.0** para la cuenca específica.
    * **Modo En Vivo (Open-Meteo):** El portal se conecta a la API de Open-Meteo, extrayendo el ensamble de los modelos **GFS** (NOAA) e **ICON** (DWD) para las coordenadas centroides de la cuenca.
    * **Modo Simulador Histórico:** Permite "viajar en el tiempo" seleccionando fechas del pasado para observar cómo se hubiera comportado el sistema durante tormentas históricas registradas en la base de datos CHIRPS.
    * **Saturación del Suelo:** La gráfica de pronóstico/simulación acumula la lluvia progresiva, permitiendo a los tomadores de decisiones visualizar el riesgo de crecidas por saturación de humedad antecedente.
    """)
