# Retro Scene Rebuild — website builder

1. Public export (reads the store and index; never writes to them):

       python website/export.py B:/User/ClaudeCode/RetroSceneRebuild/export --workers 6

   Clean releases get a public .rsr (local source path stripped from the
   manifest). Releases whose .rsr carries pieces of original files are
   "listed" only: page, file list, CRCs and NFO, but no download.

2. Static site:

       python website/build_site.py B:/User/ClaudeCode/RetroSceneRebuild/export B:/User/ClaudeCode/RetroSceneRebuild/site --link

   `--link` hardlinks the .rsr files (same drive, local preview). Leave it
   off when building a copy to upload.

3. Preview: `python -m http.server 8765 -d B:/User/ClaudeCode/RetroSceneRebuild/site`
   then open http://127.0.0.1:8765/
