from dotenv import load_dotenv  
import os 
import base64 
from requests import post, get 
import json 
import pandas as pd
from urllib.parse import quote
from IPython.display import display

import dash
from dash import Dash, dcc, html, Input, Output
import dash_bootstrap_components as dbc

pd.set_option('display.max_rows', None)

load_dotenv()

# ⚠️ Mets CLIENT_ID et CLIENT_SECRET dans .env
client_id = os.getenv("CLIENT_ID")
client_secret = os.getenv("CLIENT_SECRET")


# =======================
#      SPOTIFY API
# =======================

def get_token():
    auth_string = client_id + ":" + client_secret
    auth_bytes = auth_string.encode("utf-8")
    auth_base64 = base64.b64encode(auth_bytes).decode("utf-8")
 
    url = "https://accounts.spotify.com/api/token" 
    headers = { 
        "Authorization": "Basic " + auth_base64,
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {"grant_type": "client_credentials"}

    # verify=False uniquement si tu as vraiment besoin d’ignorer le SSL localement
    result = post(url, headers=headers, data=data, verify=False)
    result.raise_for_status()
    json_result = result.json()
    token = json_result["access_token"]
    return token

def get_auth_header(token):
    return {"Authorization": "Bearer " + token}

def search_for_playlist(token, playlist_name):
    """Recherche une playlist par nom et renvoie le JSON brut."""
    url = "https://api.spotify.com/v1/search"
    headers = get_auth_header(token)
    query = f"?q={playlist_name}&type=playlist&market=FR&limit=5"
    query_url = url + query
    result = get(query_url, headers=headers, verify=False)
    result.raise_for_status()
    json_result = json.loads(result.content)
    return json_result

def get_tracks_from_playlist(token, playlist_id): 
    """Récupère les pistes d'une playlist (max 100).""" 
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks?limit=100" 
    headers = get_auth_header(token)
    result = get(url, headers=headers, verify=False)
    result.raise_for_status()
    json_result = json.loads(result.content)
    items = json_result.get("items", [])
    tracks = [it["track"] for it in items if it.get("track") is not None]
    return tracks

def build_dataframe():
    """Construit le DataFrame df à partir de la playlist Top 50 France."""
    token = get_token()

    # Tu peux aussi chercher la playlist dynamiquement si tu préfères
    playlist_id = "2IgPkhcHbgQ4s4PdCxljAx"  # Top 50 : France
    tracks = get_tracks_from_playlist(token, playlist_id)

    id_list=[]
    track_list=[]
    artist_list=[]
    feat_list=[]
    cover_list=[]
    popularity_list=[]
    relase_date_list=[]
    feat_list_temp=[]

    for idx, track in enumerate(tracks):
        id_list.append(track['id'])
        track_list.append(track['name'])
        artist_list.append(track['artists'][0]['name'])
        if len(track['artists']) > 1:
            for idx2, artist in enumerate(track['artists']):
                if idx2 > 0:
                    feat_list_temp.append(artist['name']) 
            feat_list.append(', '.join(feat_list_temp)) 
            feat_list_temp.clear()
        else:
            feat_list.append('None')

        # Pochette & infos
        cover_list.append(track['album']['images'][0]['url'])
        relase_date_list.append(track['album']['release_date'])
        popularity_list.append(track['popularity'])

    df = pd.DataFrame({
        "id": id_list,
        "track": track_list,
        "artist": artist_list,
        "feat": feat_list,
        "cover": cover_list,
        "release_date": relase_date_list,
        "popularity": popularity_list
    })

    display(df)
    return df


# =======================
#      DASH APP
# =======================

df = build_dataframe()

external_stylesheets = [dbc.themes.BOOTSTRAP]
app = Dash(__name__, external_stylesheets=external_stylesheets)

SPOTIFY_GREEN = "#1DB954"
SPOTIFY_DARK = "#191414"
SPOTIFY_LIGHT = "#FFFFFF"

app.layout = dbc.Container(
    fluid=True,
    style={"backgroundColor": SPOTIFY_DARK, "minHeight": "100vh", "padding": "20px"},
    children=[
        html.H1(
            "Spotify Top 50 France",
            style={
                "color": SPOTIFY_GREEN,
                "fontWeight": "bold",
                "marginBottom": "10px"
            },
        ),
        html.P(
            "Recherche et visualisation des morceaux de la playlist",
            style={"color": SPOTIFY_LIGHT, "marginBottom": "30px"},
        ),
        dbc.Row(
            [
                # --------- Colonne gauche : recherche + liste ----------
                dbc.Col(
                    width=4,
                    children=[
                        dbc.Card(
                            style={
                                "backgroundColor": "#000000",
                                "border": f"1px solid {SPOTIFY_GREEN}",
                                "borderRadius": "16px",
                                "padding": "15px",
                                "height": "100%",
                            },
                            children=[
                                html.H4(
                                    "Recherche de titre",
                                    style={"color": SPOTIFY_GREEN, "marginBottom": "15px"},
                                ),
                                dbc.Input(
                                    id="search-input",
                                    placeholder="Tape un titre, un artiste ou un feat...",
                                    type="text",
                                    style={
                                        "backgroundColor": "#121212",
                                        "color": SPOTIFY_LIGHT,
                                        "borderRadius": "999px",
                                        "border": "1px solid #333",
                                        "marginBottom": "15px",
                                    },
                                ),
                                html.Label(
                                    "Sélectionne un morceau :",
                                    style={"color": SPOTIFY_LIGHT, "marginBottom": "8px"},
                                ),
                                dcc.Dropdown(
                                    id="track-dropdown",
                                    options=[
                                        {
                                            "label": f"{row.artist} - {row.track}",
                                            "value": row.id,
                                        }
                                        for row in df.itertuples()
                                    ],
                                    placeholder="Choisis un titre",
                                    style={
                                        "backgroundColor": "#121212",
                                        "color": "#000000",
                                    },
                                ),
                            ],
                        )
                    ],
                ),

                # --------- Colonne droite : détails du morceau ----------
                dbc.Col(
                    width=8,
                    children=[
                        dbc.Card(
                            style={
                                "backgroundColor": "#000000",
                                "borderRadius": "16px",
                                "padding": "20px",
                                "border": "none",
                                "height": "100%",
                            },
                            children=[
                                dbc.Row(
                                    [
                                        # Pochette
                                        dbc.Col(
                                            width=4,
                                            children=[
                                                html.Img(
                                                    id="track-cover",
                                                    src="",
                                                    style={
                                                        "width": "100%",
                                                        "borderRadius": "12px",
                                                        "boxShadow": "0 4px 20px rgba(0,0,0,0.6)",
                                                    },
                                                )
                                            ],
                                        ),
                                        # Texte + actions
                                        dbc.Col(
                                            width=8,
                                            children=[
                                                html.H2(
                                                    id="track-title",
                                                    style={
                                                        "color": SPOTIFY_LIGHT,
                                                        "marginBottom": "10px",
                                                    },
                                                ),
                                                html.H4(
                                                    id="track-artist",
                                                    style={
                                                        "color": SPOTIFY_GREEN,
                                                        "marginBottom": "10px",
                                                    },
                                                ),
                                                html.P(
                                                    id="track-feat",
                                                    style={
                                                        "color": "#B3B3B3",
                                                        "marginBottom": "10px",
                                                    },
                                                ),
                                                html.P(
                                                    id="track-release",
                                                    style={
                                                        "color": "#B3B3B3",
                                                        "marginBottom": "5px",
                                                    },
                                                ),
                                                html.P(
                                                    id="track-popularity",
                                                    style={
                                                        "color": "#B3B3B3",
                                                        "marginBottom": "15px",
                                                    },
                                                ),
                                                html.A(
                                                    "Ouvrir dans Spotify",
                                                    id="track-link",
                                                    href="#",
                                                    target="_blank",
                                                    style={
                                                        "display": "inline-block",
                                                        "padding": "10px 20px",
                                                        "borderRadius": "999px",
                                                        "backgroundColor": SPOTIFY_GREEN,
                                                        "color": "#000000",
                                                        "fontWeight": "bold",
                                                        "textDecoration": "none",
                                                    },
                                                ),
                                                # --- Lecteur Spotify (embed officiel) ---
                                                html.Iframe(
                                                    id="track-embed",
                                                    src="",
                                                    style={
                                                        "width": "100%",
                                                        "height": "152px",  # hauteur standard pour un track
                                                        "border": "none",
                                                        "borderRadius": "12px",
                                                        "marginTop": "15px",
                                                    },
                                                    # Permissions recommandées par Spotify
                                                    allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture",
                                                ),
                                            ],
                                        ),
                                    ]
                                )
                            ],
                        )
                    ],
                ),
            ],
            style={"marginTop": "10px"},
        ),
    ],
)

# =======================
#      CALLBACKS DASH
# =======================

# 1) Mise à jour de la liste en fonction de la recherche
@app.callback(
    Output("track-dropdown", "options"),
    Input("search-input", "value"),
)
def update_dropdown(search_value):
    if not search_value:
        dff = df
    else:
        mask = (
            df["track"].str.contains(search_value, case=False, na=False)
            | df["artist"].str.contains(search_value, case=False, na=False)
            | df["feat"].str.contains(search_value, case=False, na=False)
        )
        dff = df[mask]

    options = [
        {"label": f"{row.artist} - {row.track}", "value": row.id}
        for row in dff.itertuples()
    ]
    return options

# 2) Mise à jour des infos + URL d'embed
@app.callback(
    [
        Output("track-title", "children"),
        Output("track-artist", "children"),
        Output("track-feat", "children"),
        Output("track-release", "children"),
        Output("track-popularity", "children"),
        Output("track-cover", "src"),
        Output("track-link", "href"),
        Output("track-embed", "src"),   # <--- nouvel output
    ],
    Input("track-dropdown", "value"),
)
def update_track_details(track_id):
    if not track_id:
        # État par défaut
        return (
            "Sélectionne un morceau",
            "",
            "",
            "",
            "",
            "",
            "#",
            "",
        )

    row = df[df["id"] == track_id].iloc[0]

    title = row["track"]
    artist = f"Artiste principal : {row['artist']}"
    feat = f"Feat : {row['feat']}" if row["feat"] != "None" else "Pas de featuring"
    release = f"Date de sortie : {row['release_date']}"
    pop = f"Popularité Spotify : {row['popularity']}/100"
    cover = row["cover"]
    link = f"https://open.spotify.com/track/{row['id']}"
    embed_src = f"https://open.spotify.com/embed/track/{row['id']}"

    return title, artist, feat, release, pop, cover, link, embed_src


if __name__ == "__main__":
    app.run(debug=True)
