from dotenv import load_dotenv
import os, base64, json
from requests import post, get
import pandas as pd
import unicodedata

import dash
from dash import Dash, dcc, html, Input, Output, State, ctx, no_update
import dash_bootstrap_components as dbc

pd.set_option("display.max_rows", None)
load_dotenv()

# ======================
#  Spotify helpers
# ======================
client_id = os.getenv("CLIENT_ID")
client_secret = os.getenv("CLIENT_SECRET")
DEFAULT_PLAYLIST_ID = "2IgPkhcHbgQ4s4PdCxljAx"  # Top 50 : France


def get_token():
    auth_string = client_id + ":" + client_secret
    auth_bytes = auth_string.encode("utf-8")
    auth_base64 = base64.b64encode(auth_bytes).decode("utf-8")
    url = "https://accounts.spotify.com/api/token"
    headers = {
        "Authorization": "Basic " + auth_base64,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"grant_type": "client_credentials"}
    r = post(url, headers=headers, data=data, verify=False)  # retire verify=False si possible
    r.raise_for_status()
    return r.json()["access_token"]


def get_auth_header(tok):
    return {"Authorization": "Bearer " + tok}


def get_tracks_from_playlist(tok, playlist_id):
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks?limit=100"
    r = get(url, headers=get_auth_header(tok), verify=False)
    r.raise_for_status()
    js = r.json()
    items = js.get("items", [])
    return [it["track"] for it in items if it.get("track")]

def normalize_name(s: str) -> str:
    """Normalise un nom pour comparer : minuscules, sans accents, alphanum only."""
    if not s:
        return ""
    # enlever les accents
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # tout en minuscules et uniquement alphanumérique
    return "".join(c.lower() for c in s if c.isalnum())



def get_best_track_popularity(token: str, track_name: str, artist_name: str, market: str = "FR") -> int | None:
    """
    Cherche sur Spotify toutes les versions du morceau (track_name + artist_name)
    et renvoie la popularité maximale trouvée pour ce couple, ou None si rien.
    On fait deux requêtes :
      1) requête stricte track:"..." artist:"..."
      2) fallback plus souple "<track> <artist>"
    """
    base_track_norm = normalize_name(track_name)
    base_artist_norm = normalize_name(artist_name)

    if not base_track_norm or not base_artist_norm:
        return None

    def search_once(q: str):
        url = "https://api.spotify.com/v1/search"
        params = {
            "q": q,
            "type": "track",
            "limit": 20,
            "market": market,
        }
        r = get(url, headers=get_auth_header(token), params=params, verify=False)
        r.raise_for_status()
        return r.json().get("tracks", {}).get("items", []) or []

    try:
        # 1) requête la plus précise
        items = search_once(f'track:"{track_name}" artist:"{artist_name}"')

        # 2) fallback si rien trouvé
        if not items:
            items = search_once(f"{track_name} {artist_name}")
        if not items:
            return None

        best = None
        for it in items:
            t_name = it.get("name", "")
            artists = it.get("artists", [])
            main_artist_name = artists[0]["name"] if artists else ""

            t_norm = normalize_name(t_name)
            a_norm = normalize_name(main_artist_name)

            if not t_norm or not a_norm:
                continue

            # Match souple :
            # - nom normalisé identique, ou bien l'un commence par l'autre
            # - artiste principal strictement identique après normalisation
            same_track = (
                t_norm == base_track_norm
                or t_norm.startswith(base_track_norm)
                or base_track_norm.startswith(t_norm)
            )
            same_artist = a_norm == base_artist_norm

            if same_track and same_artist:
                pop = it.get("popularity", 0) or 0
                if best is None or pop > best:
                    best = pop

        return best
    except Exception:
        # En cas de souci réseau ou autre, on n'explose pas tout
        return None




def build_df_from_tracks(tracks, token: str):
    rows = []
    for track in tracks:
        artists = track.get("artists", [])
        main_artist = artists[0]["name"] if artists else "Inconnu"
        feat = ", ".join([a["name"] for a in artists[1:]]) if len(artists) > 1 else "None"
        images = track.get("album", {}).get("images", [])
        cover = images[0]["url"] if images else ""

        # popularité fournie par la playlist (souvent bonne, parfois 0 ou bizarre)
        raw_pop = track.get("popularity", 0) or 0

        best_pop = raw_pop
        if main_artist != "Inconnu":
            alt_pop = get_best_track_popularity(token, track["name"], main_artist)
            if alt_pop is not None and alt_pop > best_pop:
                best_pop = alt_pop

        rows.append(
            {
                "id": track["id"],
                "track": track["name"],
                "artist": main_artist,
                "feat": feat,
                "cover": cover,
                "release_date": track.get("album", {}).get("release_date", ""),
                "popularity": best_pop,
            }
        )
    return pd.DataFrame(rows)




