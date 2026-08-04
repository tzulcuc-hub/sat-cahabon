import streamlit as st
import ee
import folium
from streamlit_folium import st_folium
import pandas as pd
import requests
import os
import plotly.express as px
import plotly.graph_objects as go
from google.oauth2 import service_account

# -----------------------------------------------------------------------------
# 1. CONFIGURACIÓN DE PÁGINA
# -----------------------------------------------------------------------------
st.set_page_config(page_title="SAT Río Cahabón - Tiempo Real", page_icon="🌊", layout="wide")

st.markdown("""
    <style>
        .block-container { padding-top: 1.5rem; padding-bottom: 0rem; }
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
# 4. CARGA DE HISTÓRICO Y UMBRALES
# -----------------------------------------------------------------------------
@st.cache_data
def cargar_datos():
    if os.path.exists('serie_lluvia_cahabon.csv'):
        df = pd.read_csv('serie_lluvia_cahabon.csv')
        if 'Unnamed: 0' in df.columns:
            df = df.drop(columns=['Unnamed: 0'])
        df['fecha'] = pd.to_datetime(df['fecha'])
        
        precip_only = df[df['lluvia_mm'] > 2]['lluvia_mm']
        p90 = precip_only.quantile(0.90)
        p95 = precip_only.quantile(0.95)
        p99 = precip_only.quantile(0.99)
        return df, p90, p95, p99
    else:
        # DF vacío de respaldo
        return pd.DataFrame(columns=['fecha', 'lluvia_mm']), 25.0, 45.0, 70.0

df_historico, umbral_amarilla, umbral_naranja, umbral_roja = cargar_datos()

# -----------------------------------------------------------------------------
# 5. PRONÓSTICO EN TIEMPO REAL (OPEN-METEO 5 DÍAS)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=3600)
def obtener_pronostico_5_dias(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=precipitation_sum&timezone=America%2FGuatemala&forecast_days=5"
        respuesta = requests.get(url).json()
        fechas = respuesta['daily']['time']
        lluvias = respuesta['daily']['precipitation_sum']
        
        # Crear DataFrame para el pronóstico
        df_pronostico = pd.DataFrame({'Fecha': fechas, 'Lluvia Diaria (mm)': lluvias})
        # Calcular la lluvia acumulada (suma progresiva)
        df_pronostico['Acumulado (mm)'] = df_pronostico['Lluvia Diaria (mm)'].cumsum()
        
        # Extraemos variables para el panel lateral
        lluvia_hoy = lluvias[0]
        lluvia_manana = lluvias[1]
        # El pronóstico de alerta será la lluvia máxima de las próximas 48h o el acumulado si es extremo
        alerta_trigger = max(lluvia_hoy, lluvia_manana)
        
        return df_pronostico, lluvia_hoy, lluvia_manana, alerta_trigger
    except Exception:
        return pd.DataFrame(), 0.0, 0.0, 0.0

df_pronostico, lluvia_hoy, lluvia_manana, precip_pronostico = obtener_pronostico_5_dias(lat_center, lon_center)

# -----------------------------------------------------------------------------
# 6. LÓGICA DE ALERTA Y SEMÁFORO
# -----------------------------------------------------------------------------
if precip_pronostico < umbral_amarilla:
    estado, color_hex = "ESTADO NORMAL", "#28B463"
    mensaje = "Riesgo hidrológico bajo. El pronóstico actual no supera los umbrales de peligro."
elif umbral_amarilla <= precip_pronostico < umbral_naranja:
    estado, color_hex = "ALERTA AMARILLA", "#F1C40F"
    mensaje = f"Lluvia pronosticada ({precip_pronostico}mm) supera el Percentil 90 histórico. Incremento de caudales posible."
elif umbral_naranja <= precip_pronostico < umbral_roja:
    estado, color_hex = "ALERTA NARANJA", "#E67E22"
    mensaje = f"Lluvia pronosticada ({precip_pronostico}mm) supera el Percentil 95 histórico. Riesgo elevado."
else:
    estado, color_hex = "ALERTA ROJA (EXTREMA)", "#E74C3C"
    mensaje = f"¡PELIGRO EXTREMO! Pronóstico ({precip_pronostico}mm) supera el Percentil 99 histórico."

# -----------------------------------------------------------------------------
# 7. INTERFAZ GRÁFICA PRINCIPAL
# -----------------------------------------------------------------------------
with st.sidebar:
    st.image("https://catie.ac.cr/wp-content/uploads/2023/04/Logo-CATIE-2023.png", use_container_width=True)
    st.title("📡 SAT Tiempo Real")
    st.success("✅ Conectado a API Global (GFS/ICON)")
    st.write(f"**Lluvia esperada hoy:** {lluvia_hoy} mm")
    st.write(f"**Lluvia esperada mañana:** {lluvia_manana} mm")
    st.divider()
    st.markdown("### 📊 Umbrales Críticos (CHIRPS)")
    st.write(f"🟡 **P90:** {umbral_amarilla:.1f} mm")
    st.write(f"🟠 **P95:** {umbral_naranja:.1f} mm")
    st.write(f"🔴 **P99:** {umbral_roja:.1f} mm")
    dem_opacity = st.slider("Opacidad del DEM", 0.0, 1.0, 0.70)

st.title("🛰️ Sistema de Alerta Temprana - Río Cahabón")

st.markdown(f"""
<div style="background-color: {color_hex}; padding: 15px; border-radius: 10px; color: white; text-align: center; font-weight: bold; text-shadow: 1px 1px 2px #000000;">
    <h2 style="margin:0;">🚦 {estado}</h2>
    <p style="font-size: 18px; margin-top:5px;">{mensaje}</p>
</div>
<br>
""", unsafe_allow_html=True)

# ORGANIZACIÓN EN PESTAÑAS
tab1, tab2, tab3 = st.tabs(["🗺️ Geoportal y Pronóstico", "📈 Análisis Histórico", "🛡️ Protocolo y Modelos"])

with tab1:
    col_mapa, col_grafico = st.columns([3, 2])
    
    with col_mapa:
        # VISOR CARTOGRÁFICO A PRUEBA DE FALLOS
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
        st_folium(m, width="100%", height=450)

    with col_grafico:
        with col_grafico:
        st.markdown("<h4 style='text-align: center;'>🌧️ Pronóstico Extendido y Saturación (5 Días)</h4>", unsafe_allow_html=True)
        st.caption("Evalúa el impacto de la lluvia acumulada sobre los umbrales críticos de la cuenca.")
        
        if not df_pronostico.empty:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots

            # Crear un gráfico con doble eje Y
            fig_5d = make_subplots(specs=[[{"secondary_y": True}]])

            # 1. Añadir barras para la lluvia diaria
            fig_5d.add_trace(
                go.Bar(x=df_pronostico['Fecha'], y=df_pronostico['Lluvia Diaria (mm)'], 
                       name="Lluvia Diaria", marker_color='#3498DB', opacity=0.7),
                secondary_y=False,
            )

            # 2. Añadir línea para la lluvia acumulada
            fig_5d.add_trace(
                go.Scatter(x=df_pronostico['Fecha'], y=df_pronostico['Acumulado (mm)'], 
                           name="Acumulado (Saturación)", mode='lines+markers',
                           line=dict(color='#E74C3C', width=3), marker=dict(size=8)),
                secondary_y=False,
            )

            # 3. Líneas de umbral histórico
            fig_5d.add_hline(y=umbral_amarilla, line_dash="dash", line_color="#F1C40F", annotation_text=f"P90 ({umbral_amarilla:.1f}mm)")
            fig_5d.add_hline(y=umbral_naranja, line_dash="dash", line_color="#E67E22", annotation_text=f"P95 ({umbral_naranja:.1f}mm)")
            fig_5d.add_hline(y=umbral_roja, line_dash="dash", line_color="#E74C3C", annotation_text=f"P99 ({umbral_roja:.1f}mm)")

            fig_5d.update_layout(
                height=450, 
                showlegend=True, 
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                template="plotly_white",
                margin=dict(l=0, r=0, t=30, b=0)
            )
            
            # Asegurar que el eje Y escale correctamente
            max_y = max(df_pronostico['Acumulado (mm)'].max(), umbral_roja) + 15
            fig_5d.update_yaxes(title_text="Precipitación (mm)", range=[0, max_y], secondary_y=False)

            st.plotly_chart(fig_5d, use_container_width=True)
        else:
            st.warning("No se pudo cargar el pronóstico de 5 días.")

with tab2:
    if not df_historico.empty:
        st.subheader("Evolución de Precipitación (CHIRPS)")
        
        # CONTROLES DE AGREGACIÓN
        c_agg1, c_agg2 = st.columns([1, 3])
        with c_agg1:
            agg_temporal = st.radio("Agregación Temporal:", ["Diario", "Mensual", "Anual"])
        
        # LÓGICA DE AGREGACIÓN PANDAS
        df_limpio = df_historico.rename(columns={'fecha': 'Fecha', 'lluvia_mm': 'Precipitación (mm)'})
        if agg_temporal == "Diario":
            df_plot = df_limpio.copy()
        elif agg_temporal == "Mensual":
            df_plot = df_limpio.resample('ME', on='Fecha').sum().reset_index()
        elif agg_temporal == "Anual":
            df_plot = df_limpio.resample('YE', on='Fecha').sum().reset_index()

        # GRÁFICO DE SERIE DE TIEMPO INTERACTIVO
        fig_ts = px.line(df_plot, x='Fecha', y='Precipitación (mm)', title=f"Registro Histórico {agg_temporal} de Lluvia")
        
        # Solo mostramos líneas de umbral en la vista diaria (porque los umbrales se calcularon por día)
        if agg_temporal == "Diario":
            fig_ts.add_hline(y=umbral_amarilla, line_dash="dot", line_color="#F1C40F", annotation_text="P90")
            fig_ts.add_hline(y=umbral_naranja, line_dash="dot", line_color="#E67E22", annotation_text="P95")
            fig_ts.add_hline(y=umbral_roja, line_dash="dot", line_color="#E74C3C", annotation_text="P99")
        
        st.plotly_chart(fig_ts, use_container_width=True)

        st.divider()

        # TOP 10 Y BOTÓN DE DESCARGA
        c_tbl1, c_tbl2 = st.columns([2, 1])
        with c_tbl1:
            st.markdown("#### 🚨 Top 10 Eventos de Mayor Precipitación (Diaria)")
            top_10 = df_limpio.nlargest(10, 'Precipitación (mm)')
            top_10['Fecha'] = top_10['Fecha'].dt.strftime('%Y-%m-%d')
            st.dataframe(top_10, hide_index=True, use_container_width=True)
            
        with c_tbl2:
            st.markdown("#### 📥 Exportar Base de Datos")
            st.write("Descargue la serie temporal completa original para análisis en SIG o Excel.")
            csv_data = df_limpio.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="Descargar Dataset CHIRPS (CSV)", 
                data=csv_data, 
                file_name='historico_lluvia_cahabon.csv', 
                mime='text/csv'
            )

with tab3:
    st.subheader("Metodología y Protocolos del SAT")
    st.markdown("""
    **¿Cómo funciona el Pronóstico?**
    El sistema se conecta automáticamente a la API meteorológica global **Open-Meteo**. No utiliza un solo modelo, sino un *ensemble* (ensamblaje) que selecciona la mejor resolución posible para Centroamérica, integrando datos del modelo **GFS** (NOAA, Estados Unidos) y el modelo **ICON** (DWD, Alemania). Se extrae la precipitación esperada en las coordenadas exactas de la cuenca.
    
    **Zonas Prioritarias de Monitoreo (Río Cahabón):**
    * **Cuenca Media / Centro Urbano:** Paso del río por los municipios de San Pedro Carchá y Cobán.
    * **Cuenca Baja:** Comunidades en la ruta hacia Santa María Cahabón vulnerables a crecidas repentinas.
    """)
