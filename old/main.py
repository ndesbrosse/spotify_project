import pandas as pd
from pathlib import Path

from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score, f1_score, classification_report
import joblib

# ==========================
# 1. Chargement des données
# ==========================

DATA_PATH = Path("SpotifyAudioFeaturesApril2019.csv")  # adapte si besoin

df = pd.read_csv(DATA_PATH)

print("Shape:", df.shape)
print("Colonnes:", df.columns.tolist())

# Création du label
df["is_hit"] = (df["popularity"] >= 70).astype(int)

print(df["is_hit"].value_counts(normalize=True))


# Features audio
FEATURE_COLS = [
    "acousticness",
    "danceability",
    "duration_ms",
    "energy",
    "instrumentalness",
    "key",
    "liveness",
    "loudness",
    "mode",
    "speechiness",
    "tempo",
    "time_signature",
    "valence",
]

TARGET_COL = "is_hit"

# On enlève les lignes avec NaN sur les features (il ne devrait pas y en avoir, mais par sécurité)
df_model = df.dropna(subset=FEATURE_COLS + [TARGET_COL]).copy()

X = df_model[FEATURE_COLS]
y = df_model[TARGET_COL]

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42,
    stratify=y,  # important car la classe "hit" est rare
)

print("Train size:", X_train.shape, "Test size:", X_test.shape)