def parse_playlist_id(v: str) -> str:
    if not v:
        return ""
    v = v.strip()
    if "open.spotify.com/playlist/" in v:
        rest = v.split("open.spotify.com/playlist/")[1]
        return rest.split("?")[0].split("/")[0]
    if v.startswith("spotify:playlist:"):
        return v.split("spotify:playlist:")[1].split(":")[0]
    return v.split("?")[0].split("/")[0]


# ======================
#  Styles / helpers UI
# ======================
SPOTIFY_GREEN = "#1DB954"
SPOTIFY_DARK = "#191414"
SPOTIFY_LIGHT = "#FFFFFF"
RED_BAD = "#E91429"


def base_img_style():
    return {
        "width": "100%",
        "height": "auto",
        "borderRadius": "12px",
        "boxShadow": "0 4px 20px rgba(0, 0, 0, 0.6)",
        "cursor": "pointer",
        "transition": "box-shadow 0.2s, transform 0.1s, border 0.2s",
        "border": "4px solid transparent",
        "display": "block",
    }


def add_border(style, color):
    s = style.copy()
    s["border"] = f"4px solid {color}"
    return s


def overlay_style(visible=False):
    base = {
        "position": "absolute",
        "top": "0",
        "left": "0",
        "right": "0",
        "bottom": "0",
        "backgroundColor": "rgba(0,0,0,0.55)",
        "borderRadius": "12px",
        "display": "none",
        "alignItems": "center",
        "justifyContent": "center",
        "color": "#FFFFFF",
        "fontSize": "36px",
        "fontWeight": "900",
        "letterSpacing": "1px",
        "pointerEvents": "none",
        "textShadow": "0 2px 6px rgba(0,0,0,0.6)",
    }
    if visible:
        base["display"] = "flex"
    return base


def pick_two_ids_from_df(df: pd.DataFrame):
    if df is None or len(df) < 2:
        return None
    pair = df.sample(2, replace=False).reset_index(drop=True)
    return {"left_id": pair.loc[0, "id"], "right_id": pair.loc[1, "id"]}


# ======================
#  App & Router
# ======================
external_stylesheets = [dbc.themes.BOOTSTRAP]
app = Dash(
    __name__,
    external_stylesheets=external_stylesheets,
    suppress_callback_exceptions=True,
)
server = app.server


def navbar():
    return dbc.Navbar(
        dbc.Container(
            [
                dbc.NavbarBrand("Spotify Games", className="text-white"),
                dbc.Nav(
                    [
                        dbc.Button(
                            "Jeu 1 : Duel",
                            id="nav-duel",
                            color="dark",
                            className="me-2",
                            style={
                                "border": f"1px solid {SPOTIFY_GREEN}",
                                "background": "#000",
                                "color": SPOTIFY_LIGHT,
                            },
                        ),
                        dbc.Button(
                            "Jeu 2 : Classement",
                            id="nav-ranking",
                            color="dark",
                            style={
                                "border": f"1px solid {SPOTIFY_GREEN}",
                                "background": "#000",
                                "color": SPOTIFY_LIGHT,
                            },
                        ),
                    ],
                    className="ms-auto",
                ),
            ]
        ),
        color="dark",
        dark=True,
        className="mb-3",
        style={"borderBottom": f"1px solid {SPOTIFY_GREEN}"},
    )


