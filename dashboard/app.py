import base64
import json
import math
from io import BytesIO
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from branca.colormap import LinearColormap
from branca.element import Element, MacroElement
from jinja2 import Template
from streamlit_folium import st_folium


st.set_page_config(
    page_title="Tallinn Locker Accessibility",
    page_icon="📦",
    layout="wide",
)

# ─── Paths ───────────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).parent / "data"

LOCKERS_CSV      = DATA_DIR / "lockers.csv"
BOUNDARY_GEOJSON = DATA_DIR / "tallinn.geojson"
NETWORK_MAIN_GEOJSON  = DATA_DIR / "network_main_roads.geojson"
NETWORK_LOCAL_GEOJSON = DATA_DIR / "network_local_streets.geojson"
NETWORK_PED_GEOJSON   = DATA_DIR / "network_pedestrian_paths.geojson"
POPGRID_PNG      = DATA_DIR / "popgrid.png"
POPGRID_META     = DATA_DIR / "popgrid_meta.json"

MAP_CENTER = (59.437, 24.753)
MAP_ZOOM = 11


# ─── Data loaders (local static files) ──────────────────────────────────────

@st.cache_data(ttl=300)
def load_kpis() -> pd.DataFrame:
    df = pd.read_csv(LOCKERS_CSV)
    return pd.DataFrame([{
        "lockers_scored": int(len(df)),
        "avg_net_10min":  int(round(df["pop_10min_network"].mean())),
        "avg_buffer":     int(round(df["pop_400m_buffer"].mean())),
        "avg_gap":        round(float(df["buffer_network_gap"].mean()), 1),
    }])


@st.cache_data(ttl=300)
def load_lockers() -> pd.DataFrame:
    return pd.read_csv(LOCKERS_CSV)


@st.cache_data(show_spinner="Loading Tallinn boundary…")
def load_tallinn_boundary() -> dict:
    return json.loads(BOUNDARY_GEOJSON.read_text())


@st.cache_data(show_spinner="Loading walkable network…")
def load_network_main() -> dict:
    return json.loads(NETWORK_MAIN_GEOJSON.read_text())


@st.cache_data(show_spinner="Loading walkable network…")
def load_network_local() -> dict:
    return json.loads(NETWORK_LOCAL_GEOJSON.read_text())


@st.cache_data(show_spinner="Loading walkable network…")
def load_network_ped() -> dict:
    return json.loads(NETWORK_PED_GEOJSON.read_text())


@st.cache_data(show_spinner="Loading population grid…")
def load_population_grid():
    png_b64 = base64.b64encode(POPGRID_PNG.read_bytes()).decode()
    meta = json.loads(POPGRID_META.read_text())
    return png_b64, meta["bounds"], meta["stats"]


# ─── Map helpers ─────────────────────────────────────────────────────────────

def _outside_mask(boundary_geojson: dict) -> dict:
    from shapely.geometry import box, mapping, shape
    geom = shape(boundary_geojson["features"][0]["geometry"])
    world = box(15.0, 55.0, 35.0, 65.0)
    return mapping(world.difference(geom))


class _ResetControl(MacroElement):
    """In-map reset button (pure JS, no Streamlit rerun)."""

    _template = Template("""
        {% macro script(this, kwargs) %}
        (function() {
            var ResetControl = L.Control.extend({
                options: { position: 'topleft' },
                onAdd: function(map) {
                    var c = L.DomUtil.create('div', 'leaflet-bar leaflet-control');
                    var a = L.DomUtil.create('a', '', c);
                    a.innerHTML = '\\uD83C\\uDFAF Reset View';
                    a.title = 'Reset map to Tallinn centre';
                    a.href = '#';
                    a.style.cssText = 'font-size:12px;text-align:center;line-height:26px;'
                                    + 'height:26px;width:auto;padding:0 10px;'
                                    + 'text-decoration:none;color:#333;font-weight:500;';
                    L.DomEvent.on(a, 'click', function(e) {
                        L.DomEvent.stop(e);
                        map.setView({{ this.center }}, {{ this.zoom }});
                    });
                    return c;
                }
            });
            new ResetControl().addTo({{ this._parent.get_name() }});
        })();
        {% endmacro %}
    """)

    def __init__(self, center, zoom):
        super().__init__()
        self._name = "ResetControl"
        self.center = list(center)
        self.zoom = zoom


