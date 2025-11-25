from dotenv import load_dotenv
import os, base64, json
from requests import post, get
import pandas as pd
import unicodedata

import dash
from dash import Dash, dcc, html, Input, Output, State, ctx, no_update
import dash_bootstrap_components as dbc

import string, random, threading
import time

from dash.dependencies import ClientsideFunction


pd.set_option("display.max_rows", None)
load_dotenv()

# ======================
#  Spotify helpers
# ======================
client_id = os.getenv("CLIENT_ID")
client_secret = os.getenv("CLIENT_SECRET")
DEFAULT_PLAYLIST_ID = "2IgPkhcHbgQ4s4PdCxljAx"  # Top 50 : France

# ======================
#  Stockage des parties multi (DEV)
# ======================
GAMES = {}  # code -> dict de partie
GAMES_LOCK = threading.Lock()


def generate_game_code(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))

def generate_player_id(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))



def create_game(code: str, tracks_records: list[dict]) -> dict:
    """
    tracks_records = df.to_dict('records') de la playlist sélectionnée.
    """
    return {
        "code": code,
        "tracks": tracks_records,   # toute la playlist
        "round_tracks": None,       # 10 titres pour la manche en cours
        "round_started_at": None,   # timestamp du début de la manche
        "answers": {},              # player_id -> {"order_ids": [...], "ok": int, "m": int}
    }



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

# --- Cache global des popularités corrigées ---
POPULARITY_CACHE_FILE = "popularity_cache.json"

try:
    with open(POPULARITY_CACHE_FILE, "r", encoding="utf-8") as f:
        POPULARITY_CACHE: dict[str, int] = json.load(f)
except FileNotFoundError:
    POPULARITY_CACHE = {}
except Exception:
    # en cas de fichier corrompu, on repart d'un cache vide
    POPULARITY_CACHE = {}


def popularity_cache_key(track_name: str, artist_name: str) -> str:
    """Clé de cache basée sur le nom normalisé du titre + artiste principal."""
    t_norm = normalize_name(track_name)
    a_norm = normalize_name(artist_name)
    if not t_norm or not a_norm:
        return ""
    return f"{t_norm}||{a_norm}"



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


def get_cached_best_popularity(token: str, track_name: str, artist_name: str, market: str = "FR") -> int | None:
    """
    Version avec cache de get_best_track_popularity.
    - si on a déjà calculé la meilleure popularité pour (titre, artiste) -> on la renvoie direct
    - sinon on interroge l'API, on stocke en JSON + mémoire, puis on renvoie
    """
    key = popularity_cache_key(track_name, artist_name)
    if not key:
        return None

    # 1) déjà en cache -> on renvoie directement
    if key in POPULARITY_CACHE:
        return POPULARITY_CACHE[key]

    # 2) sinon on calcule via l'API
    best = get_best_track_popularity(token, track_name, artist_name, market=market)
    if best is not None:
        POPULARITY_CACHE[key] = best
        # on essaie de persister sur disque (si possible)
        try:
            with open(POPULARITY_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(POPULARITY_CACHE, f)
        except Exception:
            # en cas d'erreur d'écriture, ce n'est pas bloquant
            pass

    return best

def get_playlist_info(tok, playlist_id: str):
    """
    Récupère quelques infos rapides sur la playlist :
    - nom
    - propriétaire
    - image
    - nombre total de titres
    """
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}"
    r = get(url, headers=get_auth_header(tok), verify=False)
    r.raise_for_status()
    data = r.json()

    name = data.get("name", "Playlist inconnue")
    owner = (data.get("owner") or {}).get("display_name", "Inconnu")
    images = data.get("images") or []
    image = images[0]["url"] if images else ""
    total = (data.get("tracks") or {}).get("total", 0)

    return {
        "id": playlist_id,
        "name": name,
        "owner": owner,
        "image": image,
        "total": total,
    }