# --------- PAGE DUEL ----------
def layout_duel():
    return html.Div(
        [
            html.H1(
                "Qui est le plus populaire ?",
                style={
                    "color": SPOTIFY_LIGHT,
                    "textAlign": "center",
                    "marginBottom": "8px",
                },
            ),
            html.P(
                (
                    "La popularité d’un titre (champ *popularity* de l’API Spotify) est une valeur de **0 à 100** : "
                    "elle est calculée à partir du **nombre total d’écoutes** et de leur **récence**. "
                    "Plus la valeur est élevée, plus le morceau est populaire."
                ),
                style={
                    "color": "#B3B3B3",
                    "textAlign": "center",
                    "maxWidth": "1000px",
                    "margin": "0 auto 12px auto",
                },
            ),
            # Scoreboard
            html.Div(
                style={
                    "display": "flex",
                    "justifyContent": "center",
                    "gap": "12px",
                    "marginBottom": "8px",
                    "flexWrap": "wrap",
                },
                children=[
                    html.Div(
                        style={
                            "backgroundColor": "#000",
                            "border": f"1px solid {SPOTIFY_GREEN}",
                            "borderRadius": "999px",
                            "padding": "6px 14px",
                            "color": SPOTIFY_LIGHT,
                            "display": "flex",
                            "gap": "8px",
                            "alignItems": "baseline",
                        },
                        children=[
                            html.Span(
                                "Score",
                                style={"color": "#B3B3B3", "fontSize": "13px"},
                            ),
                            html.Span(
                                id="score-value",
                                children="0",
                                style={
                                    "color": SPOTIFY_GREEN,
                                    "fontWeight": "bold",
                                    "fontSize": "18px",
                                },
                            ),
                        ],
                    ),
                    html.Div(
                        style={
                            "backgroundColor": "#000",
                            "border": f"1px solid {SPOTIFY_GREEN}",
                            "borderRadius": "999px",
                            "padding": "6px 14px",
                            "color": SPOTIFY_LIGHT,
                            "display": "flex",
                            "gap": "8px",
                            "alignItems": "baseline",
                        },
                        children=[
                            html.Span(
                                "Streak",
                                style={"color": "#B3B3B3", "fontSize": "13px"},
                            ),
                            html.Span(
                                id="streak-value",
                                children="0",
                                style={
                                    "color": SPOTIFY_GREEN,
                                    "fontWeight": "bold",
                                    "fontSize": "18px",
                                },
                            ),
                        ],
                    ),
                    html.Div(
                        style={
                            "backgroundColor": "#000",
                            "border": f"1px solid {SPOTIFY_GREEN}",
                            "borderRadius": "999px",
                            "padding": "6px 14px",
                            "color": SPOTIFY_LIGHT,
                            "display": "flex",
                            "gap": "8px",
                            "alignItems": "baseline",
                        },
                        children=[
                            html.Span(
                                "Meilleur streak",
                                style={"color": "#B3B3B3", "fontSize": "13px"},
                            ),
                            html.Span(
                                id="best-value",
                                children="0",
                                style={
                                    "color": SPOTIFY_GREEN,
                                    "fontWeight": "bold",
                                    "fontSize": "18px",
                                },
                            ),
                        ],
                    ),
                ],
            ),
            # Board
            html.Div(
                id="board",
                style={
                    "display": "grid",
                    "gridTemplateColumns": "repeat(2, minmax(0, 1fr))",
                    "gap": "16px",
                    "alignItems": "start",
                    "width": "100%",
                },
                children=[
                    dbc.Card(
                        style={
                            "backgroundColor": "#000000",
                            "borderRadius": "16px",
                            "padding": "12px",
                            "border": "none",
                        },
                        children=[
                            html.Div(
                                style={"position": "relative"},
                                children=[
                                    html.Img(
                                        id="left-cover",
                                        src="",
                                        n_clicks=0,
                                        alt="Cover du morceau gauche",
                                        title="Clique pour choisir ce morceau",
                                        style=base_img_style(),
                                    ),
                                    html.Div(
                                        id="left-overlay",
                                        children="",
                                        style=overlay_style(False),
                                    ),
                                ],
                            ),
                            html.Div(
                                style={"marginTop": "10px"},
                                children=[
                                    html.H3(
                                        id="left-title",
                                        style={
                                            "color": SPOTIFY_LIGHT,
                                            "marginBottom": "4px",
                                            "fontSize": "clamp(16px, 2.2vw, 22px)",
                                        },
                                    ),
                                    html.P(
                                        id="left-artist",
                                        style={
                                            "color": SPOTIFY_GREEN,
                                            "marginBottom": "2px",
                                        },
                                    ),
                                    html.P(
                                        id="left-feat",
                                        style={
                                            "color": "#B3B3B3",
                                            "marginBottom": "2px",
                                        },
                                    ),
                                    html.P(
                                        id="left-release",
                                        style={
                                            "color": "#B3B3B3",
                                            "marginBottom": "8px",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                    dbc.Card(
                        style={
                            "backgroundColor": "#000000",
                            "borderRadius": "16px",
                            "padding": "12px",
                            "border": "none",
                        },
                        children=[
                            html.Div(
                                style={"position": "relative"},
                                children=[
                                    html.Img(
                                        id="right-cover",
                                        src="",
                                        n_clicks=0,
                                        alt="Cover du morceau droite",
                                        title="Clique pour choisir ce morceau",
                                        style=base_img_style(),
                                    ),
                                    html.Div(
                                        id="right-overlay",
                                        children="",
                                        style=overlay_style(False),
                                    ),
                                ],
                            ),
                            html.Div(
                                style={"marginTop": "10px"},
                                children=[
                                    html.H3(
                                        id="right-title",
                                        style={
                                            "color": SPOTIFY_LIGHT,
                                            "marginBottom": "4px",
                                            "fontSize": "clamp(16px, 2.2vw, 22px)",
                                        },
                                    ),
                                    html.P(
                                        id="right-artist",
                                        style={
                                            "color": SPOTIFY_GREEN,
                                            "marginBottom": "2px",
                                        },
                                    ),
                                    html.P(
                                        id="right-feat",
                                        style={
                                            "color": "#B3B3B3",
                                            "marginBottom": "2px",
                                        },
                                    ),
                                    html.P(
                                        id="right-release",
                                        style={
                                            "color": "#B3B3B3",
                                            "marginBottom": "8px",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            html.Div(
                style={"textAlign": "center", "marginTop": "8px"},
                children=[
                    html.H4(
                        id="result-message",
                        style={"color": SPOTIFY_LIGHT, "minHeight": "1.5em"},
                    ),
                    dbc.Button(
                        "Nouvelle manche",
                        id="next-round",
                        color="success",
                        style={
                            "backgroundColor": SPOTIFY_GREEN,
                            "border": "none",
                            "color": "#000",
                            "fontWeight": "bold",
                            "borderRadius": "999px",
                            "padding": "8px 18px",
                        },
                    ),
                ],
            ),
        ]
    )


# --------- PAGE RANKING ----------
def _track_li(tile, idx):
    return html.Li(
        id=f"rank-item-{tile['id']}",
        **{"data-id": tile["id"]},
        draggable="true",
        children=[
            html.Img(
                src=tile["cover"],
                style={
                    "width": "44px",
                    "height": "44px",
                    "objectFit": "cover",
                    "borderRadius": "6px",
                    "marginRight": "10px",
                },
            ),
            html.Div(
                [
                    html.Div(
                        tile["track"],
                        style={"color": SPOTIFY_LIGHT, "fontWeight": 600},
                    ),
                    html.Div(
                        tile["artist"],
                        style={"color": "#9aa0a6", "fontSize": "12px"},
                    ),
                ],
                style={"display": "flex", "flexDirection": "column"},
            ),
            html.Span(f"{idx+1}", style={"marginLeft": "auto", "color": "#70757a"}),
        ],
        style={
            "listStyle": "none",
            "display": "flex",
            "alignItems": "center",
            "gap": "8px",
            "padding": "10px 12px",
            "borderRadius": "10px",
            "border": "1px solid #2a2a2a",
            "background": "#111",
            "cursor": "grab",
            "userSelect": "none",
        },
    )


def layout_ranking():
    return html.Div(
        [
            html.H2(
                "Classe le Top 10 par popularité (1 = le plus populaire)",
                style={
                    "color": SPOTIFY_LIGHT,
                    "textAlign": "center",
                    "marginBottom": "12px",
                },
            ),
            html.Div(
                style={
                    "display": "flex",
                    "gap": "16px",
                    "justifyContent": "center",
                    "flexWrap": "wrap",
                },
                children=[
                    dbc.Button(
                        "Recharger 10 titres",
                        id="rank-reshuffle",
                        style={
                            "background": "#000",
                            "border": f"1px solid {SPOTIFY_GREEN}",
                            "color": SPOTIFY_LIGHT,
                        },
                    ),
                    dbc.Button(
                        "Valider",
                        id="rank-validate",
                        color="success",
                        style={
                            "backgroundColor": SPOTIFY_GREEN,
                            "color": "#000",
                        },
                    ),
                    # --- nouveau bouton "Nouvelle partie" (caché au début) ---
                    dbc.Button(
                        "Nouvelle partie",
                        id="rank-newgame",
                        color="secondary",
                        style={
                            "background": "#000",
                            "border": f"1px solid {SPOTIFY_GREEN}",
                            "color": SPOTIFY_LIGHT,
                            "display": "none",
                        },
                    ),
                ],
            ),
            html.Div(
                id="rank-list-container",
                style={"maxWidth": "800px", "margin": "16px auto"},
            ),
            # on ne s'en sert plus vraiment, mais on le garde pour compat
            html.Div(
                id="rank-results",
                style={"maxWidth": "1000px", "margin": "8px auto"},
            ),
        ]
    )


# --------- Layout principal / Router ----------
app.layout = html.Div(
    style={
        "backgroundColor": SPOTIFY_DARK,
        "minHeight": "100vh",
        "width": "100%",
        "overflowX": "hidden",
    },
    children=[
        dcc.Location(id="url", refresh=False),
        # stores globaux
        dcc.Store(id="df-store", data=None),
        dcc.Store(id="pair-store", data=None),
        dcc.Store(id="selection-store", data={"selected": None}),
        dcc.Store(
            id="score-store",
            data={"score": 0, "streak": 0, "best": 0, "last_round_key": None},
        ),
        dcc.Store(id="ranking-tracks", data=None),
        dcc.Store(id="rank-order", data=[]),
        dcc.Store(id="rank-init", data=None),
        # modale playlist
        dbc.Modal(
            id="playlist-modal",
            is_open=True,
            centered=True,
            backdrop="static",
            keyboard=False,
            children=[
                dbc.ModalHeader(dbc.ModalTitle("Choisis une playlist Spotify")),
                dbc.ModalBody(
                    [
                        html.P(
                            "Entre un ID de playlist, une URL Spotify, ou utilise la playlist par défaut.",
                            style={"color": "#B3B3B3", "marginBottom": "8px"},
                        ),
                        dbc.Input(
                            id="playlist-input",
                            placeholder="Ex: 37i9dQZF1DXcBWIGoYBM5M ou https://open.spotify.com/playlist/...",
                            type="text",
                        ),
                        html.Small(
                            id="modal-error",
                            style={
                                "color": "#ff8a8a",
                                "display": "block",
                                "marginTop": "8px",
                            },
                        ),
                    ]
                ),
                dbc.ModalFooter(
                    [
                        dbc.Button(
                            "Utiliser Top 50 : France", id="use-default", color="secondary"
                        ),
                        dbc.Button(
                            "Charger",
                            id="load-playlist",
                            color="success",
                            style={
                                "backgroundColor": SPOTIFY_GREEN,
                                "color": "#000",
                            },
                        ),
                    ]
                ),
            ],
        ),
        navbar(),
        dbc.Container(
            id="page-content",
            style={
                "minHeight": "100vh",
                "padding": "24px",
                "width": "100%",
                "maxWidth": "min(1100px, 95vw)",
                "margin": "0 auto",
            },
        ),
    ],
)

# validation_layout pour que Dash connaisse tous les ids de toutes les pages
app.validation_layout = html.Div(
    [
        app.layout,
        layout_duel(),
        layout_ranking(),
    ]
)

# ======================
#  Router
# ======================
@app.callback(Output("page-content", "children"), Input("url", "pathname"))
def route(path):
    if path == "/ranking":
        return layout_ranking()
    return layout_duel()


@app.callback(
    Output("url", "pathname", allow_duplicate=True),
    Input("nav-duel", "n_clicks"),
    Input("nav-ranking", "n_clicks"),
    prevent_initial_call=True,
)
def nav(n_duel, n_rank):
    trig = ctx.triggered_id
    if trig == "nav-ranking":
        return "/ranking"
    if trig == "nav-duel":
        return "/"
    return no_update


# ======================
#  Playlist loading
# ======================
@app.callback(
    Output("df-store", "data"),
    Output("playlist-modal", "is_open"),
    Output("modal-error", "children"),
    Input("load-playlist", "n_clicks"),
    Input("use-default", "n_clicks"),
    State("playlist-input", "value"),
    prevent_initial_call=True,
)
def load_playlist(n_load, n_default, raw_value):
    trig = ctx.triggered_id
    if trig == "use-default":
        playlist_id = DEFAULT_PLAYLIST_ID
    elif trig == "load-playlist":
        playlist_id = parse_playlist_id(raw_value or "")
        if not playlist_id:
            return no_update, True, "Veuillez entrer un ID ou une URL de playlist valide."
    else:
        return no_update, True, no_update

    try:
        token = get_token()
        tracks = get_tracks_from_playlist(token, playlist_id)
        df = build_df_from_tracks(tracks, token)
    except Exception:
        return (
            no_update,
            True,
            "Impossible de charger la playlist (ID invalide ou inaccessible).",
        )

    if df is None or df.empty or len(df) < 2:
        return (
            no_update,
            True,
            "Playlist vide/inaccessible ou contenant moins de 2 titres.",
        )

    return df.to_dict("records"), False, ""


# ======================
#  Logique DUEL
# ======================
@app.callback(
    Output("pair-store", "data"),
    Output("selection-store", "data"),
    Output("score-store", "data"),
    Input("df-store", "data"),
)
def seed_round_on_df(records):
    if not records:
        return None, {"selected": None}, {
            "score": 0,
            "streak": 0,
            "best": 0,
            "last_round_key": None,
        }
    df = pd.DataFrame(records)
    pair = pick_two_ids_from_df(df)
    return pair, {"selected": None}, {
        "score": 0,
        "streak": 0,
        "best": 0,
        "last_round_key": None,
    }


@app.callback(
    Output("pair-store", "data", allow_duplicate=True),
    Input("next-round", "n_clicks"),
    State("df-store", "data"),
    prevent_initial_call=True,
)
def new_round_click(_n, records):
    if not records:
        return no_update
    df = pd.DataFrame(records)
    return pick_two_ids_from_df(df)


@app.callback(
    Output("selection-store", "data", allow_duplicate=True),
    Input("left-cover", "n_clicks"),
    Input("right-cover", "n_clicks"),
    Input("next-round", "n_clicks"),
    State("selection-store", "data"),
    prevent_initial_call=True,
)
def choose_or_reset(n_left, n_right, n_next, selection_state):
    trig = ctx.triggered_id
    if trig == "next-round":
        return {"selected": None}
    if selection_state and selection_state.get("selected") in ("left", "right"):
        return selection_state
    if trig == "left-cover":
        return {"selected": "left"}
    if trig == "right-cover":
        return {"selected": "right"}
    return selection_state or {"selected": None}


@app.callback(
    [
        Output("left-cover", "src"),
        Output("right-cover", "src"),
        Output("left-cover", "style"),
        Output("right-cover", "style"),
        Output("left-title", "children"),
        Output("left-artist", "children"),
        Output("left-feat", "children"),
        Output("left-release", "children"),
        Output("right-title", "children"),
        Output("right-artist", "children"),
        Output("right-feat", "children"),
        Output("right-release", "children"),
        Output("result-message", "children"),
        Output("left-overlay", "children"),
        Output("right-overlay", "children"),
        Output("left-overlay", "style"),
        Output("right-overlay", "style"),
    ],
    Input("pair-store", "data"),
    Input("selection-store", "data"),
    State("df-store", "data"),
)
def render_duel(pair_data, selection_state, records):
    empty = (
        "",
        "",
        base_img_style(),
        base_img_style(),
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        overlay_style(False),
        overlay_style(False),
    )
    if not records or not pair_data:
        return empty
    df = pd.DataFrame(records)
    if pair_data["left_id"] not in set(df["id"]) or pair_data["right_id"] not in set(
        df["id"]
    ):
        return empty

    left = df[df["id"] == pair_data["left_id"]].iloc[0]
    right = df[df["id"] == pair_data["right_id"]].iloc[0]

    left_src, right_src = left["cover"], right["cover"]
    left_title, right_title = left["track"], right["track"]
    left_artist = f"Artiste principal : {left['artist']}"
    right_artist = f"Artiste principal : {right['artist']}"
    left_feat = f"Feat : {left['feat']}" if left["feat"] != "None" else "Pas de featuring"
    right_feat = (
        f"Feat : {right['feat']}" if right["feat"] != "None" else "Pas de featuring"
    )
    left_release = f"Date de sortie : {left['release_date']}"
    right_release = f"Date de sortie : {right['release_date']}"

    left_style, right_style = base_img_style(), base_img_style()
    message = ""
    ltxt = rtxt = ""
    lostyle = overlay_style(False)
    rostyle = overlay_style(False)

    sel = (selection_state or {}).get("selected")
    if sel in ("left", "right"):
        lp, rp = int(left["popularity"]), int(right["popularity"])
        ltxt, rtxt = f"Popularité : {lp}", f"Popularité : {rp}"
        lostyle, rostyle = overlay_style(True), overlay_style(True)

        if lp == rp:
            left_style = add_border(left_style, SPOTIFY_GREEN)
            right_style = add_border(right_style, SPOTIFY_GREEN)
            message = f"Égalité parfaite ! Les deux morceaux ont {lp}/100."
        else:
            correct_side = "left" if lp > rp else "right"
            if sel == correct_side:
                if sel == "left":
                    left_style = add_border(left_style, SPOTIFY_GREEN)
                else:
                    right_style = add_border(right_style, SPOTIFY_GREEN)
                message = "✅ Bonne réponse !"
            else:
                if sel == "left":
                    left_style = add_border(left_style, RED_BAD)
                    right_style = add_border(right_style, SPOTIFY_GREEN)
                else:
                    right_style = add_border(right_style, RED_BAD)
                    left_style = add_border(left_style, SPOTIFY_GREEN)
                message = (
                    f"❌ Mauvaise réponse. "
                    f"« {right_title if correct_side=='right' else left_title} » est plus populaire."
                )

    return (
        left_src,
        right_src,
        left_style,
        right_style,
        left_title,
        left_artist,
        left_feat,
        left_release,
        right_title,
        right_artist,
        right_feat,
        right_release,
        message,
        ltxt,
        rtxt,
        lostyle,
        rostyle,
    )


@app.callback(
    Output("score-store", "data", allow_duplicate=True),
    Input("selection-store", "data"),
    State("pair-store", "data"),
    State("score-store", "data"),
    State("df-store", "data"),
    prevent_initial_call=True,
)
def update_score(selection_state, pair_data, score_state, records):
    if not records or not pair_data:
        return score_state
    sel = (selection_state or {}).get("selected")
    if sel not in ("left", "right"):
        return score_state

    rk = f"{pair_data['left_id']}|{pair_data['right_id']}"
    if score_state.get("last_round_key") == rk:
        return score_state

    df = pd.DataFrame(records)
    try:
        left = df[df["id"] == pair_data["left_id"]].iloc[0]
        right = df[df["id"] == pair_data["right_id"]].iloc[0]
    except Exception:
        return score_state

    lp, rp = int(left["popularity"]), int(right["popularity"])
    correct = (
        True
        if lp == rp
        else (sel == "left" and lp > rp) or (sel == "right" and rp > lp)
    )

    score = score_state.get("score", 0)
    streak = score_state.get("streak", 0)
    best = score_state.get("best", 0)

    if correct:
        score += 100 - abs(lp - rp)
        streak += 1
        best = max(best, streak)
    else:
        streak = 0

    return {"score": score, "streak": streak, "best": best, "last_round_key": rk}


@app.callback(
    Output("score-value", "children"),
    Output("streak-value", "children"),
    Output("best-value", "children"),
    Input("score-store", "data"),
)
def render_scoreboard(store):
    return (
        str(store.get("score", 0)),
        str(store.get("streak", 0)),
        str(store.get("best", 0)),
    )


# ======================
#  Logique RANKING
# ======================
@app.callback(
    Output("ranking-tracks", "data"),
    Input("url", "pathname"),
    Input("rank-reshuffle", "n_clicks"),
    Input("rank-newgame", "n_clicks"),
    State("df-store", "data"),
    prevent_initial_call=True,
)
def make_ranking_set(path, _n1, _n2, records):
    if path != "/ranking" or not records:
        return no_update

    df = pd.DataFrame(records)

    # Mélanger puis enlever les doublons de popularité
    # => chaque ligne gardée a une popularité unique
    df_unique = df.sample(frac=1).drop_duplicates(subset="popularity")

    # S'il n'y a pas assez de popularités distinctes, on utilise ce qu'on a
    if len(df_unique) < 2:
        return no_update

    n = min(10, len(df_unique))
    sample = df_unique.head(n).to_dict("records")
    return sample


@app.callback(
    Output("rank-list-container", "children"),
    Input("ranking-tracks", "data"),
)
def render_rank_list(tracks):
    if not tracks:
        return html.Div(
            "Charge une playlist puis reviens ici.",
            style={"color": "#aaa", "textAlign": "center"},
        )
    items = [_track_li(t, i) for i, t in enumerate(tracks)]
    ul = html.Ul(
        items,
        id="rank-list",
        style={
            "padding": 0,
            "margin": "8px auto",
            "display": "flex",
            "flexDirection": "column",
            "gap": "10px",
        },
    )
    hint = html.Div(
        "Astuce : fais glisser les lignes pour les réordonner puis clique sur Valider.",
        style={"textAlign": "center", "color": "#9aa0a6", "marginBottom": "8px"},
    )
    return [hint, ul]


# Quand on génère une nouvelle liste de titres, on remet les boutons
# en mode "jeu" (Valider visible, Nouvelle partie caché).
@app.callback(
    Output("rank-newgame", "style"),
    Output("rank-validate", "style"),
    Input("ranking-tracks", "data"),
    State("rank-newgame", "style"),
    State("rank-validate", "style"),
)
def reset_ranking_buttons(tracks, new_style, val_style):
    new_style = dict(new_style or {})
    val_style = dict(val_style or {})
    if tracks:
        new_style["display"] = "none"
        val_style["display"] = "inline-block"
    return new_style, val_style


# --- (A) INIT drag&drop : on attend que le DOM ait créé #rank-list ---
app.clientside_callback(
    """
    function(pathname, tracks){
        try {
            if (pathname !== '/ranking') {
                return window.dash_clientside.no_update;
            }

            function attachDnD(){
                var list = document.getElementById('rank-list');
                if (!list) {
                    // réessaye tant que la liste n’existe pas encore
                    window.setTimeout(attachDnD, 100);
                    return;
                }
                if (list.__dnd_inited) {
                    return;
                }
                list.__dnd_inited = true;

                var dragEl = null;

                list.addEventListener('dragstart', function(e){
                    var li = e.target.closest('li');
                    if(!li) return;
                    dragEl = li;
                    e.dataTransfer.effectAllowed = 'move';
                    e.dataTransfer.setData('text/plain', li.getAttribute('data-id'));
                    li.style.opacity = '0.4';
                });

                list.addEventListener('dragend', function(e){
                    if(dragEl){ dragEl.style.opacity = ''; }
                    dragEl = null;
                });

                list.addEventListener('dragover', function(e){
                    e.preventDefault();
                    var over = e.target.closest('li');
                    if(!over || over === dragEl) return;
                    var rect = over.getBoundingClientRect();
                    var next = (e.clientY - rect.top) / (rect.bottom - rect.top) > 0.5;
                    list.insertBefore(dragEl, next ? over.nextSibling : over);
                });

                list.addEventListener('drop', function(e){ e.preventDefault(); });
            }

            // lance l’attachement après un petit délai pour laisser React finir le rendu
            window.setTimeout(attachDnD, 0);
            return Date.now();
        } catch (err) {
            console.warn('Init DnD error:', err);
            return window.dash_clientside.no_update;
        }
    }
    """,
    Output("rank-init", "data"),
    Input("url", "pathname"),
    Input("ranking-tracks", "data"),
    prevent_initial_call=True,
)

# --- (B) Lecture de l’ordre au clic "Valider" ---
app.clientside_callback(
    """
    function(n_clicks){
        try {
            if (!n_clicks) return window.dash_clientside.no_update;
            var list = document.getElementById('rank-list');
            if(!list) return window.dash_clientside.no_update;
            var order = [];
            for (var i=0;i<list.children.length;i++){
                var id = list.children[i].getAttribute('data-id');
                if(id) order.push(id);
            }
            return order;
        } catch (err) {
            console.warn('Read order error:', err);
            return window.dash_clientside.no_update;
        }
    }
    """,
    Output("rank-order", "data"),
    Input("rank-validate", "n_clicks"),
    prevent_initial_call=True,
)


# === Validation du classement ===
# On remplace la liste modifiable par les résultats
# et on affiche le bouton "Nouvelle partie".
@app.callback(
    Output("rank-list-container", "children", allow_duplicate=True),
    Output("rank-newgame", "style", allow_duplicate=True),
    Output("rank-validate", "style", allow_duplicate=True),
    Input("rank-order", "data"),
    State("ranking-tracks", "data"),
    State("rank-newgame", "style"),
    State("rank-validate", "style"),
    prevent_initial_call=True,
)
def validate_ranking(order_ids, tracks, new_style, val_style):
    if not tracks or not order_ids:
        return (
            html.Div(
                "Aucune liste à valider.",
                style={"color": "#aaa", "textAlign": "center"},
            ),
            new_style,
            val_style,
        )

    df = pd.DataFrame(tracks)
    truth = df.sort_values("popularity", ascending=False).reset_index(drop=True)
    truth_ids = list(truth["id"])
    by_id = {t["id"]: t for t in tracks}
    user = [by_id[i] for i in order_ids if i in by_id]

    m = min(len(truth_ids), len(user))
    user = user[:m]
    truth = truth.iloc[:m]

    ok = 0
    left_col = html.Div(
        [
            html.H4("Classement correct", style={"color": SPOTIFY_LIGHT}),
            html.Ul(
                [
                    html.Li(
                        [
                            html.Img(
                                src=row["cover"],
                                style={
                                    "width": "34px",
                                    "height": "34px",
                                    "objectFit": "cover",
                                    "borderRadius": "6px",
                                    "marginRight": "10px",
                                },
                            ),
                            html.Span(
                                row["track"],
                                style={"color": SPOTIFY_LIGHT, "fontWeight": 600},
                            ),
                            html.Span(
                                "  —  " + row["artist"],
                                style={
                                    "color": "#9aa0a6",
                                    "fontSize": "12px",
                                    "marginLeft": "6px",
                                },
                            ),
                            html.Span(
                                f"  ({int(row['popularity'])})",
                                style={"color": "#70757a", "marginLeft": "auto"},
                            ),
                        ],
                        style={
                            "display": "flex",
                            "alignItems": "center",
                            "gap": "6px",
                            "listStyle": "none",
                            "border": "1px solid #2a2a2a",
                            "borderRadius": "10px",
                            "padding": "8px 10px",
                            "background": "#111",
                            "marginBottom": "8px",
                        },
                    )
                    for _, row in truth.iterrows()
                ],
                style={"padding": 0},
            ),
        ],
        style={"flex": "1"},
    )

    right_items = []
    for idx, t in enumerate(user):
        correct_id = truth_ids[idx]
        good = t["id"] == correct_id
        if good:
            ok += 1
        right_items.append(
            html.Li(
                [
                    html.Img(
                        src=t["cover"],
                        style={
                            "width": "34px",
                            "height": "34px",
                            "objectFit": "cover",
                            "borderRadius": "6px",
                            "marginRight": "10px",
                        },
                    ),
                    html.Span(
                        t["track"],
                        style={"color": SPOTIFY_LIGHT, "fontWeight": 600},
                    ),
                    html.Span(
                        "  —  " + t["artist"],
                        style={
                            "color": "#9aa0a6",
                            "fontSize": "12px",
                            "marginLeft": "6px",
                        },
                    ),
                    html.Span(
                        f"({int(t['popularity'])})",
                        style={"color": "#70757a", "marginLeft": "auto"},
                    ),
                ],
                style={
                    "display": "flex",
                    "alignItems": "center",
                    "gap": "6px",
                    "listStyle": "none",
                    "border": "2px solid " + ("#2ecc71" if good else "#e74c3c"),
                    "borderRadius": "10px",
                    "padding": "8px 10px",
                    "background": "#0f1a12" if good else "#1b0f10",
                    "marginBottom": "8px",
                },
            )
        )

    right_col = html.Div(
        [
            html.H4(
                f"Ton classement  —  {ok}/{m} bien placés",
                style={"color": SPOTIFY_LIGHT},
            ),
            html.Ul(right_items, style={"padding": 0}),
        ],
        style={"flex": "1"},
    )

    results_div = html.Div(
        [left_col, right_col],
        style={"display": "flex", "gap": "18px", "flexWrap": "wrap"},
    )

    # on affiche "Nouvelle partie" et on cache "Valider"
    new_style = dict(new_style or {})
    val_style = dict(val_style or {})
    new_style["display"] = "inline-block"
    val_style["display"] = "none"

    return results_div, new_style, val_style


if __name__ == "__main__":
    app.run(debug=True)