class _MapLegends(MacroElement):
    """Bottom-left legends stack — buffers (top), pop-grid (below)."""

    _template = Template("""
        {% macro script(this, kwargs) %}
        (function() {
            var map = {{ this._parent.get_name() }};

            var baseCss =
                "background:rgba(255,255,255,0.92);" +
                "padding:8px 10px;border-radius:4px;font-size:11px;" +
                "box-shadow:0 1px 4px rgba(0,0,0,0.35);margin-bottom:6px;";

            var LegendControl = L.Control.extend({
                options: { position: 'bottomleft' },
                onAdd: function(m) {
                    var c = L.DomUtil.create('div', 'leaflet-control');
                    c.style.background = 'transparent';
                    c.style.border = 'none';
                    c.style.boxShadow = 'none';

                    {% if this.has_buffer %}
                    var bufEl = document.createElement('div');
                    bufEl.id = 'buffer-legend-inner';
                    bufEl.style.cssText = baseCss +
                        'display:{{ "block" if this.buffer_visible else "none" }};';
                    bufEl.innerHTML =
                        '<div style="font-weight:600;margin-bottom:4px;color:#222;">' +
                        {{ this.buffer_title | tojson }} + '</div>' +
                        '<div style="width:160px;height:12px;border-radius:2px;' +
                        'background:linear-gradient(to right,' +
                        '#ffffcc 0%,#fed976 25%,#fd8d3c 50%,#e31a1c 75%,#800026 100%);' +
                        '"></div>' +
                        '<div style="display:flex;justify-content:space-between;' +
                        'font-size:10px;color:#444;margin-top:2px;">' +
                        '<span>' + {{ this.buffer_low | tojson }} + '</span>' +
                        '<span>' + {{ this.buffer_high | tojson }} + '</span></div>';
                    c.appendChild(bufEl);
                    {% endif %}

                    {% if this.has_popgrid %}
                    var popEl = document.createElement('div');
                    popEl.id = 'pop-grid-legend-inner';
                    popEl.style.cssText = baseCss +
                        'display:{{ "block" if this.popgrid_visible else "none" }};';
                    popEl.innerHTML =
                        '<div style="font-weight:600;margin-bottom:4px;color:#222;">' +
                        {{ this.popgrid_title | tojson }} + '</div>' +
                        '<div style="width:160px;height:12px;border-radius:2px;' +
                        'background:linear-gradient(to right,' +
                        '#440154 0%,#3b528b 25%,#21918c 50%,#5ec962 75%,#fde725 100%);' +
                        '"></div>' +
                        '<div style="display:flex;justify-content:space-between;' +
                        'font-size:10px;color:#444;margin-top:2px;">' +
                        '<span>' + {{ this.popgrid_low | tojson }} + '</span>' +
                        '<span>' + {{ this.popgrid_high | tojson }} + '</span></div>';
                    c.appendChild(popEl);
                    {% endif %}

                    return c;
                }
            });
            new LegendControl().addTo(map);

            function toggle(id, show) {
                var el = document.getElementById(id);
                if (el) el.style.display = show ? 'block' : 'none';
            }

            map.on('overlayadd', function(e) {
                if (!e.name) return;
                if (e.name.indexOf('400 m buffers') !== -1) {
                    toggle('buffer-legend-inner', true);
                }
                if (e.name.indexOf('Population grid') !== -1) {
                    toggle('pop-grid-legend-inner', true);
                }
            });
            map.on('overlayremove', function(e) {
                if (!e.name) return;
                if (e.name.indexOf('400 m buffers') !== -1) {
                    toggle('buffer-legend-inner', false);
                }
                if (e.name.indexOf('Population grid') !== -1) {
                    toggle('pop-grid-legend-inner', false);
                }
            });
        })();
        {% endmacro %}
    """)

    def __init__(self, buffer_legend=None, popgrid_stats=None,
                 buffer_visible=True, popgrid_visible=False):
        super().__init__()
        self._name = "MapLegends"

        self.has_buffer = buffer_legend is not None
        self.has_popgrid = popgrid_stats is not None
        self.buffer_visible = buffer_visible
        self.popgrid_visible = popgrid_visible

        if buffer_legend:
            self.buffer_title = buffer_legend["title"]
            self.buffer_low = buffer_legend["low"]
            self.buffer_high = buffer_legend["high"]
        else:
            self.buffer_title = self.buffer_low = self.buffer_high = ""

        if popgrid_stats:
            self.popgrid_title = "Population density (WorldPop)"
            self.popgrid_low = f"Low: {popgrid_stats['low']:.1f}"
            self.popgrid_high = f"High: {popgrid_stats['high']:.0f}"
        else:
            self.popgrid_title = self.popgrid_low = self.popgrid_high = ""