def build_df_from_tracks(tracks, token: str):
    rows = []
    for track in tracks:
        artists = track.get("artists", [])
        main_artist = artists[0]["name"] if artists else "Inconnu"
        feat = ", ".join([a["name"] for a in artists[1:]]) if len(artists) > 1 else "None"
        images = track.get("album", {}).get("images", [])
        cover = images[0]["url"] if images else ""

        raw_pop = track.get("popularity", 0) or 0
        best_pop = raw_pop

        if main_artist != "Inconnu":
            # 🔥 on passe par la version CACHÉE
            alt_pop = get_cached_best_popularity(token, track["name"], main_artist)
            if alt_pop is not None:
                # on garde la meilleure des deux valeurs
                best_pop = max(best_pop, alt_pop)

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
    """
    Tire deux morceaux avec des popularités différentes.
    Si la playlist n'a pas au moins 2 popularités distinctes,
    on renvoie None.
    """
    if df is None or len(df) < 2:
        return None

    # On enlève les doublons de popularité : chaque ligne restante
    # a une popularité unique.
    df_unique = df.drop_duplicates(subset="popularity")

    # S'il n'y a pas au moins 2 popularités distinctes,
    # on ne peut pas faire de duel "différent vs différent".
    if len(df_unique) < 2:
        return None

    pair = df_unique.sample(2, replace=False).reset_index(drop=True)
    return {
        "left_id": pair.loc[0, "id"],
        "right_id": pair.loc[1, "id"],
    }



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
                dbc.NavbarBrand("Spotify Games", className="text-white", href="/"),
                dbc.Nav(
                    [
                        dbc.Button(
                            "Changer de playlist",
                            id="change-playlist",
                            color="dark",
                            className="me-3",
                            style={
                                "border": f"1px solid {SPOTIFY_GREEN}",
                                "background": "#000",
                                "color": SPOTIFY_LIGHT,
                            },
                        ),
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
                        dbc.Button(
                            "Classement (Multi 1v1)",
                            id="nav-ranking-multi",
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

# --------- PAGE ACCUEIL ----------
def layout_home():
    return html.Div(
        [
            html.H1(
                "Spotify Games",
                style={
                    "color": SPOTIFY_LIGHT,
                    "textAlign": "center",
                    "marginBottom": "12px",
                },
            ),
            html.P(
                "Choisis un mode de jeu pour tester ta connaissance des hits Spotify.",
                style={
                    "color": "#b3b3b3",
                    "textAlign": "center",
                    "maxWidth": "700px",
                    "margin": "0 auto 40px",
                },
            ),
            html.Div(
                [
                    # Carte pour le mode Duel
                    dcc.Link(
                        dbc.Card(
                            [
                                dbc.CardImg(
                                    src=app.get_asset_url("duel.png"),
                                    top=True,
                                    style={
                                        "objectFit": "cover",
                                        "height": "220px",   # légèrement plus grand
                                    },
                                ),
                                dbc.CardBody(
                                    html.H3(
                                        "Jeu 1 : Duel",
                                        className="card-title",
                                        style={
                                            "textAlign": "center",
                                            "color": SPOTIFY_LIGHT,  # même couleur que "Spotify Games"
                                        },
                                    )
                                ),
                            ],
                            style={
                                "width": "300px",                 # carré un peu plus grand
                                "cursor": "pointer",
                                "border": f"1px solid {SPOTIFY_GREEN}",
                                "backgroundColor": "#121212",
                            },
                        ),
                        href="/duel",
                        style={"textDecoration": "none"},
                    ),
                    # Carte pour le mode Classement
                    dcc.Link(
                        dbc.Card(
                            [
                                dbc.CardImg(
                                    src=app.get_asset_url("ranking.png"),
                                    top=True,
                                    style={
                                        "objectFit": "cover",
                                        "height": "220px",  # légèrement plus grand
                                    },
                                ),
                                dbc.CardBody(
                                    html.H3(
                                        "Jeu 2 : Classement",
                                        className="card-title",
                                        style={
                                            "textAlign": "center",
                                            "color": SPOTIFY_LIGHT,  # même couleur que "Spotify Games"
                                        },
                                    )
                                ),
                            ],
                            style={
                                "width": "300px",                 # carré un peu plus grand
                                "cursor": "pointer",
                                "border": f"1px solid {SPOTIFY_GREEN}",
                                "backgroundColor": "#121212",
                            },
                        ),
                        href="/ranking",
                        style={"textDecoration": "none"},
                    ),
                ],
                style={
                    "display": "flex",
                    "justifyContent": "center",
                    "gap": "32px",
                    "flexWrap": "wrap",
                },
            ),
        ],
        style={"paddingTop": "40px", "paddingBottom": "40px"},
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
                "Classe le Top 10 par popularité",
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
                        "Valider",
                        id="rank-validate",
                        color="success",
                        style={
                            "backgroundColor": SPOTIFY_GREEN,
                            "color": "#000",
                        },
                    ),
                    # --- bouton "Nouvelle partie" (caché au début) ---
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
            html.Div(
                id="rank-results",
                style={"maxWidth": "1000px", "margin": "8px auto"},
            ),
        ]
    )


def layout_ranking_multi():
    return html.Div(
        [
            html.H2(
                "Classement multi 1v1",
                style={
                    "color": SPOTIFY_LIGHT,
                    "textAlign": "center",
                    "marginBottom": "12px",
                },
            ),

            # Zone création / join
            html.Div(
                [
                    html.H4(
                        "Créer ou rejoindre une partie",
                        style={"color": SPOTIFY_LIGHT, "textAlign": "center"},
                    ),
                    html.Div(
                        [
                            dbc.Button(
                                "Créer une partie",
                                id="mp-create-game",
                                style={
                                    "background": "#000",
                                    "border": f"1px solid {SPOTIFY_GREEN}",
                                    "color": SPOTIFY_LIGHT,
                                    "marginRight": "8px",
                                },
                            ),
                            dbc.Input(
                                id="mp-code-input",
                                placeholder="Code de partie (ex: ABC123)",
                                type="text",
                                style={"maxWidth": "180px"},
                            ),
                            dbc.Button(
                                "Rejoindre",
                                id="mp-join-game",
                                style={
                                    "background": "#000",
                                    "border": f"1px solid {SPOTIFY_GREEN}",
                                    "color": SPOTIFY_LIGHT,
                                    "marginLeft": "8px",
                                },
                            ),
                        ],
                        style={
                            "display": "flex",
                            "justifyContent": "center",
                            "alignItems": "center",
                            "gap": "6px",
                            "flexWrap": "wrap",
                            "marginTop": "8px",
                        },
                    ),
                    html.Small(
                        id="mp-status-text",
                        style={
                            "color": "#b3b3b3",
                            "display": "block",
                            "marginTop": "8px",
                            "textAlign": "center",
                        },
                    ),
                    html.Div(
                        id="mp-game-info",
                        style={
                            "color": SPOTIFY_GREEN,
                            "textAlign": "center",
                            "marginTop": "4px",
                            "fontWeight": "bold",
                        },
                    ),
                ],
                style={"marginBottom": "16px"},
            ),

            dcc.Interval(id="mp-interval", interval=2000, n_intervals=0),

            # Zone de jeu
            html.Div(
                [
                    html.Div(
                        [
                            dbc.Button(
                                "Lancer / relancer la manche",
                                id="mp-start-round",
                                style={
                                    "backgroundColor": SPOTIFY_GREEN,
                                    "color": "#000",
                                    "fontWeight": "bold",
                                },
                            ),
                            dbc.Button(
                                "Valider mon classement",
                                id="mp-rank-validate",
                                style={
                                    "background": "#000",
                                    "border": f"1px solid {SPOTIFY_GREEN}",
                                    "color": SPOTIFY_LIGHT,
                                    "marginLeft": "10px",
                                },
                            ),
                        ],
                        style={
                            "display": "flex",
                            "justifyContent": "center",
                            "gap": "8px",
                            "flexWrap": "wrap",
                            "marginBottom": "8px",
                        },
                    ),
                    html.Div(
                        id="mp-rank-list-container",
                        style={
                            "width": "100%",
                            "maxWidth": "1200px",   # avant : 800px
                            "margin": "16px auto",
                        },
                    ),
                    html.Div(
                        id="mp-round-results",
                        style={"maxWidth": "1000px", "margin": "8px auto"},
                    ),
                ]
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
        dcc.Store(id="playlist-info", data=None),
        dcc.Store(id="pair-store", data=None),
        dcc.Store(id="selection-store", data={"selected": None}),
        dcc.Store(
            id="score-store",
            data={"score": 0, "streak": 0, "best": 0, "last_round_key": None},
        ),
        dcc.Store(id="ranking-tracks", data=None),
        dcc.Store(id="rank-order", data=[]),
        dcc.Store(id="rank-init", data=None),
        dcc.Store(id="mp-game-code", data=None),            # code de partie multi courant
        dcc.Store(id="mp-player-id", data=None),            # identifiant joueur multi
        dcc.Store(id="mp-rank-order", data=None),           # ordre envoyé quand on clique "Valider"
        dcc.Store(id="mp-rank-order-snapshot", data=None),  # dernier ordre observé dans la liste
        dcc.Store(id="mp-dnd-init", data=None),             # juste pour initialiser le drag&drop multi
        dcc.Store(id="mp-validated", data=False),  # le joueur a cliqué sur "Valider" ?

        # modale playlist
        dbc.Modal(
            id="playlist-modal",
            is_open=False,
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
        html.Div(
            id="playlist-summary",
            style={
                "padding": "0 24px",
                "marginTop": "8px",
                "color": "#B3B3B3",
                "fontSize": "13px",
            },
        ),
        dbc.Container(
            id="page-content",
            style={
                "minHeight": "100vh",
                "padding": "24px",
                "width": "100%",
                "maxWidth": "min(1400px, 98vw)",  # avant : 1100px, 95vw
                "margin": "0 auto",
            },
            children=[
                # 3 pages toujours présentes, on joue juste sur display
                html.Div(id="page-home", children=layout_home()),
                html.Div(
                    id="page-duel",
                    children=layout_duel(),
                    style={"display": "none"},
                ),
                html.Div(
                    id="page-ranking",
                    children=layout_ranking(),
                    style={"display": "none"},
                ),
                html.Div(
                    id="page-ranking-multi",
                    children=layout_ranking_multi(),
                    style={"display": "none"},
                ),
            ],
        ),
    ],
)




# ======================
#  Router
# ======================
@app.callback(
    Output("page-home", "style"),
    Output("page-duel", "style"),
    Output("page-ranking", "style"),
    Output("page-ranking-multi", "style"),
    Input("url", "pathname"),
)
def route(path):
    # Tout caché par défaut
    home_style = {"display": "none"}
    duel_style = {"display": "none"}
    ranking_style = {"display": "none"}
    ranking_multi_style = {"display": "none"}

    if path == "/duel":
        duel_style["display"] = "block"
    elif path == "/ranking":
        ranking_style["display"] = "block"
    elif path == "/ranking-multi":
        ranking_multi_style["display"] = "block"
    else:
        # page d'accueil par défaut
        home_style["display"] = "block"

    return home_style, duel_style, ranking_style, ranking_multi_style



@app.callback(
    Output("url", "pathname", allow_duplicate=True),
    Input("nav-duel", "n_clicks"),
    Input("nav-ranking", "n_clicks"),
    Input("nav-ranking-multi", "n_clicks"),
    prevent_initial_call=True,
)
def nav(n_duel, n_rank, n_rank_multi):
    trig = ctx.triggered_id
    if trig == "nav-ranking-multi":
        return "/ranking-multi"
    if trig == "nav-ranking":
        return "/ranking"
    if trig == "nav-duel":
        return "/duel"
    return no_update




# ======================
#  Playlist loading
# ======================

@app.callback(
    Output("df-store", "data"),
    Output("playlist-info", "data"),
    Output("playlist-modal", "is_open"),
    Output("modal-error", "children"),
    Input("load-playlist", "n_clicks"),
    Input("use-default", "n_clicks"),
    Input("url", "pathname"),
    State("playlist-input", "value"),
    State("df-store", "data"),
)
def load_playlist(n_load, n_default, path, raw_value, df_data):
    trig = ctx.triggered_id

    # 1) Changement de page : ouvrir la modale uniquement sur les pages de jeu,
    #    et seulement si aucune playlist n'est encore chargée.
    if trig == "url":
        if path in ("/duel", "/ranking", "/ranking-multi") and not df_data:
            # On arrive sur un mode de jeu sans playlist -> ouvrir la modale
            return no_update, no_update, True, no_update
        else:
            # Sur la home, ou bien une playlist existe déjà -> modale fermée
            return no_update, no_update, False, no_update

    # 2) Clic sur "Utiliser Top 50 : France"
    if trig == "use-default":
        playlist_id = DEFAULT_PLAYLIST_ID

    # 3) Clic sur "Charger" avec une valeur saisie
    elif trig == "load-playlist":
        playlist_id = parse_playlist_id(raw_value or "")
        if not playlist_id:
            return (
                no_update,
                no_update,
                True,
                "Veuillez entrer un ID ou une URL de playlist valide.",
            )

    # 4) Autre chose (ne devrait pas arriver)
    else:
        return no_update, no_update, no_update, no_update

    # --- Chargement réel de la playlist ---
    try:
        token = get_token()
        tracks = get_tracks_from_playlist(token, playlist_id)
        df = build_df_from_tracks(tracks, token)

        # 👉 Récup des infos playlist
        info = get_playlist_info(token, playlist_id)
        info["loaded_count"] = len(tracks)  # nb de titres chargés pour le jeu

    except Exception:
        return (
            no_update,
            no_update,
            True,
            "Impossible de charger la playlist (ID invalide ou inaccessible).",
        )

    if df is None or df.empty or len(df) < 2:
        return (
            no_update,
            no_update,
            True,
            "Playlist vide/inaccessible ou contenant moins de 2 titres.",
        )

    # Succès : on stocke le df + les infos playlist + on ferme la modale
    return df.to_dict("records"), info, False, ""

@app.callback(
    Output("playlist-modal", "is_open", allow_duplicate=True),
    Input("change-playlist", "n_clicks"),
    prevent_initial_call=True,
)
def open_playlist_modal_from_nav(n):
    if n:
        # On force l'ouverture de la fenêtre de choix de playlist
        return True
    return no_update


@app.callback(
    Output("playlist-summary", "children"),
    Input("playlist-info", "data"),
)
def render_playlist_summary(info):
    if not info:
        return html.Span("Aucune playlist sélectionnée.",
                         style={"color": "#777"})

    name = info.get("name", "Playlist inconnue")
    owner = info.get("owner", "Inconnu")
    total = info.get("total", 0)
    loaded = info.get("loaded_count", 0)
    img = info.get("image", "")

    parts = []

    if img:
        parts.append(
            html.Img(
                src=img,
                style={
                    "width": "32px",
                    "height": "32px",
                    "objectFit": "cover",
                    "borderRadius": "4px",
                    "marginRight": "8px",
                    "verticalAlign": "middle",
                },
            )
        )

    parts.append(
        html.Span(
            f"Playlist : {name}",
            style={"color": SPOTIFY_LIGHT, "fontWeight": "bold"},
        )
    )
    parts.append(
        html.Span(
            f" • par {owner}",
            style={"marginLeft": "4px"},
        )
    )
    parts.append(
        html.Span(
            f" • {loaded} titres chargés (sur {total})",
            style={"marginLeft": "4px", "color": "#9aa0a6"},
        )
    )

    return html.Div(parts, style={"display": "flex", "alignItems": "center"})



# ======================
#  Logique DUEL
# ======================
@app.callback(
    Output("pair-store", "data", allow_duplicate=True),
    Output("selection-store", "data", allow_duplicate=True),
    Output("score-store", "data"),
    Input("df-store", "data"),
    prevent_initial_call=True,
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
    Output("selection-store", "data", allow_duplicate=True),
    Output("score-store", "data", allow_duplicate=True),
    Input("url", "pathname"),
    State("df-store", "data"),
    State("score-store", "data"),
    prevent_initial_call=True,
)
def reset_duel_on_nav(path, records, score_state):
    # On ne fait quelque chose que si on arrive sur /duel
    # et qu'une playlist est déjà chargée
    if path != "/duel" or not records:
        return no_update, no_update, no_update

    df = pd.DataFrame(records)
    pair = pick_two_ids_from_df(df)

    # Si pas encore de state, on repart de zéro
    if not score_state:
        score_state = {
            "score": 0,
            "streak": 0,
            "best": 0,
            "last_round_key": None,
        }
    else:
        # On remet seulement la streak à 0,
        # on garde le best et le score si tu veux les exploiter plus tard
        score_state = score_state.copy()
        score_state["streak"] = 0
        score_state["last_round_key"] = None  # pour une nouvelle manche propre

    # Nouvelle paire + aucune réponse + streak reset
    return pair, {"selected": None}, score_state





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
        "",      # left-overlay children
        "",      # right-overlay children
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
        else:
            correct_side = "left" if lp > rp else "right"
            if sel == correct_side:
                if sel == "left":
                    left_style = add_border(left_style, SPOTIFY_GREEN)
                else:
                    right_style = add_border(right_style, SPOTIFY_GREEN)
            else:
                if sel == "left":
                    left_style = add_border(left_style, RED_BAD)
                    right_style = add_border(right_style, SPOTIFY_GREEN)
                else:
                    right_style = add_border(right_style, RED_BAD)
                    left_style = add_border(left_style, SPOTIFY_GREEN)
                

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
    Output("streak-value", "children"),
    Output("best-value", "children"),
    Input("score-store", "data"),
)
def render_scoreboard(store):
    return (
        str(store.get("streak", 0)),
        str(store.get("best", 0)),
    )



# ======================
#  Logique RANKING
# ======================
@app.callback(
    Output("ranking-tracks", "data"),
    Input("url", "pathname"),
    Input("rank-newgame", "n_clicks"),
    Input("df-store", "data"),
    prevent_initial_call=True,
)
def make_ranking_set(path, _n_new, records):
    # On ne fait quelque chose que si :
    # - on est sur la page /ranking
    # - une playlist est chargée
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

@app.callback(
    Output("mp-rank-list-container", "children"),
    Input("mp-interval", "n_intervals"),
    State("mp-game-code", "data"),
    State("mp-player-id", "data"),
    State("mp-validated", "data"),
    State("mp-rank-order-snapshot", "data"),
)
def render_mp_rank_list(_n, code, player_id, validated, snapshot_order):
    if not code:
        return html.Div(
            "Crée ou rejoins une partie pour voir les titres.",
            style={"color": "#aaa", "textAlign": "center"},
        )

    with GAMES_LOCK:
        game = GAMES.get(code)
        if not game:
            return html.Div(
                "Partie introuvable (le créateur a peut-être quitté).",
                style={"color": "#aaa", "textAlign": "center"},
            )

        round_tracks = game.get("round_tracks")
        started_at = game.get("round_started_at")
        answers = game.get("answers") or {}

    # Manche pas encore lancée ou pas de titres
    if not round_tracks or not started_at:
        return html.Div(
            "En attente que quelqu’un lance la manche.",
            style={"color": "#aaa", "textAlign": "center"},
        )

    # Chrono & condition d'affichage des résultats
    now = time.time()
    elapsed = now - started_at
    remaining = max(0, 60 - int(elapsed))
    results_ready = (elapsed >= 60) or (len(answers) >= 2)

    # ========= AVANT LES RÉSULTATS =========
    if not results_ready:
        # Si le joueur a déjà cliqué sur "Valider" -> on masque la liste
        if validated:
            return html.Div(
                f"Classement envoyé, en attente de ton adversaire… (temps restant : {remaining}s)",
                style={"color": "#b3b3b3", "textAlign": "center", "marginTop": "8px"},
            )

        # Sinon on affiche la liste + timer + hint
        items = [_track_li(t, i) for i, t in enumerate(round_tracks)]
        ul = html.Ul(
            items,
            id="mp-rank-list",
            style={
                "padding": 0,
                "margin": "8px auto",
                "display": "flex",
                "flexDirection": "column",
                "gap": "10px",
            },
        )
        timer_div = html.Div(
            f"Temps restant : {remaining}s",
            style={
                "textAlign": "center",
                "color": SPOTIFY_LIGHT,
                "marginBottom": "4px",
                "fontWeight": "bold",
            },
        )
        hint = html.Div(
            "Fais glisser les lignes pour les réordonner, puis clique sur Valider.",
            style={"textAlign": "center", "color": "#9aa0a6", "marginBottom": "8px"},
        )
        return [timer_div, hint, ul]

    # ========= RÉSULTATS (la liste disparaît) =========
    df = pd.DataFrame(round_tracks)
    truth = df.sort_values("popularity", ascending=False).reset_index(drop=True)
    truth_ids = list(truth["id"])
    by_id = {t["id"]: t for t in round_tracks}

    # ---- Colonne centrale : classement correct ----
    truth_items = []
    for _, row in truth.iterrows():
        truth_items.append(
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
        )

    center_col = html.Div(
        [
            html.H4("Classement correct", style={"color": SPOTIFY_LIGHT}),
            html.Ul(truth_items, style={"padding": 0}),
        ],
        style={"flex": "1", "minWidth": "260px"},
    )

    # ---- Helper pour construire une colonne joueur ----
    def build_user_col(order_ids, title_prefix):
        if not order_ids:
            return html.Div(
                [
                    html.H4(title_prefix, style={"color": SPOTIFY_LIGHT}),
                    html.P(
                        "Aucun classement disponible pour ce joueur.",
                        style={"color": "#b3b3b3"},
                    ),
                ],
                style={"flex": "1", "minWidth": "260px"},
            )

        user = [by_id[i] for i in order_ids if i in by_id]
        m = min(len(truth_ids), len(user))
        user = user[:m]

        ok = 0
        lis = []
        for idx, t in enumerate(user):
            correct_id = truth_ids[idx]
            good = t["id"] == correct_id
            if good:
                ok += 1
            lis.append(
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

        return html.Div(
            [
                html.H4(
                    f"{title_prefix} — {ok}/{m} bien placés",
                    style={"color": SPOTIFY_LIGHT},
                ),
                html.Ul(lis, style={"padding": 0}),
            ],
            style={"flex": "1", "minWidth": "260px"},
        )

    # ---- Ordre du joueur courant (gauche) ----
    order_me = None
    ans_me = answers.get(player_id) if player_id else None

    if ans_me and ans_me.get("order_ids"):
        order_me = ans_me["order_ids"]
    elif snapshot_order:
        # le joueur n'a pas validé mais on a le snapshot local
        order_me = snapshot_order
    else:
        # fallback : ordre initial des titres
        order_me = [t["id"] for t in round_tracks]

    left_col = build_user_col(order_me, "Ton classement")

    # ---- Ordre de l'adversaire (droite) ----
    opponent_order = None
    if answers and player_id:
        opponent_id = None
        for pid in answers.keys():
            if pid != player_id:
                opponent_id = pid
                break
        if opponent_id:
            opponent_order = answers[opponent_id].get("order_ids")

    right_col = build_user_col(opponent_order, "Classement de ton adversaire")

    header = html.Div(
        "Résultats de la manche",
        style={
            "textAlign": "center",
            "color": SPOTIFY_LIGHT,
            "fontWeight": "bold",
            "marginBottom": "10px",
        },
    )
    return html.Div(
        [
            header,
            html.Div(
                [left_col, center_col, right_col],
                style={
                    "display": "grid",
                    "gridTemplateColumns": "repeat(3, minmax(0, 1fr))",
                    "gap": "24px",  # ou 32px
                    "alignItems": "flex-start",
                },
            ),
        ]
    )






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


# ======================
#  Multi ranking : lobby (création / join)
# ======================
@app.callback(
    Output("mp-game-code", "data"),
    Output("mp-player-id", "data"),
    Output("mp-status-text", "children", allow_duplicate=True),
    Input("mp-create-game", "n_clicks"),
    Input("mp-join-game", "n_clicks"),
    State("df-store", "data"),
    State("mp-code-input", "value"),
    State("mp-player-id", "data"),
    prevent_initial_call=True,
)

def handle_multi_lobby(n_create, n_join, df_data, code_input, player_id_current):
    trig = ctx.triggered_id

    # On garde un player_id par onglet/fenêtre
    player_id = player_id_current or generate_player_id()

    # Création de partie : on utilise la playlist du df-store
    if trig == "mp-create-game":
        if not df_data:
            return no_update, no_update, "Charge d'abord une playlist (bouton 'Changer de playlist' en haut)."

        code = generate_game_code()
        with GAMES_LOCK:
            while code in GAMES:
                code = generate_game_code()
            GAMES[code] = create_game(code, df_data)

        return code, player_id, f"Partie créée ! Code à partager : {code}"

    # Rejoindre une partie existante
    if trig == "mp-join-game":
        code = (code_input or "").strip().upper()
        if not code:
            return no_update, no_update, "Entre un code de partie."
        with GAMES_LOCK:
            if code not in GAMES:
                return no_update, no_update, "Partie introuvable. Vérifie le code."
        return code, player_id, f"Tu as rejoint la partie {code}."

    return no_update, no_update, no_update



@app.callback(
    Output("mp-game-info", "children"),
    Input("mp-game-code", "data"),
)
def show_mp_game_info(code):
    if not code:
        return "Aucune partie en cours."
    return f"Partie en cours : {code}"

@app.callback(
    Output("mp-status-text", "children", allow_duplicate=True),
    Input("mp-start-round", "n_clicks"),
    State("mp-game-code", "data"),
    prevent_initial_call=True,
)
def mp_start_round(n_start, code):
    if not n_start:
        return no_update
    if not code:
        return "Crée ou rejoins d'abord une partie."

    with GAMES_LOCK:
        game = GAMES.get(code)
        if not game:
            return "Partie introuvable (le créateur a peut-être quitté)."

        df = pd.DataFrame(game["tracks"])
        if df.empty:
            return "Cette partie n'a pas de titres associés."

        df_unique = df.sample(frac=1).drop_duplicates(subset="popularity")
        if len(df_unique) < 2:
            return "Pas assez de titres avec des popularités distinctes."

        n = min(10, len(df_unique))
        game["round_tracks"] = df_unique.head(n).to_dict("records")

        # 🔥 reset chrono + réponses
        game["round_started_at"] = time.time()
        game["answers"] = {}

    return "Manche lancée ! Classe les 10 titres puis clique sur Valider."



# === CLIENTSIDE CALLBACKS : DRAG & DROP ===

# Classement solo : initialisation du drag & drop
app.clientside_callback(
    ClientsideFunction(namespace="ranking", function_name="init_dnd"),
    Output("rank-init", "data"),
    Input("url", "pathname"),
    Input("ranking-tracks", "data"),
    prevent_initial_call=True,
)

# Classement solo : lecture de l'ordre au clic sur "Valider"
app.clientside_callback(
    ClientsideFunction(namespace="ranking", function_name="read_order"),
    Output("rank-order", "data"),
    Input("rank-validate", "n_clicks"),
    prevent_initial_call=True,
)

# Multi : initialisation du drag & drop
app.clientside_callback(
    ClientsideFunction(namespace="rankingMulti", function_name="init_dnd"),
    Output("mp-dnd-init", "data"),
    Input("url", "pathname"),
    Input("mp-game-code", "data"),
    prevent_initial_call=True,
)

# Multi : lecture de l'ordre au clic sur "Valider mon classement"
app.clientside_callback(
    ClientsideFunction(namespace="rankingMulti", function_name="read_order"),
    Output("mp-rank-order", "data"),
    Input("mp-rank-validate", "n_clicks"),
    prevent_initial_call=True,
)

# Multi : snapshot de l'ordre toutes les 2s (pour le cas où le joueur ne clique pas sur Valider)
app.clientside_callback(
    ClientsideFunction(namespace="rankingMulti", function_name="snapshot_order"),
    Output("mp-rank-order-snapshot", "data"),
    Input("mp-interval", "n_intervals"),
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

@app.callback(
    Output("mp-status-text", "children", allow_duplicate=True),
    Input("mp-rank-order", "data"),
    State("mp-game-code", "data"),
    State("mp-player-id", "data"),
    prevent_initial_call=True,
)
def submit_mp_ranking(order_ids, code, player_id):
    if not order_ids or not code or not player_id:
        return "Aucune liste à valider."

    with GAMES_LOCK:
        game = GAMES.get(code)
        if not game or not game.get("round_tracks"):
            return "Partie ou manche introuvable."

        round_tracks = game["round_tracks"]
        df = pd.DataFrame(round_tracks)
        truth = df.sort_values("popularity", ascending=False).reset_index(drop=True)
        truth_ids = list(truth["id"])
        by_id = {t["id"]: t for t in round_tracks}

        user = [by_id[i] for i in order_ids if i in by_id]
        m = min(len(truth_ids), len(user))
        ok = 0
        for idx in range(m):
            if user[idx]["id"] == truth_ids[idx]:
                ok += 1

        answers = game.setdefault("answers", {})
        answers[player_id] = {
            "order_ids": order_ids,
            "ok": ok,
            "m": m,
        }

    return "Classement reçu ✅ En attente de la réponse de ton adversaire ou de la fin du chrono (60s)…"




if __name__ == "__main__":
    app.run(debug=True)
