from dotenv import load_dotenv
import os
import base64
from requests import post, get
import json
import pandas as pd

import dash
from dash import Dash, dcc, html, Input, Output, State, ctx, no_update
import dash_bootstrap_components as dbc

pd.set_option('display.max_rows', None)
load_dotenv()

# ==========
#  Spotify
# ==========
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

    result = post(url, headers=headers, data=data, verify=False)  # retire verify=False si possible
    result.raise_for_status()
    return result.json()["access_token"]

def get_auth_header(token):
    return {"Authorization": "Bearer " + token}

def get_tracks_from_playlist(token, playlist_id):
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks?limit=100"
    headers = get_auth_header(token)
    result = get(url, headers=headers, verify=False)
    result.raise_for_status()
    json_result = json.loads(result.content)
    items = json_result.get("items", [])
    return [it["track"] for it in items if it.get("track")]

def build_df_from_tracks(tracks):
    rows = []
    for track in tracks:
        artists = track.get("artists", [])
        main_artist = artists[0]["name"] if artists else "Inconnu"
        feat = ", ".join([a["name"] for a in artists[1:]]) if len(artists) > 1 else "None"
        images = track.get("album", {}).get("images", [])
        cover = images[0]["url"] if images else ""
        rows.append({
            "id": track["id"],
            "track": track["name"],
            "artist": main_artist,
            "feat": feat,
            "cover": cover,
            "release_date": track.get("album", {}).get("release_date", ""),
            "popularity": track.get("popularity", 0),
        })
    return pd.DataFrame(rows)

def parse_playlist_id(value: str) -> str:
    """Accepte un ID brut, une URL open.spotify.com/playlist/<id> ou spotify:playlist:<id>."""
    if not value:
        return ""
    v = value.strip()
    if "open.spotify.com/playlist/" in v:
        rest = v.split("open.spotify.com/playlist/")[1]
        return rest.split("?")[0].split("/")[0]
    if v.startswith("spotify:playlist:"):
        return v.split("spotify:playlist:")[1].split(":")[0]
    return v.split("?")[0].split("/")[0]

# ==========
#   Dash
# ==========
external_stylesheets = [dbc.themes.BOOTSTRAP]
app = Dash(__name__, external_stylesheets=external_stylesheets)

SPOTIFY_GREEN = "#1DB954"
SPOTIFY_DARK   = "#191414"
SPOTIFY_LIGHT  = "#FFFFFF"
RED_BAD        = "#E91429"

def base_img_style():
    return {
        "width": "100%",
        "height": "auto",
        "borderRadius": "12px",
        "boxShadow": "0 4px 20px rgba(0,0,0,0.6)",
        "cursor": "pointer",
        "transition": "box-shadow 0.2s, transform 0.1s, border 0.2s",
        "border": "4px solid transparent",
        "display": "block",
    }

def add_border(style, color=None):
    s = style.copy()
    s["border"] = f"4px solid {color}" if color else "4px solid transparent"
    return s