class _NetworkRadioControl(MacroElement):
    """
    Top-right panel:
      • radio for walk-time threshold
      • three network-category checkboxes (main, local, pedestrian)
      • two auxiliary-layer checkboxes (marker pins, population grid)
    All toggles are pure JS — no Streamlit rerun.
    """

    _template = Template("""
        {% macro script(this, kwargs) %}
        (function() {
            var map = {{ this._parent.get_name() }};
            var fg_names = {
                5:  '{{ this.fg5_name }}',
                10: '{{ this.fg10_name }}',
                15: '{{ this.fg15_name }}'
            };
            var net_names = {
                'main':  '{{ this.net_main_name }}',
                'local': '{{ this.net_local_name }}',
                'ped':   '{{ this.net_ped_name }}'
            };
            var markers_name = '{{ this.markers_name }}';
            var popgrid_name = '{{ this.popgrid_name }}';
            var stats = {{ this.stats | tojson }};

            function getLayer(name) {
                return window[name] || eval(name);
            }

            var Panel = L.Control.extend({
                options: { position: 'topright' },
                onAdd: function(m) {
                    var c = L.DomUtil.create('div', 'leaflet-control');
                    c.style.cssText =
                        'background:#fff;padding:10px 12px;border-radius:5px;' +
                        'box-shadow:0 1px 5px rgba(0,0,0,0.4);font-size:12px;' +
                        'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;' +
                        'color:#333;line-height:1.3;';

                    function section(title) {
                        return '<div style="font-weight:600;font-size:12px;' +
                               'margin-bottom:6px;padding-bottom:5px;color:#222;' +
                               'border-bottom:1px solid #e2e2e2;">' + title + '</div>';
                    }
                    function divider() {
                        return '<div style="border-top:1px solid #e2e2e2;' +
                               'margin:8px 0 6px 0;"></div>';
                    }
                    function radioRow(value, label, checked) {
                        return '<label style="display:flex;align-items:center;' +
                               'gap:6px;margin-bottom:5px;cursor:pointer;color:#333;">' +
                               '<input type="radio" name="wt_radio" value="' + value + '"' +
                               (checked ? ' checked' : '') + ' style="margin:0;cursor:pointer;"> ' +
                               label + '</label>';
                    }
                    function checkRow(cls, target, label, checked) {
                        return '<label style="display:flex;align-items:center;' +
                               'gap:6px;margin-bottom:5px;cursor:pointer;color:#333;">' +
                               '<input type="checkbox" class="' + cls + '" ' +
                               'data-target="' + target + '"' + (checked ? ' checked' : '') +
                               ' style="margin:0;cursor:pointer;"> ' + label + '</label>';
                    }

                    c.innerHTML =
                        section('\\uD83D\\uDEB6 Walk time') +
                        radioRow(5,  '5 min',  false) +
                        radioRow(10, '10 min', true) +
                        radioRow(15, '15 min', false) +
                        divider() +
                        section('\\uD83D\\uDEE3\\uFE0F Network') +
                        checkRow('wt_net', 'main',  'Main roads',       true) +
                        checkRow('wt_net', 'local', 'Local streets',    false) +
                        checkRow('wt_net', 'ped',   'Pedestrian paths', false) +
                        divider() +
                        section('\\uD83D\\uDCCC Other layers') +
                        checkRow('wt_toggle', 'markers', 'Locker locations',  false) +
                        checkRow('wt_toggle', 'popgrid', 'Population grid',   false);

                    L.DomEvent.disableClickPropagation(c);
                    L.DomEvent.disableScrollPropagation(c);
                    return c;
                }
            });
            new Panel().addTo(map);

            var LegendControl = L.Control.extend({
                options: { position: 'bottomleft' },
                onAdd: function(m) {
                    var c = L.DomUtil.create('div', 'leaflet-control');
                    c.style.cssText = 'background:transparent;border:none;box-shadow:none;';

                    [5, 10, 15].forEach(function(t) {
                        var s = stats[String(t)] || [0, 1];
                        var el = document.createElement('div');
                        el.id = 'wt-legend-' + t;
                        el.style.cssText =
                            'background:rgba(255,255,255,0.92);padding:8px 10px;' +
                            'border-radius:4px;font-size:11px;' +
                            'box-shadow:0 1px 4px rgba(0,0,0,0.35);margin-bottom:6px;' +
                            'display:' + (t === 10 ? 'block' : 'none') + ';';
                        el.innerHTML =
                            '<div style="font-weight:600;margin-bottom:4px;color:#222;">' +
                            'Residents reachable in ' + t + ' min on foot</div>' +
                            '<div style="width:160px;height:12px;border-radius:2px;' +
                            'background:linear-gradient(to right,' +
                            '#d73027 0%,#fc8d59 25%,#fee08b 50%,#a6d96a 75%,#1a9641 100%);' +
                            '"></div>' +
                            '<div style="display:flex;justify-content:space-between;' +
                            'font-size:10px;color:#444;margin-top:2px;">' +
                            '<span>Low: ' + Math.round(s[0]).toLocaleString() + '</span>' +
                            '<span>High: ' + Math.round(s[1]).toLocaleString() + '</span></div>';
                        c.appendChild(el);
                    });
                    return c;
                }
            });
            new LegendControl().addTo(map);

            function show(threshold) {
                [5, 10, 15].forEach(function(t) {
                    var fg = getLayer(fg_names[t]);
                    if (fg) {
                        if (t === threshold) {
                            if (!map.hasLayer(fg)) map.addLayer(fg);
                        } else {
                            if (map.hasLayer(fg)) map.removeLayer(fg);
                        }
                    }
                    var el = document.getElementById('wt-legend-' + t);
                    if (el) el.style.display = (t === threshold) ? 'block' : 'none';
                });
            }

            var container = map.getContainer();
            container.addEventListener('change', function(e) {
                if (e.target && e.target.name === 'wt_radio') {
                    show(parseInt(e.target.value));
                    return;
                }
                if (e.target && e.target.classList) {
                    if (e.target.classList.contains('wt_net')) {
                        var t = e.target.getAttribute('data-target');
                        var layer = getLayer(net_names[t]);
                        if (layer) {
                            if (e.target.checked) {
                                if (!map.hasLayer(layer)) map.addLayer(layer);
                            } else {
                                if (map.hasLayer(layer)) map.removeLayer(layer);
                            }
                        }
                    }
                    if (e.target.classList.contains('wt_toggle')) {
                        var t2 = e.target.getAttribute('data-target');
                        var layer2 = null;
                        if (t2 === 'markers') layer2 = getLayer(markers_name);
                        if (t2 === 'popgrid') layer2 = getLayer(popgrid_name);
                        if (layer2) {
                            if (e.target.checked) {
                                if (!map.hasLayer(layer2)) map.addLayer(layer2);
                            } else {
                                if (map.hasLayer(layer2)) map.removeLayer(layer2);
                            }
                        }
                    }
                }
            });

            show(10);

            // Hide non-default layers (matches the unchecked checkboxes above)
            ['local', 'ped'].forEach(function(k) {
                var lyr = getLayer(net_names[k]);
                if (lyr && map.hasLayer(lyr)) map.removeLayer(lyr);
            });
            [markers_name, popgrid_name].forEach(function(nm) {
                var lyr = getLayer(nm);
                if (lyr && map.hasLayer(lyr)) map.removeLayer(lyr);
            });
        })();
        {% endmacro %}
    """)

    def __init__(self, fg5_name, fg10_name, fg15_name,
                 net_main_name, net_local_name, net_ped_name,
                 markers_name, popgrid_name, stats):
        super().__init__()
        self._name = "NetworkRadioControl"
        self.fg5_name = fg5_name
        self.fg10_name = fg10_name
        self.fg15_name = fg15_name
        self.net_main_name = net_main_name
        self.net_local_name = net_local_name
        self.net_ped_name = net_ped_name
        self.markers_name = markers_name
        self.popgrid_name = popgrid_name
        self.stats = {str(k): [float(v[0]), float(v[1])]
                      for k, v in stats.items()}


