import requests

CA = r"C:\certs\SOLUTEC-RootCA_base64.crt"  # adapte le chemin

urls = [
    "https://www.google.com",
    "https://api.spotify.com/v1/search?q=acdc&type=artist&limit=1",
    "https://accounts.spotify.com/api/token",
]

for url in urls:
    print(f"Test: {url}")
    try:
        r = requests.get(url, timeout=15, verify=CA)
        print("  OK ->", r.status_code)
    except Exception as e:
        print("  ERREUR ->", repr(e))