def overlay_style(visible=False):
    base = {
        "position": "absolute",
        "top": "0", "left": "0", "right": "0", "bottom": "0",
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

# --- OUTER WRAPPER ---
app.layout = html.Div(
    style={"backgroundColor": SPOTIFY_DARK, "minHeight": "100vh", "width": "100%", "overflowX": "hidden"},
    children=[
        # Stores
        dcc.Store(id="df-store", data=None),
        dcc.Store(id="pair-store", data=None),
        dcc.Store(id="selection-store", data={"selected": None}),
        dcc.Store(id="score-store", data={"score": 0, "streak": 0, "best": 0, "last_round_key": None}),

        # Modale de choix de playlist
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
                            style={"color": "#ff8a8a", "display": "block", "marginTop": "8px"},
                        ),
                    ]
                ),
                dbc.ModalFooter(
                    [
                        dbc.Button("Utiliser Top 50 : France", id="use-default", color="secondary"),
                        dbc.Button("Charger", id="load-playlist", color="success",
                                   style={"backgroundColor": SPOTIFY_GREEN, "color": "#000"}),
                    ]
                ),
            ],
        ),

        # Container principal
        dbc.Container(
            fluid=True,
            style={"minHeight": "100vh", "padding": "24px", "width": "100%",
                   "maxWidth": "min(1100px, 95vw)", "margin": "0 auto"},
            children=[
                html.H1("Qui est le plus populaire ?",
                        style={"color": SPOTIFY_LIGHT, "textAlign": "center", "marginBottom": "8px"}),
                html.P(
                    ("La popularité d’un titre (champ *popularity* de l’API Spotify) est une valeur de **0 à 100** : "
                     "elle est calculée à partir du **nombre total d’écoutes** et de leur **récence**. "
                     "Plus la valeur est élevée, plus le morceau est populaire."),
                    style={"color": "#B3B3B3", "textAlign": "center", "maxWidth": "1000px",
                           "margin": "0 auto 12px auto"},
                ),

                # Scoreboard
                html.Div(
                    style={"display": "flex", "justifyContent": "center", "gap": "12px",
                           "marginBottom": "8px", "flexWrap": "wrap"},
                    children=[
                        html.Div(
                            style={"backgroundColor": "#000", "border": f"1px solid {SPOTIFY_GREEN}",
                                   "borderRadius": "999px", "padding": "6px 14px", "color": SPOTIFY_LIGHT,
                                   "display": "flex", "gap": "8px", "alignItems": "baseline"},
                            children=[html.Span("Score", style={"color": "#B3B3B3", "fontSize": "13px"}),
                                      html.Span(id="score-value", children="0",
                                                style={"color": SPOTIFY_GREEN, "fontWeight": "bold", "fontSize": "18px"})]
                        ),
                        html.Div(
                            style={"backgroundColor": "#000", "border": f"1px solid {SPOTIFY_GREEN}",
                                   "borderRadius": "999px", "padding": "6px 14px", "color": SPOTIFY_LIGHT,
                                   "display": "flex", "gap": "8px", "alignItems": "baseline"},
                            children=[html.Span("Streak", style={"color": "#B3B3B3", "fontSize": "13px"}),
                                      html.Span(id="streak-value", children="0",
                                                style={"color": SPOTIFY_GREEN, "fontWeight": "bold", "fontSize": "18px"})]
                        ),
                        html.Div(
                            style={"backgroundColor": "#000", "border": f"1px solid {SPOTIFY_GREEN}",
                                   "borderRadius": "999px", "padding": "6px 14px", "color": SPOTIFY_LIGHT,
                                   "display": "flex", "gap": "8px", "alignItems": "baseline"},
                            children=[html.Span("Meilleur streak", style={"color": "#B3B3B3", "fontSize": "13px"}),
                                      html.Span(id="best-value", children="0",
                                                style={"color": SPOTIFY_GREEN, "fontWeight": "bold", "fontSize": "18px"})]
                        ),
                    ],
                ),

                # Plateau de jeu
                html.Div(
                    id="board",
                    style={"display": "grid", "gridTemplateColumns": "repeat(2, minmax(0, 1fr))",
                           "gap": "16px", "alignItems": "start", "width": "100%"},
                    children=[
                        # Gauche
                        dbc.Card(
                            style={"backgroundColor": "#000000", "borderRadius": "16px",
                                   "padding": "12px", "border": "none"},
                            children=[
                                html.Div(
                                    style={"position": "relative"},
                                    children=[
                                        html.Img(id="left-cover", src="", n_clicks=0,
                                                 alt="Cover du morceau gauche",
                                                 title="Clique pour choisir ce morceau",
                                                 style=base_img_style()),
                                        html.Div(id="left-overlay", children="", style=overlay_style(False)),
                                    ],
                                ),
                                html.Div(
                                    style={"marginTop": "10px"},
                                    children=[
                                        html.H3(id="left-title",
                                                style={"color": SPOTIFY_LIGHT, "marginBottom": "4px",
                                                       "fontSize": "clamp(16px, 2.2vw, 22px)"}),
                                        html.P(id="left-artist", style={"color": SPOTIFY_GREEN, "marginBottom": "2px"}),
                                        html.P(id="left-feat", style={"color": "#B3B3B3", "marginBottom": "2px"}),
                                        html.P(id="left-release", style={"color": "#B3B3B3", "marginBottom": "8px"}),
                                    ],
                                ),
                            ],
                        ),
                        # Droite
                        dbc.Card(
                            style={"backgroundColor": "#000000", "borderRadius": "16px",
                                   "padding": "12px", "border": "none"},
                            children=[
                                html.Div(
                                    style={"position": "relative"},
                                    children=[
                                        html.Img(id="right-cover", src="", n_clicks=0,
                                                 alt="Cover du morceau droite",
                                                 title="Clique pour choisir ce morceau",
                                                 style=base_img_style()),
                                        html.Div(id="right-overlay", children="", style=overlay_style(False)),
                                    ],
                                ),
                                html.Div(
                                    style={"marginTop": "10px"},
                                    children=[
                                        html.H3(id="right-title",
                                                style={"color": SPOTIFY_LIGHT, "marginBottom": "4px",
                                                       "fontSize": "clamp(16px, 2.2vw, 22px)"}),
                                        html.P(id="right-artist", style={"color": SPOTIFY_GREEN, "marginBottom": "2px"}),
                                        html.P(id="right-feat", style={"color": "#B3B3B3", "marginBottom": "2px"}),
                                        html.P(id="right-release", style={"color": "#B3B3B3", "marginBottom": "8px"}),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),

                # Message + bouton nouvelle manche
                html.Div(
                    style={"textAlign": "center", "marginTop": "8px"},
                    children=[
                        html.H4(id="result-message", style={"color": SPOTIFY_LIGHT, "minHeight": "1.5em"}),
                        dbc.Button(
                            "Nouvelle manche",
                            id="next-round",
                            color="success",
                            style={"backgroundColor": SPOTIFY_GREEN, "border": "none", "color": "#000",
                                   "fontWeight": "bold", "borderRadius": "999px", "padding": "8px 18px"},
                        ),
                    ],
                ),
            ],
        ),
    ],
)

# =============================
#  Callbacks : chargement playlist
# =============================

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
        df = build_df_from_tracks(tracks)
    except Exception:
        return no_update, True, "Impossible de charger la playlist (ID invalide ou inaccessible)."

    if df is None or df.empty or len(df) < 2:
        return no_update, True, "Playlist vide/inaccessible ou contenant moins de 2 titres."

    return df.to_dict("records"), False, ""  # ferme la modale

# Quand la playlist change : on réinitialise la manche + le score
@app.callback(
    Output("pair-store", "data"),
    Output("selection-store", "data"),
    Output("score-store", "data"),
    Input("df-store", "data"),
)
def seed_round_on_df(records):
    if not records:
        return None, {"selected": None}, {"score": 0, "streak": 0, "best": 0, "last_round_key": None}
    df = pd.DataFrame(records)
    pair = pick_two_ids_from_df(df)
    return pair, {"selected": None}, {"score": 0, "streak": 0, "best": 0, "last_round_key": None}

# Nouvelle manche -> pair-store (allow duplicate)
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
    pair = pick_two_ids_from_df(df)
    return pair

# =============================
#     Callbacks du mini-jeu
# =============================

# Choix / reset sélection (allow duplicate car seed_round_on_df écrit aussi dans selection-store)
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

# UI principale
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
def render_ui(pair_data, selection_state, records):
    empty = ("", "", base_img_style(), base_img_style(),
             "", "", "", "", "", "", "", "",
             "", "", "", overlay_style(False), overlay_style(False))
    if not records or not pair_data:
        return empty

    df = pd.DataFrame(records)
    if pair_data["left_id"] not in set(df["id"]) or pair_data["right_id"] not in set(df["id"]):
        return empty

    left = df[df["id"] == pair_data["left_id"]].iloc[0]
    right = df[df["id"] == pair_data["right_id"]].iloc[0]

    left_src = left["cover"]
    right_src = right["cover"]
    left_title = left["track"]
    right_title = right["track"]
    left_artist = f"Artiste principal : {left['artist']}"
    right_artist = f"Artiste principal : {right['artist']}"
    left_feat = f"Feat : {left['feat']}" if left["feat"] != "None" else "Pas de featuring"
    right_feat = f"Feat : {right['feat']}" if right["feat"] != "None" else "Pas de featuring"
    left_release = f"Date de sortie : {left['release_date']}"
    right_release = f"Date de sortie : {right['release_date']}"

    left_style = base_img_style()
    right_style = base_img_style()
    message = ""

    left_overlay_text = ""
    right_overlay_text = ""
    left_overlay_style = overlay_style(False)
    right_overlay_style = overlay_style(False)

    selected = (selection_state or {}).get("selected")
    if selected in ("left", "right"):
        lp = int(left["popularity"])
        rp = int(right["popularity"])

        left_overlay_text = f"Popularité : {lp}"
        right_overlay_text = f"Popularité : {rp}"
        left_overlay_style = overlay_style(True)
        right_overlay_style = overlay_style(True)

        if lp == rp:
            left_style = add_border(left_style, SPOTIFY_GREEN)
            right_style = add_border(right_style, SPOTIFY_GREEN)
            message = f"Égalité parfaite ! Les deux morceaux ont {lp}/100."
        else:
            correct_side = "left" if lp > rp else "right"
            if selected == correct_side:
                if selected == "left":
                    left_style = add_border(left_style, SPOTIFY_GREEN)
                else:
                    right_style = add_border(right_style, SPOTIFY_GREEN)
                message = "✅ Bonne réponse !"
            else:
                if selected == "left":
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
        left_src, right_src,
        left_style, right_style,
        left_title, left_artist, left_feat, left_release,
        right_title, right_artist, right_feat, right_release,
        message,
        left_overlay_text, right_overlay_text,
        left_overlay_style, right_overlay_style,
    )

# Score / Streak / Best (allow duplicate car seed_round_on_df écrit aussi score-store)
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

    selected = (selection_state or {}).get("selected")
    if selected not in ("left", "right"):
        return score_state

    round_key = f"{pair_data['left_id']}|{pair_data['right_id']}"
    if score_state.get("last_round_key") == round_key:
        return score_state

    df = pd.DataFrame(records)
    try:
        left = df[df["id"] == pair_data["left_id"]].iloc[0]
        right = df[df["id"] == pair_data["right_id"]].iloc[0]
    except Exception:
        return score_state

    lp, rp = int(left["popularity"]), int(right["popularity"])
    correct = True if lp == rp else ((selected == "left" and lp > rp) or (selected == "right" and rp > lp))

    score = score_state.get("score", 0)
    streak = score_state.get("streak", 0)
    best = score_state.get("best", 0)

    if correct:
        gained = 100 - abs(lp - rp)
        score += gained
        streak += 1
        best = max(best, streak)
    else:
        streak = 0

    return {"score": score, "streak": streak, "best": best, "last_round_key": round_key}

# Affichage du scoreboard
@app.callback(
    Output("score-value", "children"),
    Output("streak-value", "children"),
    Output("best-value", "children"),
    Input("score-store", "data"),
)
def render_scoreboard(store):
    return str(store.get("score", 0)), str(store.get("streak", 0)), str(store.get("best", 0))

if __name__ == "__main__":
    app.run(debug=True)