def _base_map(show_network: bool = True,
              show_pop_grid: bool = False,
              prepend_layers=None,
              buffer_legend=None,
              show_layer_control: bool = True) -> folium.Map:
    """Base Folium map with configurable overlays."""
    m = folium.Map(
        location=list(MAP_CENTER),
        zoom_start=MAP_ZOOM,
        tiles=None,
        control_scale=True,
    )

    carto_key = ""
    try:
        carto_key = st.secrets.get("CARTO_API_KEY", "")
    except Exception:
        pass
    if not carto_key:
        import os
        carto_key = os.getenv("CARTO_API_KEY", "").strip()

    if carto_key:
        tiles_url = (
            "https://{s}.basemaps.cartocdn.com/dark_all/"
            "{z}/{x}/{y}{r}.png?key=" + carto_key
        )
        folium.TileLayer(
            tiles=tiles_url,
            name="CartoDB Dark Matter",
            attr="© OpenStreetMap contributors, © CARTO",
            opacity=0.95,
            detect_retina=True,
            control=False,
        ).add_to(m)
    else:
        folium.TileLayer(
            tiles="OpenStreetMap",
            name="OpenStreetMap",
            opacity=0.95,
            control=False,
        ).add_to(m)

    m.get_root().header.add_child(Element(
        "<style>"
        ".leaflet-bar a,"
        ".leaflet-bar a:hover,"
        ".leaflet-bar a:focus,"
        ".leaflet-bar a:active {"
        "  text-decoration: none !important;"
        "  outline: none !important;"
        "  box-shadow: none !important;"
        "}"
        "</style>"
    ))

    if prepend_layers:
        for layer in prepend_layers:
            layer.add_to(m)

    pop_stats = None
    if show_pop_grid:
        try:
            png_b64, bounds, pop_stats = load_population_grid()
            folium.raster_layers.ImageOverlay(
                image=f"data:image/png;base64,{png_b64}",
                bounds=bounds,
                opacity=0.75,
                name="Population grid (WorldPop)",
                interactive=False,
                show=False,
            ).add_to(m)
        except Exception as e:
            st.warning(f"Population grid unavailable: {e}")

    if show_network:
        try:
            net = load_network_main()
            folium.GeoJson(
                net,
                name="Walkable network (sampled)",
                style_function=lambda _: {
                    "color": "#000000",
                    "weight": 1,
                    "opacity": 0.9,
                },
                control=False,
            ).add_to(m)
        except Exception as e:
            st.warning(f"Network overlay unavailable: {e}")

    try:
        bnd = load_tallinn_boundary()
        folium.GeoJson(
            bnd,
            name="Tallinn boundary",
            style_function=lambda _: {
                "color": "#000000",
                "weight": 1,
                "opacity": 1,
                "fill": False,
            },
            control=False,
        ).add_to(m)

        folium.GeoJson(
            _outside_mask(bnd),
            name="Outside study area",
            style_function=lambda _: {
                "fillColor": "#f2f2f2",
                "color": "#d0d0d0",
                "weight": 0.6,
                "fillOpacity": 0.75,
            },
            interactive=False,
            control=False,
        ).add_to(m)
    except Exception as e:
        st.warning(f"Boundary / mask overlay unavailable: {e}")

    m.add_child(_ResetControl(MAP_CENTER, MAP_ZOOM))

    if buffer_legend or pop_stats:
        m.add_child(_MapLegends(
            buffer_legend=buffer_legend,
            popgrid_stats=pop_stats,
            buffer_visible=buffer_legend is not None,
            popgrid_visible=False,
        ))

    if show_layer_control:
        folium.LayerControl(collapsed=False, position="topright").add_to(m)
    return m


