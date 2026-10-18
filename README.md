<h1><img src="assets/icons/kbroton-neon.png" alt="" width="48" align="center"> Kbroton Game Launcher</h1>

A Qt 6 desktop launcher for adding and running Windows games and apps with GE-Proton/Wine/Proton on Linux.

<img width="1170" height="878" alt="Captura_de_tela_20261018_172859" src="https://github.com/user-attachments/assets/1ab94290-ca83-4c94-a2eb-e737eda01fa5" />

## Requirements

- Python 3.10+
- PySide6
- Internet access on startup to check and download the latest GE-Proton release

## Run

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Build distribution packages

Run the script on the corresponding distribution. Each script requires that
distribution's package-building tools and writes the package to `dist/`.

```sh
./build/ubuntu.sh  # Ubuntu: creates a .deb (requires dpkg-deb)
./build/fedora.sh  # Fedora: creates an .rpm (requires rpmbuild)
./build/arch.sh    # Arch Linux: creates a .pkg.tar.zst (requires makepkg)
```

The packages install the application under `/usr/lib/kbroton`, add the
`kbroton` command and a desktop launcher, and depend on the distribution's
Python and PySide6 packages. Set `VERSION` to override the package version
(default `1.0.0`) or `OUTPUT_DIR` to change the output directory.
