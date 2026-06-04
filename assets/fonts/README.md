# Fonts

The Style tab's font picker shows whatever TTF/OTF files live in this
folder. They're loaded at startup at the process level (CoreText on macOS,
GDI on Windows, fontconfig on Linux) — no system install needed.

Press Start 2P is the default. To grab every recommended font in one shot:

```bash
mkdir -p assets/fonts
GFONTS=https://github.com/google/fonts/raw/main/ofl
curl -sL -o assets/fonts/PressStart2P-Regular.ttf  $GFONTS/pressstart2p/PressStart2P-Regular.ttf
curl -sL -o assets/fonts/VT323-Regular.ttf         $GFONTS/vt323/VT323-Regular.ttf
curl -sL -o assets/fonts/Silkscreen-Regular.ttf    $GFONTS/silkscreen/Silkscreen-Regular.ttf
curl -sL -o assets/fonts/PixelifySans-Regular.ttf  "$GFONTS/pixelifysans/PixelifySans%5Bwght%5D.ttf"
curl -sL -o assets/fonts/MajorMonoDisplay-Regular.ttf $GFONTS/majormonodisplay/MajorMonoDisplay-Regular.ttf
curl -sL -o assets/fonts/ShareTechMono-Regular.ttf $GFONTS/sharetechmono/ShareTechMono-Regular.ttf
curl -sL -o assets/fonts/JetBrainsMono-Regular.ttf https://github.com/JetBrains/JetBrainsMono/raw/master/fonts/ttf/JetBrainsMono-Regular.ttf
```

Restart and pick from the dropdown.

If a font isn't there, Tk falls back to the system sans-serif. Won't break
anything.

## Adding a new one

1. Drop the file here.
2. Add a `(label, family, size)` entry to `_AVAILABLE_FONT_FAMILIES` in
   `nfo_gui.py`. The family string must match what's embedded in the TTF
   (open the file once in Font Book on macOS to see the name).
3. Restart.