def _radius_for(value: float, value_max: float,
                min_px: float = 4.0, max_px: float = 18.0) -> float:
    if value_max <= 0:
        return min_px
    return min_px + (max_px - min_px) * math.sqrt(max(value, 0) / value_max)


# ─── Three maps ──────────────────────────────────────────────────────────────

def build_buffer_map(lockers: pd.DataFrame,
                     show_pop_grid: bool = False) -> folium.Map:
    vmax = float(lockers["pop_400m_buffer"].max() or 1)
    vmin = float(lockers["pop_400m_buffer"].min() or 0)

    cmap = LinearColormap(
        colors=["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026"],
        vmin=0, vmax=vmax,
    )

    buf_fg = folium.FeatureGroup(name="400 m buffers", show=True)
    for _, r in lockers.iterrows():
        v = int(r["pop_400m_buffer"])
        folium.Circle(
            location=[r["lat"], r["lon"]],
            radius=400,
            color="#333333",
            weight=0.6,
            fill=True,
            fill_color=cmap(v),
            fill_opacity=0.80,
            tooltip=f"{r['locker_name']} — {v:,}",
            popup=folium.Popup(
                f"<b>{r['locker_name']}</b><br>"
                f"400 m buffer: <b>{v:,}</b>",
                max_width=240),
        ).add_to(buf_fg)

    marker_fg = folium.FeatureGroup(name="Locker locations", show=False)
    for _, r in lockers.iterrows():
        folium.Marker(
            location=[r["lat"], r["lon"]],
            icon=folium.Icon(color="lightgray", icon="cube", prefix="fa"),
            tooltip=f"{r['locker_name']}",
            popup=folium.Popup(
                f"<b>{r['locker_name']}</b><br>"
                f"400 m buffer: <b>{int(r['pop_400m_buffer']):,}</b>",
                max_width=240),
        ).add_to(marker_fg)

    buffer_legend = {
        "title": "Residents inside the 400 m buffer",
        "low":   f"Low: {int(vmin):,}",
        "high":  f"High: {int(vmax):,}",
    }

    return _base_map(
        show_network=False,
        show_pop_grid=show_pop_grid,
        prepend_layers=[buf_fg, marker_fg],
        buffer_legend=buffer_legend,
    )


