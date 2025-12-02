import os
import requests

# Target directory
STATIC_DIR = os.path.join("portal", "static", "portal", "vendor")
os.makedirs(STATIC_DIR, exist_ok=True)

ASSETS = {
    "tailwindcss.js": "https://cdn.tailwindcss.com",
    "htmx.min.js": "https://unpkg.com/htmx.org@1.9.6/dist/htmx.min.js",
    "phosphor.js": "https://unpkg.com/@phosphor-icons/web",
    "simple-datatables.css": "https://cdn.jsdelivr.net/npm/simple-datatables@3.2.0/dist/style.css",
    "simple-datatables.js": "https://cdn.jsdelivr.net/npm/simple-datatables@3.2.0/dist/umd/simple-datatables.js"
}

print(f"Downloading assets to {STATIC_DIR}...")

for filename, url in ASSETS.items():
    print(f" - Fetching {filename}...")
    try:
        response = requests.get(url)
        if response.status_code == 200:
            with open(os.path.join(STATIC_DIR, filename), "wb") as f:
                f.write(response.content)
        else:
            print(f"Error downloading {filename}: Status {response.status_code}")
    except Exception as e:
        print(f"Failed to download {filename}: {e}")

print("Done! You can now update dashboard.html to use {% static 'portal/vendor/...' %}")