<h1><img src="assets/icons/kbroton-neon.png" alt="" width="48" align="center"> Kbroton Launcher</h1>

A Qt 6 desktop launcher for adding and running Windows games and apps with GE-Proton/Wine/Proton on Linux.

<img width="1170" height="878" alt="Captura_de_tela_20261018_233906" src="https://github.com/user-attachments/assets/df91237d-d800-4bb9-b2c1-596592226fba" />


## Requirements

- Python 3.10+
- PySide6
- Internet access only when downloading/updating GE-Proton or looking up online artwork


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