def build_network_map(lockers: pd.DataFrame) -> folium.Map:
    """
    Tab 2 — network accessibility for 5, 10, 15 min in one map.
    Walk-time radio + network-category checkboxes + auxiliary toggles.
    All controls are pure JS — no Streamlit rerun.
    """
    m = _base_map(show_network=False, show_pop_grid=False,
                  show_layer_control=False)

    # --- three network-category layers (visibility managed by JS) ---
    def _net_layer(loader, color, weight, opacity):
        fg = folium.FeatureGroup(name="net")
        folium.GeoJson(
            loader(),
            style_function=lambda _, c=color, w=weight, o=opacity: {
                "color": c, "weight": w, "opacity": o,
            },
            control=False,
        ).add_to(fg)
        fg.add_to(m)
        return fg

    net_main_fg  = _net_layer(load_network_main,  "#00A36C", 1.75, 0.95)
    net_local_fg = _net_layer(load_network_local, "#7FBF9E", 1.10, 0.60)
    net_ped_fg   = _net_layer(load_network_ped,   "#A8D5C2", 0.85, 0.55)

    # --- three walk-time dot layers ---
    fgs = {}
    stats = {}
    for mins in (5, 10, 15):
        col = f"pop_{mins}min_network"
        vals = lockers[col].astype(float)
        vmax = float(vals.max() or 1)
        stats[mins] = (float(vals.min() or 0), vmax)

        cmap = LinearColormap(
            colors=["#d73027", "#fc8d59", "#fee08b", "#a6d96a", "#1a9641"],
            vmin=0, vmax=vmax,
        )

        fg = folium.FeatureGroup(name=f"Network {mins} min", show=True)
        for _, r in lockers.iterrows():
            v = int(r[col])
            folium.CircleMarker(
                location=[r["lat"], r["lon"]],
                radius=_radius_for(v, vmax),
                color="#333", weight=0.6, fill=True,
                fill_color=cmap(v), fill_opacity=0.85,
                tooltip=f"{r['locker_name']} — {v:,}",
                popup=folium.Popup(
                    f"<b>{r['locker_name']}</b><br>"
                    f"{mins}-min network: <b>{v:,}</b>",
                    max_width=240),
            ).add_to(fg)
        fg.add_to(m)
        fgs[mins] = fg

    # --- locker markers (hidden by default; JS hides on init) ---
    marker_fg = folium.FeatureGroup(name="Locker locations")
    for _, r in lockers.iterrows():
        folium.Marker(
            location=[r["lat"], r["lon"]],
            icon=folium.Icon(color="lightgray", icon="cube", prefix="fa"),
            tooltip=f"{r['locker_name']}",
            popup=folium.Popup(
                f"<b>{r['locker_name']}</b><br>"
                f"5-min network: {int(r['pop_5min_network']):,}<br>"
                f"10-min network: {int(r['pop_10min_network']):,}<br>"
                f"15-min network: {int(r['pop_15min_network']):,}",
                max_width=260),
        ).add_to(marker_fg)
    marker_fg.add_to(m)

    # --- population grid (hidden by default; JS hides on init) ---
    png_b64, bounds, _ = load_population_grid()
    pop_grid = folium.raster_layers.ImageOverlay(
        image=f"data:image/png;base64,{png_b64}",
        bounds=bounds,
        opacity=0.75,
        name="Population grid (WorldPop)",
        interactive=False,
        show=False,
    )
    pop_grid.add_to(m)

    m.add_child(_NetworkRadioControl(
        fg5_name=fgs[5].get_name(),
        fg10_name=fgs[10].get_name(),
        fg15_name=fgs[15].get_name(),
        net_main_name=net_main_fg.get_name(),
        net_local_name=net_local_fg.get_name(),
        net_ped_name=net_ped_fg.get_name(),
        markers_name=marker_fg.get_name(),
        popgrid_name=pop_grid.get_name(),
        stats=stats,
    ))
    return m


