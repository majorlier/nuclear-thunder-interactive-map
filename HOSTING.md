# Nuclear Thunder Map — hosting

This release is a static website. It has no build step and does not require
Python on the public server.

## GitHub Pages

1. Create a new public GitHub repository, for example `nuclear-thunder-map`.
2. Upload everything in this folder to the repository root. `index.html` must
   be at the top level, alongside `map_data.json`.
3. Open the repository's **Settings**, then **Pages**.
4. Under **Build and deployment**, choose **Deploy from a branch**.
5. Select the `main` branch and `/ (root)`, then save.
6. After GitHub finishes deploying, open the URL shown on the Pages settings
   screen. It will usually be:
   `https://YOUR-USERNAME.github.io/nuclear-thunder-map/`

Keep all file and folder names unchanged. In particular, preserve the `icons`
folder and the relative paths of all JSON and image files.

## Other static hosts

Upload this folder unchanged to any static web host and configure
`index.html` as the entry page. There is no server-side code.

## Local test

Open PowerShell in this folder and run:

```powershell
py -m http.server 8000
```

Then open <http://localhost:8000/>. Stop the server with `Ctrl+C`.

Do not rely on double-clicking `index.html`: browsers commonly block its local
JSON requests when loaded through a `file://` address.

The map currently loads Leaflet from `unpkg.com`, so visitors need an internet
connection even though the map data and imagery are included in this release.