def build_gap_map(lockers: pd.DataFrame) -> folium.Map:
    m = _base_map(show_network=True, show_pop_grid=False)
    gaps = lockers["buffer_network_gap"].astype(float)
    vmax_abs = float(gaps.abs().max() or 1)

    cmap = LinearColormap(
        colors=["#2166ac", "#67a9cf", "#f7f7f7", "#ef8a62", "#b2182b"],
        vmin=-vmax_abs, vmax=vmax_abs,
        caption="Gap (%) — blue: buffer underestimates · red: buffer overestimates",
    )

    for _, r in lockers.iterrows():
        g = float(r["buffer_network_gap"])
        folium.CircleMarker(
            location=[r["lat"], r["lon"]],
            radius=_radius_for(abs(g), vmax_abs, min_px=4.0, max_px=16.0),
            color="#333", weight=0.6, fill=True,
            fill_color=cmap(g), fill_opacity=0.85,
            tooltip=f"{r['locker_name']} — gap {g:.1f}%",
            popup=folium.Popup(
                f"<b>{r['locker_name']}</b><br>"
                f"Gap: <b>{g:.1f}%</b><br>"
                f"Buffer: {int(r['pop_400m_buffer']):,}<br>"
                f"10-min network: {int(r['pop_10min_network']):,}",
                max_width=260),
        ).add_to(m)
    cmap.add_to(m)
    return m


# ─── Render ──────────────────────────────────────────────────────────────────

st.title("📦 Tallinn Locker Accessibility")
st.caption(
    "Network-based walkability (Dijkstra on the OSM pedestrian graph) "
    "compared against a naive 400 m buffer."
)

row = load_kpis().iloc[0]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Lockers scored",             f"{int(row['lockers_scored'])}")
c2.metric("Avg pop served (10 min)",    f"{int(row['avg_net_10min']):,}")
c3.metric("Avg pop in 400 m buffer",    f"{int(row['avg_buffer']):,}")
c4.metric("Avg buffer-network gap",     f"{row['avg_gap']:.1f}")

st.divider()

lockers = load_lockers()

tab_buffer, tab_network, tab_gap = st.tabs([
    "📍 Lockers & 400 m buffers",
    "🚶 Network accessibility (5 / 10 / 15 min)",
    "📊 Buffer vs network gap",
])

with tab_buffer:
    st.subheader("Naive 400 m straight-line buffer")
    st.caption(
        "Each circle is the actual 400 m footprint around one Omniva locker. "
        "Color reflects the resident population inside the circle — "
        "**no streets, no barriers, no detours**. "
        "Use the layer switcher at the top-right to toggle the buffers, "
        "locker locations, and the WorldPop population grid."
    )
    st_folium(
        build_buffer_map(lockers, show_pop_grid=True),
        width=None, height=620, returned_objects=[],
    )

with tab_network:
    st.subheader("Residents reachable on foot, by walk-time threshold")
    st.caption(
        "Choose **5, 10, or 15 minutes** using the radio control in the "
        "top-right corner of the map. Switching is instant — no page reload. "
        "The legend in the bottom-left updates with the selected threshold."
    )
    st_folium(
        build_network_map(lockers),
        width=None, height=620, returned_objects=[],
    )

with tab_gap:
    st.subheader("How wrong is the naive 400 m buffer?")
    st.caption(
        "Gap = (buffer − 5-min network) / buffer × 100. "
        "**Blue** = buffer under-estimates accessibility. "
        "**Red** = buffer over-estimates. Size scales with |gap|."
    )
    st_folium(build_gap_map(lockers), width=None, height=620,
              returned_objects=[])
