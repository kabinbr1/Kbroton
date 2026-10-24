import hashlib
import argparse
import json
import os
import platform
import re
import shutil
import signal
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path
from pathlib import PurePosixPath
from urllib.parse import quote, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PySide6.QtCore import QProcess, QProcessEnvironment, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QFont, QIcon, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QSizePolicy,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QCheckBox,
)

from localization import LANGUAGE_OPTIONS, resolve_language, set_language, tr

APP_NAME = "Kbroton"
APP_VERSION = "1.1.0"
APP_CATEGORIES = (
    ("Game", "Game"),
    ("AudioVideo", "Audio/Video"),
    ("Development", "Development"),
    ("Education", "Education"),
    ("Graphics", "Graphics"),
    ("Network", "Network"),
    ("Office", "Office"),
    ("Science", "Science"),
    ("Settings", "Settings"),
    ("System", "System"),
    ("Utility", "Utility"),
)
APP_CATEGORY_IDS = {category for category, _ in APP_CATEGORIES}
PROTON_GE_RELEASES_API = (
    "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases/latest"
)
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")).expanduser()
DATA_DIR = DATA_HOME / APP_NAME
LEGACY_DATA_DIR = DATA_HOME / "aster-launcher"
CONFIG_FILE = DATA_DIR / "library.json"
PROTON_GE_DIR = DATA_DIR / "proton-ge"
APP_ICON_PATH = Path(__file__).resolve().parent / "assets/icons/kbroton-neon.png"
STEAM_ROOTS = [
    Path.home() / ".steam/steam",
    Path.home() / ".local/share/Steam",
    Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
]


def migrate_legacy_data_directory():
    global DATA_DIR, CONFIG_FILE, PROTON_GE_DIR

    if LEGACY_DATA_DIR == DATA_DIR or not LEGACY_DATA_DIR.exists():
        return ""
    if LEGACY_DATA_DIR.is_symlink() or not LEGACY_DATA_DIR.is_dir():
        return f"Legacy data path is not a regular directory: {LEGACY_DATA_DIR}"

    try:
        DATA_HOME.mkdir(parents=True, exist_ok=True)
        if not DATA_DIR.exists():
            LEGACY_DATA_DIR.replace(DATA_DIR)
            return ""

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conflicts = []
        for source in LEGACY_DATA_DIR.iterdir():
            target = DATA_DIR / source.name
            if target.exists():
                conflicts.append(source.name)
                continue
            source.replace(target)
        if conflicts:
            return (
                "Some legacy data could not be merged because Kbroton already has "
                f"items with the same names: {', '.join(sorted(conflicts))}. "
                f"Unmerged data remains in {LEGACY_DATA_DIR}."
            )
        try:
            LEGACY_DATA_DIR.rmdir()
        except OSError:
            pass
        return ""
    except OSError as error:
        if not CONFIG_FILE.exists() and (LEGACY_DATA_DIR / "library.json").is_file():
            DATA_DIR = LEGACY_DATA_DIR
            CONFIG_FILE = DATA_DIR / "library.json"
            PROTON_GE_DIR = DATA_DIR / "proton-ge"
        return f"Could not fully migrate launcher data: {error}"


def rebase_legacy_proton_path(path):
    if not path:
        return path
    try:
        relative_path = Path(path).relative_to(LEGACY_DATA_DIR)
    except ValueError:
        return path
    return str(DATA_DIR / relative_path)


def find_proton_tools():
    tools = set()
    for root in STEAM_ROOTS:
        for folder in (root / "steamapps/common", root / "compatibilitytools.d"):
            if folder.is_dir():
                tools.update(folder.glob("*/proton"))
    return sorted((tool for tool in tools if tool.is_file()), key=lambda item: str(item.parent).lower())


def default_steam_root():
    return next((root for root in STEAM_ROOTS if root.is_dir()), STEAM_ROOTS[0])


def load_state():
    try:
        state = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
        if not isinstance(state, dict) or not isinstance(state.get("games", []), list):
            raise ValueError("The launcher library has an invalid format.")
        proton_path = state.get("proton_path", "")
        proton_ge_path = state.get("proton_ge_path", "")
        if not isinstance(proton_path, str) or not isinstance(proton_ge_path, str):
            raise ValueError("The launcher library has invalid Proton settings.")
        steamgriddb_api_key = state.get("steamgriddb_api_key", "")
        if not isinstance(steamgriddb_api_key, str):
            raise ValueError("The SteamGridDB API key setting is invalid.")
        language = state.get("language", "system")
        if not isinstance(language, str):
            raise ValueError("The language setting is invalid.")
        if language == "en_GB":
            language = "en_US"
        automatic_proton_ge_updates = state.get("automatic_proton_ge_updates", False)
        proton_ge_prompt_dismissed = state.get("proton_ge_prompt_dismissed", False)
        artwork_notice_dismissed = state.get("artwork_notice_dismissed", False)
        if not isinstance(automatic_proton_ge_updates, bool) or not isinstance(
            proton_ge_prompt_dismissed, bool
        ) or not isinstance(artwork_notice_dismissed, bool):
            raise ValueError("The application settings are invalid.")
        try:
            set_language(language)
        except ValueError as error:
            QMessageBox.warning(None, tr("Invalid language setting"), str(error))
            language = "system"
        proton_path = rebase_legacy_proton_path(proton_path)
        proton_ge_path = rebase_legacy_proton_path(proton_ge_path)
        games = state.get("games", [])
        game_ids = set()
        compat_paths = set()
        for game in games:
            if (
                not isinstance(game, dict)
                or any(not isinstance(game.get(key), str) or not game[key] for key in
                       ("id", "name", "executable", "proton_path"))
            ):
                raise ValueError("A game entry in the launcher library is invalid.")
            uuid.UUID(game["id"])
            if "custom_icon" in game and not isinstance(game["custom_icon"], bool):
                raise ValueError("A game entry has an invalid custom-icon setting.")
            category = game.setdefault("category", "Game")
            if not isinstance(category, str) or category not in APP_CATEGORY_IDS:
                raise ValueError("A game entry has an invalid application category.")
            custom_icon_revision = game.get("custom_icon_revision")
            if custom_icon_revision is not None:
                try:
                    valid_icon_revision = (
                        isinstance(custom_icon_revision, str)
                        and uuid.UUID(hex=custom_icon_revision).hex == custom_icon_revision
                    )
                except ValueError:
                    valid_icon_revision = False
                if not valid_icon_revision:
                    raise ValueError("A game entry has an invalid custom-icon revision.")
            compat_data_path = game.get("compat_data_path")
            if compat_data_path is not None and (
                not isinstance(compat_data_path, str)
                or not Path(compat_data_path).is_absolute()
                or not Path(compat_data_path).name.isalnum()
            ):
                raise ValueError("A game entry has an invalid compatibility-data path.")
            if game["id"] in game_ids:
                raise ValueError("The launcher library contains duplicate game IDs.")
            game_ids.add(game["id"])
            if compat_data_path is not None:
                normalized_compat_path = str(Path(compat_data_path).resolve())
                if normalized_compat_path in compat_paths:
                    raise ValueError("Multiple games use the same compatibility-data path.")
                compat_paths.add(normalized_compat_path)
            game["proton_path"] = rebase_legacy_proton_path(game["proton_path"])
        return {
            "proton_path": proton_path,
            "proton_ge_path": proton_ge_path,
            "steamgriddb_api_key": steamgriddb_api_key,
            "language": language,
            "automatic_proton_ge_updates": automatic_proton_ge_updates,
            "proton_ge_prompt_dismissed": proton_ge_prompt_dismissed,
            "artwork_notice_dismissed": artwork_notice_dismissed,
            "games": games,
        }
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
        QMessageBox.warning(
            None,
            tr("Could not load library"),
            tr(
                "Your library could not be read. A fresh library will be used.\n\n{error}",
                error=error,
            ),
        )
        return {
            "proton_path": "",
            "proton_ge_path": "",
            "language": "system",
            "automatic_proton_ge_updates": False,
            "proton_ge_prompt_dismissed": False,
            "artwork_notice_dismissed": False,
            "games": [],
        }


def save_state(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_file = CONFIG_FILE.with_suffix(".tmp")
    descriptor = os.open(
        temporary_file,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        state_file = os.fdopen(descriptor, "w")
        descriptor = -1
        with state_file:
            state_file.write(json.dumps(state, indent=2))
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    temporary_file.replace(CONFIG_FILE)


def proton_label(path):
    return Path(path).parent.name


def prefix_directory_name(app_name):
    return "".join(character for character in app_name.casefold() if character.isalnum()) or "app"


def steam_root_for(proton_path):
    for parent in Path(proton_path).parents:
        if parent.name == "steamapps":
            return parent.parent
        if parent.name == "compatibilitytools.d":
            return parent.parent
    return default_steam_root()


def proton_ge_architecture():
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    if machine in ("aarch64", "arm64"):
        return "aarch64"
    raise RuntimeError(f"GE-Proton does not provide a supported build for {machine}.")


def github_request(url):
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Kbroton-Game-Launcher",
        },
    )
    return urlopen(request, timeout=30)


def safe_extract_proton(archive_path, destination):
    destination.mkdir()
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        member_paths = {}
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or not path.parts or ".." in path.parts:
                raise ValueError(f"Unsafe path in Proton archive: {member.name}")
            member_paths[member.name.rstrip("/")] = member
            if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                raise ValueError(f"Unsupported entry in Proton archive: {member.name}")
            if member.issym() or member.islnk():
                link = PurePosixPath(member.linkname)
                if link.is_absolute():
                    raise ValueError(f"Unsafe link in Proton archive: {member.name}")
                resolved = (
                    PurePosixPath(member.name).parent / link
                    if member.issym()
                    else link
                )
                normalized = PurePosixPath(os.path.normpath(str(resolved)))
                if normalized.is_absolute() or ".." in normalized.parts:
                    raise ValueError(f"Unsafe link in Proton archive: {member.name}")

        symlink_names = {name for name, member in member_paths.items() if member.issym()}
        for name in symlink_names:
            if any(other.startswith(name + "/") for other in member_paths):
                raise ValueError(f"Symlink is used as a directory in Proton archive: {name}")

        for member in members:
            path = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                path.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"Could not read Proton archive entry: {member.name}")
                with source, path.open("xb") as output:
                    shutil.copyfileobj(source, output)
                path.chmod(member.mode & 0o777)

        for member in members:
            path = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.issym():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(member.linkname)
            elif member.islnk():
                link = destination.joinpath(*PurePosixPath(member.linkname).parts)
                if not link.is_file() or link.is_symlink():
                    raise ValueError(f"Invalid hard link in Proton archive: {member.name}")
                path.parent.mkdir(parents=True, exist_ok=True)
                os.link(link, path)

        for member in reversed(members):
            if member.isdir():
                path = destination.joinpath(*PurePosixPath(member.name).parts)
                path.chmod(member.mode & 0o777)


class ProtonGeUpdater(QThread):
    status_changed = Signal(str)
    download_progress = Signal(int, int)
    installed = Signal(str, str)
    failed = Signal(str)

    def run(self):
        try:
            self._update()
        except (HTTPError, URLError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
            if not self.isInterruptionRequested():
                self.failed.emit(str(error))

    def _update(self):
        architecture = proton_ge_architecture()
        self.status_changed.emit("Checking for the latest GE-Proton release…")
        with github_request(PROTON_GE_RELEASES_API) as response:
            release = json.load(response)

        tag = release["tag_name"]
        assets = {
            asset["name"]: asset
            for asset in release["assets"]
            if isinstance(asset, dict) and isinstance(asset.get("name"), str)
        }
        archive_name = f"{tag}-{architecture}.tar.gz"
        checksum_name = f"{tag}-{architecture}.sha512sum"
        if (
            not re.fullmatch(r"GE-Proton[\w.-]+", tag, re.ASCII)
            or archive_name not in assets
            or checksum_name not in assets
        ):
            raise ValueError(f"The latest GE-Proton release has no {architecture} archive and checksum.")

        target = PROTON_GE_DIR / tag
        backup = PROTON_GE_DIR / f".{tag}.previous"
        if target.is_symlink() or backup.is_symlink():
            raise ValueError("Refusing to replace a symlink in the GE-Proton installation directory.")
        proton_script = target / "proton"
        if proton_script.is_file() and os.access(proton_script, os.X_OK):
            self.status_changed.emit(f"{tag} is already installed; it is up to date.")
            self.installed.emit(str(proton_script), tag)
            return

        archive_asset = assets[archive_name]
        checksum_asset = assets[checksum_name]
        if (
            urlparse(archive_asset["browser_download_url"]).hostname != "github.com"
            or urlparse(checksum_asset["browser_download_url"]).hostname != "github.com"
            or not isinstance(archive_asset["size"], int)
            or archive_asset["size"] <= 0
        ):
            raise ValueError("GitHub returned invalid GE-Proton release asset metadata.")

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PROTON_GE_DIR.mkdir(parents=True, exist_ok=True)
        self.status_changed.emit(f"Downloading {tag} from GitHub…")

        with github_request(checksum_asset["browser_download_url"]) as response:
            checksum_text = response.read(4096).decode("ascii")
        checksum_parts = checksum_text.split()
        if len(checksum_parts) < 2 or checksum_parts[-1].lstrip("*") != archive_name:
            raise ValueError("The GE-Proton release checksum does not match its archive.")
        expected_digest = checksum_parts[0].lower()
        if not re.fullmatch(r"[0-9a-f]{128}", expected_digest):
            raise ValueError("The GE-Proton release contains an invalid SHA-512 checksum.")

        with tempfile.TemporaryDirectory(prefix=".ge-proton-", dir=PROTON_GE_DIR) as temp_dir:
            temporary_dir = Path(temp_dir)
            archive_path = temporary_dir / archive_name
            digest = hashlib.sha512()
            downloaded = 0
            with github_request(archive_asset["browser_download_url"]) as response:
                with archive_path.open("wb") as archive_file:
                    while chunk := response.read(1024 * 1024):
                        if self.isInterruptionRequested():
                            return
                        archive_file.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        self.download_progress.emit(downloaded, archive_asset["size"])
            if downloaded != archive_asset["size"]:
                raise ValueError(
                    f"Incomplete GE-Proton download ({downloaded} of {archive_asset['size']} bytes)."
                )
            if digest.hexdigest() != expected_digest:
                raise ValueError("The downloaded GE-Proton archive failed SHA-512 verification.")

            self.status_changed.emit(f"Verifying and unpacking {tag}…")
            extracted_dir = temporary_dir / "extracted"
            safe_extract_proton(archive_path, extracted_dir)
            expected_script = extracted_dir / tag / "proton"
            candidates = (
                [expected_script]
                if expected_script.is_file()
                else [path for path in extracted_dir.rglob("proton") if path.is_file()]
            )
            if len(candidates) != 1 or not os.access(candidates[0], os.X_OK):
                raise ValueError("The downloaded GE-Proton archive does not contain one executable proton script.")

            installed_root = candidates[0].parent
            if backup.exists():
                shutil.rmtree(backup)
            if target.exists():
                target.replace(backup)
            try:
                installed_root.replace(target)
            except OSError:
                if backup.exists() and not target.exists():
                    backup.replace(target)
                raise
            if backup.exists():
                shutil.rmtree(backup)

        self.download_progress.emit(archive_asset["size"], archive_asset["size"])
        self.status_changed.emit(f"{tag} is ready.")
        self.installed.emit(str(PROTON_GE_DIR / tag / "proton"), tag)


class SteamGridDBIconLookup(QThread):
    icon_found = Signal(str, bytes)
    icon_not_found = Signal(str)
    failed = Signal(str, str)

    def __init__(self, game_id, name, api_key, parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.name = name
        self.api_key = api_key

    def run(self):
        try:
            search = self.get_json(
                "https://www.steamgriddb.com/api/v2/search/autocomplete/"
                + quote(self.name, safe=""),
            )
            if self.isInterruptionRequested():
                return
            candidates = search.get("data", [])
            match = self.best_name_match(self.name, candidates)
            if match is None:
                self.icon_not_found.emit(self.game_id)
                return

            icons = self.get_json(
                f"https://www.steamgriddb.com/api/v2/icons/game/{match['id']}",
            ).get("data", [])
            if self.isInterruptionRequested():
                return
            icon_url = self.highest_resolution_icon_url(icons)
            if not icon_url:
                self.icon_not_found.emit(self.game_id)
                return
            parsed_url = urlparse(icon_url)
            if (
                parsed_url.scheme != "https"
                or not parsed_url.hostname
                or not (
                    parsed_url.hostname == "steamgriddb.com"
                    or parsed_url.hostname.endswith(".steamgriddb.com")
                )
            ):
                raise ValueError("SteamGridDB returned an invalid icon URL.")

            request = Request(
                icon_url,
                headers={"User-Agent": "Kbroton-Game-Launcher"},
            )
            with urlopen(request, timeout=20) as response:
                final_url = urlparse(response.geturl())
                if (
                    final_url.scheme != "https"
                    or not final_url.hostname
                    or not (
                        final_url.hostname == "steamgriddb.com"
                        or final_url.hostname.endswith(".steamgriddb.com")
                    )
                ):
                    raise ValueError("SteamGridDB redirected the icon request to an invalid URL.")
                image_data = response.read(10 * 1024 * 1024 + 1)
            if self.isInterruptionRequested():
                return
            if len(image_data) > 10 * 1024 * 1024:
                raise ValueError("The SteamGridDB icon is larger than 10 MB.")
            self.icon_found.emit(self.game_id, image_data)
        except (HTTPError, URLError, OSError, ValueError, KeyError, TypeError) as error:
            self.failed.emit(self.game_id, str(error))

    @staticmethod
    def highest_resolution_icon_url(icons):
        fallback_url = None
        best_url = None
        best_dimensions = (0, 0)
        for item in icons:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            url = item["url"]
            if fallback_url is None:
                fallback_url = url
            width = item.get("width")
            height = item.get("height")
            if (
                isinstance(width, bool)
                or not isinstance(width, int)
                or isinstance(height, bool)
                or not isinstance(height, int)
                or width <= 0
                or height <= 0
            ):
                continue
            dimensions = (width * height, min(width, height))
            if dimensions > best_dimensions:
                best_url = url
                best_dimensions = dimensions
        return best_url or fallback_url

    def get_json(self, url):
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "Kbroton-Game-Launcher",
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=20) as response:
            payload = response.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError("SteamGridDB returned an unexpectedly large response.")
        result = json.loads(payload)
        if not isinstance(result, dict) or result.get("success") is not True:
            raise ValueError("SteamGridDB returned an invalid response.")
        data = result.get("data")
        if not isinstance(data, list):
            raise ValueError("SteamGridDB returned invalid artwork data.")
        return result

    @staticmethod
    def normalize_name(name):
        tokens = re.findall(r"[a-z0-9]+", name.casefold())
        return tokens[1:] if tokens and tokens[0] in {"a", "an", "the"} else tokens

    @staticmethod
    def compact_name(name):
        return re.sub(r"[^a-z0-9]", "", name.casefold())

    @classmethod
    def best_name_match(cls, query, candidates):
        query_tokens = cls.normalize_name(query)
        compact_query = cls.compact_name(query)
        if not query_tokens:
            return None

        ranked = []
        for item in candidates:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), int)
                or not isinstance(item.get("name"), str)
            ):
                continue
            title_tokens = cls.normalize_name(item["name"])
            if cls.compact_name(item["name"]) == compact_query:
                score = 0
            elif title_tokens == query_tokens:
                score = 0
            elif (
                len(query_tokens) >= 2
                and title_tokens[:len(query_tokens)] == query_tokens
            ):
                score = 1
            elif len(query_tokens) >= 2:
                match_index = next(
                    (
                        index for index in range(len(title_tokens) - len(query_tokens) + 1)
                        if title_tokens[index:index + len(query_tokens)] == query_tokens
                    ),
                    None,
                )
                if match_index is None:
                    continue
                score = 2
            else:
                continue
            ranked.append((score, item))

        if not ranked:
            return None
        ranked.sort(key=lambda result: result[0])
        return ranked[0][1]


class LauncherWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        migration_message = migrate_legacy_data_directory()
        self.state = load_state()
        set_language(self.state.get("language", "system"))
        if migration_message:
            QMessageBox.warning(
                None,
                tr("Kbroton data migration"),
                tr(
                    "{message}\n\nKbroton is using: {directory}",
                    message=migration_message,
                    directory=DATA_DIR,
                ),
            )
        detected_proton = self.available_proton_tools()
        if not self.state.get("proton_path") and detected_proton:
            self.state["proton_path"] = str(detected_proton[0])
        self.first_run_proton_prompt = (
            not self.has_installed_proton_ge()
            and not self.state.get("proton_ge_prompt_dismissed", False)
            and not self.state.get("automatic_proton_ge_updates", False)
        )
        self.game_processes = {}
        self.game_sessions = {}
        self.game_status_labels = {}
        self.game_play_buttons = {}
        self.game_force_stop_buttons = {}
        self.force_stop_pending = set()
        self.game_settings_buttons = {}
        self.game_proton_labels = {}
        self.game_winecfg_buttons = {}
        self.game_icon_labels = {}
        self.icon_lookup_workers = {}
        self.log_buffers = {}
        self.selected_log_game = None
        self.setWindowTitle("kbroton")
        self.resize(1040, 720)
        self.setMinimumSize(760, 560)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        navigation = QToolBar(tr("Main navigation"), self)
        navigation.setObjectName("mainNavigation")
        navigation.setMovable(False)
        navigation.setFloatable(False)
        navigation.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, navigation)

        centered_navigation = QWidget()
        centered_navigation.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        centered_layout = QHBoxLayout(centered_navigation)
        centered_layout.setContentsMargins(0, 0, 0, 0)
        centered_layout.setSpacing(6)
        centered_layout.addStretch(1)

        self.nav_actions = {}
        navigation_group = QActionGroup(self)
        navigation_group.setExclusive(True)
        for key, text in (
            ("installed", tr("Library")),
            ("install", tr("Install new app/game")),
            ("settings", tr("Settings")),
        ):
            action = QAction(text, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, page=key: self.show_page(page))
            navigation_group.addAction(action)
            self.nav_actions[key] = action
            button = QToolButton(centered_navigation)
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            centered_layout.addWidget(button)
        centered_layout.addStretch(1)
        navigation.addWidget(centered_navigation)

        self.artwork_key_notice = QFrame()
        self.artwork_key_notice.setObjectName("artworkKeyNotice")
        self.artwork_key_notice.setFrameShape(QFrame.Shape.StyledPanel)
        notice_layout = QHBoxLayout(self.artwork_key_notice)
        notice_layout.setContentsMargins(12, 4, 8, 4)
        notice_layout.setSpacing(8)
        notice_text = QLabel(
            tr("Add a SteamGridDB key in Settings to show artwork for your apps.")
        )
        notice_text.setObjectName("artworkKeyNoticeText")
        notice_text.setWordWrap(True)
        notice_layout.addWidget(notice_text, 1)
        close_notice = QPushButton("×")
        close_notice.setObjectName("textButton")
        close_notice.setProperty("noticeClose", True)
        close_notice.setFixedSize(30, 30)
        close_notice.setAccessibleName(tr("Dismiss artwork setup notice"))
        close_notice.setToolTip(tr("Dismiss"))
        close_notice.clicked.connect(self.artwork_key_notice.hide)
        notice_layout.addWidget(close_notice)
        never_ask_notice = QPushButton(tr("Never ask again"))
        never_ask_notice.setObjectName("textButton")
        never_ask_notice.clicked.connect(self.dismiss_artwork_notice_permanently)
        notice_layout.addWidget(never_ask_notice)
        self.artwork_key_notice.setVisible(
            not bool(self.state.get("steamgriddb_api_key", "").strip())
            and not self.state.get("artwork_notice_dismissed", False)
        )
        layout.addWidget(self.artwork_key_notice)

        self.proton_update_panel = QFrame()
        self.proton_update_panel.setObjectName("protonUpdatePanel")
        self.proton_update_panel.setFrameShape(QFrame.Shape.Panel)
        self.proton_update_panel.setFrameShadow(QFrame.Shadow.Raised)
        self.proton_notice_timer = QTimer(self)
        self.proton_notice_timer.setSingleShot(True)
        self.proton_notice_timer.setInterval(6000)
        self.proton_notice_timer.timeout.connect(self.proton_update_panel.hide)
        update_layout = QVBoxLayout(self.proton_update_panel)
        update_layout.setContentsMargins(14, 9, 14, 9)
        update_layout.setSpacing(5)
        update_title = QLabel(tr("Compatibility tools"))
        update_title.setObjectName("gameTitle")
        update_layout.addWidget(update_title)
        update_row = QHBoxLayout()
        self.proton_update_status = QLabel()
        self.proton_update_status.setObjectName("smallMuted")
        update_row.addWidget(self.proton_update_status, 1)
        self.proton_update_dismiss = QPushButton("×")
        self.proton_update_dismiss.setObjectName("protonUpdateDismiss")
        self.proton_update_dismiss.setProperty("noticeClose", True)
        self.proton_update_dismiss.setFixedSize(30, 30)
        self.proton_update_dismiss.setAccessibleName(tr("Dismiss"))
        self.proton_update_dismiss.setToolTip(tr("Dismiss"))
        self.proton_update_dismiss.clicked.connect(self.dismiss_proton_update_notice)
        update_row.addWidget(self.proton_update_dismiss)
        self.proton_progress = QProgressBar()
        self.proton_progress.setObjectName("protonProgress")
        self.proton_progress.setTextVisible(False)
        self.proton_progress.setFixedHeight(5)
        self.proton_progress.hide()
        update_layout.addLayout(update_row)
        update_layout.addWidget(self.proton_progress)
        self.proton_update_panel.hide()
        layout.addWidget(self.proton_update_panel)

        self.pages = QStackedWidget()
        self.install_page = self.build_install_page()
        self.installed_page = self.build_installed_page()
        self.settings_page = self.build_settings_page()
        self.pages.addWidget(self.scrollable_page(self.install_page))
        self.pages.addWidget(self.scrollable_page(self.installed_page))
        self.pages.addWidget(self.scrollable_page(self.settings_page))
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.show_page("installed")
        self.refresh_games()
        for game in self.state.get("games", []):
            try:
                self.write_game_desktop_entry(game)
            except OSError as error:
                QMessageBox.warning(
                    self,
                    tr("Could not create app menu entry"),
                    tr(
                        "Kbroton could not create a desktop launcher for {name}:\n\n{error}",
                        name=game["name"],
                        error=error,
                    ),
                )
            self.request_game_icon(game)
        self.refresh_proton_options()
        self.update_install_access()
        if self.state.get("automatic_proton_ge_updates", False):
            self.start_proton_update()
        if self.first_run_proton_prompt:
            QTimer.singleShot(0, self.prompt_for_proton)
        self.process_monitor = QTimer(self)
        self.process_monitor.timeout.connect(self.update_game_process_status)
        self.process_monitor.start(1500)

    @staticmethod
    def scrollable_page(page):
        scroll = QScrollArea()
        scroll.setObjectName("tabScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(page)
        return scroll

    def start_proton_update(self):
        if hasattr(self, "proton_updater") and self.proton_updater.isRunning():
            return
        self.proton_updater = ProtonGeUpdater(self)
        self.proton_updater.status_changed.connect(self.set_proton_update_status)
        self.proton_updater.download_progress.connect(self.update_download_progress)
        self.proton_updater.installed.connect(self.on_proton_installed)
        self.proton_updater.failed.connect(self.on_proton_update_failed)
        self.proton_updater.finished.connect(self.on_proton_update_finished)
        if hasattr(self, "proton_update_button"):
            self.proton_update_button.setEnabled(False)
        self.proton_updater.start()

    def prompt_for_proton(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle(tr("Set up Proton"))
        dialog.setText(
            tr(
                "GE-Proton is a compatibility layer that enables Windows games and "
                "applications to run on Linux. No Proton version was detected, so we "
                "recommend installing GE-Proton. Would you like to install it now?"
                if not self.available_proton_tools()
                else "GE-Proton is a compatibility layer that enables Windows games "
                "and applications to run on Linux. We detected an existing Proton "
                "version on your system, so installing GE-Proton is not necessary. "
                "Would you like to install it anyway?"
            )
        )
        download_button = dialog.addButton(
            tr("Download GE-Proton"), QMessageBox.ButtonRole.AcceptRole
        )
        settings_button = dialog.addButton(
            tr("Choose an existing Proton"), QMessageBox.ButtonRole.ActionRole
        )
        not_now_button = dialog.addButton(
            tr("Not now"), QMessageBox.ButtonRole.RejectRole
        )
        dialog.exec()
        if dialog.clickedButton() == download_button:
            self.state["proton_ge_prompt_dismissed"] = True
            self.persist_state()
            self.start_proton_update()
        elif dialog.clickedButton() in (settings_button, not_now_button):
            self.state["proton_ge_prompt_dismissed"] = True
            self.persist_state()
            if dialog.clickedButton() == settings_button:
                self.show_page("settings")

    def has_installed_proton_ge(self):
        saved_path = self.state.get("proton_ge_path", "")
        candidates = [Path(saved_path)] if saved_path else []
        if PROTON_GE_DIR.is_dir():
            candidates.extend(PROTON_GE_DIR.glob("*/proton"))
        return any(
            path.is_file() and os.access(path, os.X_OK)
            for path in candidates
        )

    def on_proton_update_finished(self):
        if hasattr(self, "proton_update_button"):
            self.proton_update_button.setEnabled(True)

    def dismiss_proton_update_notice(self):
        self.proton_notice_timer.stop()
        self.proton_update_panel.hide()

    def closeEvent(self, event):
        if hasattr(self, "proton_updater") and self.proton_updater.isRunning():
            self.proton_updater.requestInterruption()
            if not self.proton_updater.wait(1000):
                self.proton_update_status.setText(
                    tr("Finishing the Proton update before closing…")
                )
                event.ignore()
                return
        for worker in list(self.icon_lookup_workers.values()):
            worker.requestInterruption()
            if worker.isRunning() and not worker.wait(1000):
                event.ignore()
                return
        event.accept()

    def set_proton_update_status(self, message):
        is_downloading = message.startswith("Downloading ")
        is_unpacking = message.startswith("Verifying ")
        is_checking = message == "Checking for the latest GE-Proton release…"
        is_up_to_date = message.endswith(" is already installed; it is up to date.")
        is_ready = message.endswith(" is ready.")
        if is_checking:
            message = tr("Checking for the latest GE-Proton release…")
        elif message.startswith("Downloading "):
            tag = message[len("Downloading "):-len(" from GitHub…")]
            message = tr("Downloading {tag} from GitHub…", tag=tag)
        elif message.startswith("Verifying and unpacking "):
            tag = message[len("Verifying and unpacking "):-1]
            message = tr("Verifying and unpacking {tag}…", tag=tag)
        elif is_up_to_date:
            tag = message.removesuffix(" is already installed; it is up to date.")
            message = tr("{tag} is already installed; it is up to date.", tag=tag)
        elif is_ready:
            tag = message.removesuffix(" is ready.")
            message = tr("{tag} is ready.", tag=tag)
        self.proton_update_status.setText(message)
        if is_ready or is_up_to_date:
            self.proton_notice_timer.start()
        elif is_downloading or is_unpacking or is_checking:
            self.proton_notice_timer.stop()
        self.proton_update_panel.setVisible(
            is_checking or is_downloading or is_unpacking or is_up_to_date or is_ready
        )
        self.proton_progress.setVisible(is_downloading or is_unpacking)
        if is_downloading:
            self.proton_progress.setRange(0, 0)
        elif is_unpacking:
            self.proton_progress.setRange(0, 0)

    def update_download_progress(self, downloaded, total):
        self.proton_progress.setRange(0, total)
        self.proton_progress.setValue(downloaded)

    def on_proton_installed(self, proton_path, tag):
        previous_ge_path = self.state.get("proton_ge_path", "")
        current_default = self.state.get("proton_path", "")
        previous_default = current_default
        if not current_default or current_default == previous_ge_path:
            self.state["proton_path"] = proton_path
        self.state["proton_ge_path"] = proton_path
        if not self.persist_state():
            self.state["proton_path"] = previous_default
            self.state["proton_ge_path"] = previous_ge_path
            self.proton_progress.hide()
            self.set_proton_update_status(
                tr(
                    "{tag} was installed, but {app} could not save it as the default Proton.",
                    tag=tag,
                    app=APP_NAME,
                )
            )
            self.refresh_proton_options()
            self.update_install_access()
            return
        self.refresh_proton_options()
        self.update_install_access()
        self.proton_progress.hide()

    def on_proton_update_failed(self, message):
        self.proton_progress.hide()
        self.proton_update_status.setText(tr("GE-Proton update failed: {message}", message=message))
        self.proton_update_status.setToolTip(message)
        self.proton_update_panel.hide()
        self.update_install_access()
        QMessageBox.warning(
            self,
            tr("GE-Proton update failed"),
            tr("GE-Proton could not be downloaded and extracted:\n\n{message}", message=message),
        )

    def show_page(self, name):
        if name == "install" and (
            not self.state.get("proton_path")
            or not Path(self.state["proton_path"]).is_file()
        ):
            return
        indexes = {"install": 0, "installed": 1, "settings": 2}
        self.pages.setCurrentIndex(indexes[name])
        self.nav_actions[name].setChecked(True)
        if name == "installed":
            self.refresh_games()
        elif name == "install":
            self.refresh_proton_options()

    def build_install_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        header = QFrame()
        header.setObjectName("installHeaderPanel")
        header.setFrameShape(QFrame.Shape.StyledPanel)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(18, 12, 18, 12)
        header_layout.setSpacing(4)
        heading = QLabel(tr("Bring your library together."))
        heading.setObjectName("heading")
        heading_font = heading.font()
        heading_font.setPointSize(heading_font.pointSize() + 4)
        heading_font.setWeight(QFont.Weight.DemiBold)
        heading.setFont(heading_font)
        intro = QLabel(
            tr(
                "Add a Windows game or app you already have and launch it with Proton. "
                "Each one gets its own compatibility prefix."
            )
        )
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        header_layout.addWidget(heading)
        header_layout.addWidget(intro)
        layout.addWidget(header)

        form = QFrame()
        form.setObjectName("installFormPanel")
        form.setFrameShape(QFrame.Shape.StyledPanel)
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(24, 22, 24, 24)
        form_layout.setSpacing(12)
        form_layout.addWidget(self.field_label(tr("GAME/APP NAME")))
        self.game_name = QLineEdit()
        self.game_name.setPlaceholderText(tr("e.g. The Witcher 3"))
        form_layout.addWidget(self.game_name)
        form_layout.addWidget(self.field_label(tr("WINDOWS EXECUTABLE")))
        file_row = QHBoxLayout()
        self.exe_path = QLineEdit()
        self.exe_path.setPlaceholderText(tr("Choose the game's or app's .exe file"))
        self.exe_path.textChanged.connect(self.update_name_from_path)
        browse_exe = QPushButton(tr("Browse"))
        browse_exe.setObjectName("secondaryButton")
        browse_exe.clicked.connect(self.choose_executable)
        file_row.addWidget(self.exe_path, 1)
        file_row.addWidget(browse_exe)
        form_layout.addLayout(file_row)
        form_layout.addWidget(self.field_label(tr("APP CATEGORY")))
        self.app_category = QComboBox()
        self.app_category.setMinimumHeight(44)
        for category, label in APP_CATEGORIES:
            self.app_category.addItem(tr(label), category)
        form_layout.addWidget(self.app_category)
        form_layout.addWidget(self.field_label(tr("PROTON VERSION")))
        proton_row = QHBoxLayout()
        self.install_proton = QComboBox()
        self.install_proton.setMinimumHeight(44)
        proton_row.addWidget(self.install_proton, 1)
        settings_link = QPushButton(tr("Manage Proton"))
        settings_link.setObjectName("textButton")
        settings_link.clicked.connect(lambda: self.show_page("settings"))
        proton_row.addWidget(settings_link)
        form_layout.addLayout(proton_row)
        form_layout.addWidget(self.field_label(tr("PREFIX LOCATION")))
        prefix_row = QHBoxLayout()
        self.prefix_location = QLineEdit(str(DATA_DIR / "compatdata"))
        self.prefix_location.setPlaceholderText(
            tr("Choose a folder or mounted drive for game prefixes")
        )
        prefix_row.addWidget(self.prefix_location, 1)
        browse_prefix = QPushButton(tr("Browse"))
        browse_prefix.setObjectName("secondaryButton")
        browse_prefix.clicked.connect(self.choose_prefix_location)
        prefix_row.addWidget(browse_prefix)
        form_layout.addLayout(prefix_row)
        prefix_hint = QLabel(
            tr("Each app gets its own prefix folder here. The prefix is created on first launch.")
        )
        prefix_hint.setObjectName("smallMuted")
        prefix_hint.setWordWrap(True)
        form_layout.addWidget(prefix_hint)
        self.add_game_button = QPushButton(tr("Add to library"))
        self.add_game_button.setObjectName("primaryButton")
        self.add_game_button.setMinimumHeight(48)
        self.add_game_button.clicked.connect(self.add_game)
        form_layout.addWidget(self.add_game_button)
        layout.addWidget(form)
        layout.addStretch(1)
        return page

    def update_install_access(self):
        proton_path = self.state.get("proton_path", "")
        ready = bool(proton_path and Path(proton_path).is_file())
        install_action = self.nav_actions.get("install")
        if install_action:
            install_action.setEnabled(ready)
            install_action.setToolTip(
                tr(
                    "No Proton version was found. Choose or install a Proton version "
                    "in Settings before adding a game."
                )
                if not ready else ""
            )
        if hasattr(self, "install_page"):
            self.install_page.setEnabled(ready)

    def build_installed_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(14)

        header = QFrame()
        header.setObjectName("libraryHeaderPanel")
        header.setFrameShape(QFrame.Shape.StyledPanel)
        section_header = QHBoxLayout(header)
        section_header.setContentsMargins(18, 12, 18, 12)
        heading = QLabel(tr("Your library"))
        heading.setObjectName("heading")
        heading_font = heading.font()
        heading_font.setPointSize(heading_font.pointSize() + 4)
        heading_font.setWeight(QFont.Weight.DemiBold)
        heading.setFont(heading_font)
        section_header.addWidget(heading)
        section_header.addStretch()
        self.game_count = QLabel()
        self.game_count.setObjectName("muted")
        count_panel = QFrame()
        count_panel.setObjectName("countPanel")
        count_panel.setFrameShape(QFrame.Shape.Panel)
        count_panel.setFrameShadow(QFrame.Shadow.Sunken)
        count_layout = QHBoxLayout(count_panel)
        count_layout.setContentsMargins(10, 4, 10, 4)
        count_layout.addWidget(self.game_count)
        section_header.addWidget(count_panel)
        layout.addWidget(header)

        self.game_list = QVBoxLayout()
        self.game_list.setSpacing(14)
        list_body = QWidget()
        list_body.setLayout(self.game_list)
        layout.addWidget(list_body, 1)

        self.logs_panel = QFrame()
        self.logs_panel.setObjectName("logsPanel")
        self.logs_panel.setFrameShape(QFrame.Shape.Panel)
        self.logs_panel.setFrameShadow(QFrame.Shadow.Raised)
        logs_layout = QVBoxLayout(self.logs_panel)
        logs_layout.setContentsMargins(12, 8, 12, 10)
        logs_layout.setSpacing(8)
        log_header = QHBoxLayout()
        self.log_title = QLabel(tr("Game logs"))
        self.log_title.setObjectName("gameTitle")
        log_header.addWidget(self.log_title)
        log_header.addStretch()
        self.hide_logs_button = QPushButton(tr("Show logs"))
        self.hide_logs_button.setObjectName("textButton")
        self.hide_logs_button.clicked.connect(self.toggle_logs)
        log_header.addWidget(self.hide_logs_button)
        logs_layout.addLayout(log_header)
        self.game_log_view = QPlainTextEdit()
        self.game_log_view.setObjectName("gameLogView")
        self.game_log_view.setReadOnly(True)
        self.game_log_view.setMaximumBlockCount(3000)
        self.game_log_view.setPlaceholderText(
            tr("Select a game to view its Proton and Wine output.")
        )
        self.game_log_view.setFixedHeight(150)
        self.game_log_view.hide()
        logs_layout.addWidget(self.game_log_view)
        layout.addWidget(self.logs_panel)
        return page

    def build_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        header = QFrame()
        header.setObjectName("settingsHeaderPanel")
        header.setFrameShape(QFrame.Shape.StyledPanel)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(18, 12, 18, 12)
        header_layout.setSpacing(4)
        heading = QLabel(tr("Settings"))
        heading.setObjectName("heading")
        heading_font = heading.font()
        heading_font.setPointSize(heading_font.pointSize() + 4)
        heading_font.setWeight(QFont.Weight.DemiBold)
        heading.setFont(heading_font)
        title_row = QHBoxLayout()
        title_row.addWidget(heading)
        title_row.addStretch()
        version_badge = QLabel(f"v{APP_VERSION}")
        version_badge.setObjectName("appVersionBadge")
        title_row.addWidget(version_badge)
        intro = QLabel(
            tr("Choose the default compatibility tool for newly added apps and games.")
        )
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        header_layout.addLayout(title_row)
        header_layout.addWidget(intro)
        layout.addWidget(header)

        panel = QFrame()
        panel.setObjectName("settingsFormPanel")
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        form = QVBoxLayout(panel)
        form.setContentsMargins(24, 22, 24, 24)
        form.setSpacing(12)
        language_panel = QFrame()
        language_panel.setObjectName("settingsFormPanel")
        language_panel.setFrameShape(QFrame.Shape.StyledPanel)
        language_layout = QVBoxLayout(language_panel)
        language_layout.setContentsMargins(24, 18, 24, 20)
        language_layout.setSpacing(10)
        language_layout.addWidget(self.field_label(tr("Language")))
        self.language_selector = QComboBox()
        self.language_selector.setObjectName("languageSelector")
        self.language_selector.setMinimumHeight(40)
        for language, name in LANGUAGE_OPTIONS:
            self.language_selector.addItem(tr(name), language)
        selected_language = self.state.get("language", "system")
        language_index = self.language_selector.findData(selected_language)
        if language_index >= 0:
            self.language_selector.setCurrentIndex(language_index)
        language_layout.addWidget(self.language_selector)
        language_note = QLabel(tr("Language changes take effect after restarting Kbroton."))
        language_note.setObjectName("smallMuted")
        language_note.setWordWrap(True)
        language_layout.addWidget(language_note)
        save_language_button = QPushButton(tr("Save language"))
        save_language_button.setObjectName("secondaryButton")
        save_language_button.clicked.connect(self.save_language_setting)
        language_layout.addWidget(save_language_button)
        layout.addWidget(language_panel)

        form.addWidget(self.field_label(tr("DEFAULT PROTON/WINE VERSION")))
        self.default_proton_selector = QComboBox()
        self.default_proton_selector.setObjectName("defaultProtonSelector")
        self.default_proton_selector.setMinimumHeight(40)
        self.default_proton_selector.currentIndexChanged.connect(
            self.select_default_proton
        )
        form.addWidget(self.default_proton_selector)
        form.addWidget(self.field_label(tr("PROTON EXECUTABLE")))
        path_row = QHBoxLayout()
        self.proton_path = QLineEdit()
        self.proton_path.setPlaceholderText(
            tr("Select a Proton installation's proton script")
        )
        path_row.addWidget(self.proton_path, 1)
        browse_proton = QPushButton(tr("Browse"))
        browse_proton.setObjectName("secondaryButton")
        browse_proton.clicked.connect(self.choose_proton)
        path_row.addWidget(browse_proton)
        form.addLayout(path_row)
        self.proton_status = QLabel()
        self.proton_status.setObjectName("smallMuted")
        self.proton_status.setWordWrap(True)
        form.addWidget(self.proton_status)
        self.save_settings_button = QPushButton(tr("Save Proton setting"))
        self.save_settings_button.setObjectName("primaryButton")
        self.save_settings_button.setMinimumHeight(46)
        self.save_settings_button.clicked.connect(self.save_proton_setting)
        form.addWidget(self.save_settings_button)
        layout.addWidget(panel)

        ge_panel = QFrame()
        ge_panel.setObjectName("settingsFormPanel")
        ge_panel.setFrameShape(QFrame.Shape.StyledPanel)
        ge_layout = QVBoxLayout(ge_panel)
        ge_layout.setContentsMargins(24, 18, 24, 20)
        ge_layout.setSpacing(10)
        ge_heading = QLabel(tr("GE-Proton updates"))
        ge_heading.setObjectName("gameTitle")
        ge_layout.addWidget(ge_heading)
        self.automatic_proton_ge_updates = QCheckBox(
            tr("Automatically check for GE-Proton updates on startup")
        )
        self.automatic_proton_ge_updates.setChecked(
            self.state.get("automatic_proton_ge_updates", False)
        )
        self.automatic_proton_ge_updates.toggled.connect(
            self.save_proton_ge_update_preference
        )
        ge_layout.addWidget(self.automatic_proton_ge_updates)
        ge_note = QLabel(
            tr(
                "Automatically check for updates and download the latest GE-Proton "
                "release on startup. This is optional and off by default."
            )
        )
        ge_note.setObjectName("smallMuted")
        ge_note.setWordWrap(True)
        ge_layout.addWidget(ge_note)
        self.proton_update_button = QPushButton(tr("Download / update GE-Proton"))
        self.proton_update_button.setObjectName("secondaryButton")
        self.proton_update_button.clicked.connect(self.start_proton_update)
        ge_layout.addWidget(self.proton_update_button)
        layout.addWidget(ge_panel)

        artwork_panel = QFrame()
        artwork_panel.setObjectName("settingsFormPanel")
        artwork_panel.setFrameShape(QFrame.Shape.StyledPanel)
        artwork_layout = QVBoxLayout(artwork_panel)
        artwork_layout.setContentsMargins(24, 18, 24, 20)
        artwork_layout.setSpacing(10)
        artwork_heading = QLabel(tr("Game artwork"))
        artwork_heading.setObjectName("gameTitle")
        artwork_layout.addWidget(artwork_heading)
        artwork_intro = QLabel(
            tr(
                "Look up app icons with SteamGridDB when an app is added, and retry "
                "when launching if no icon is available. App names are sent to SteamGridDB."
            )
        )
        artwork_intro.setObjectName("smallMuted")
        artwork_intro.setWordWrap(True)
        artwork_layout.addWidget(artwork_intro)
        artwork_layout.addWidget(self.field_label(tr("STEAMGRIDDB API KEY")))
        self.steamgriddb_api_key = QLineEdit(
            self.state.get("steamgriddb_api_key", "")
        )
        self.steamgriddb_api_key.setObjectName("steamGridDBApiKey")
        self.steamgriddb_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.steamgriddb_api_key.setPlaceholderText(
            tr("Optional — required for online icon lookups")
        )
        artwork_layout.addWidget(self.steamgriddb_api_key)
        self.save_artwork_settings_button = QPushButton(tr("Save artwork settings"))
        self.save_artwork_settings_button.setObjectName("secondaryButton")
        self.save_artwork_settings_button.clicked.connect(self.save_artwork_settings)
        artwork_layout.addWidget(self.save_artwork_settings_button)
        layout.addWidget(artwork_panel)
        layout.addStretch(1)
        self.proton_path.textChanged.connect(self.update_proton_status)
        return page

    def save_language_setting(self):
        language = self.language_selector.currentData()
        try:
            resolve_language(language)
        except ValueError as error:
            QMessageBox.warning(self, tr("Invalid language setting"), str(error))
            return
        previous_language = self.state.get("language", "system")
        self.state["language"] = language
        if not self.persist_state():
            self.state["language"] = previous_language
            return
        QMessageBox.information(
            self,
            tr("Language saved"),
            tr("Restart Kbroton to apply the selected language."),
        )

    @staticmethod
    def field_label(text):
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def refresh_proton_options(self):
        selected = self.state.get("proton_path", "")
        if not Path(selected).is_file():
            selected = ""
        detected = self.available_proton_tools()
        if selected and Path(selected) not in detected:
            detected.insert(0, Path(selected))
        self.install_proton.clear()
        for tool in detected:
            self.install_proton.addItem(proton_label(tool), str(tool))
        if not detected:
            self.install_proton.addItem(
                tr("No Proton found — configure it in Settings"), ""
            )
        index = self.install_proton.findData(selected)
        if index >= 0:
            self.install_proton.setCurrentIndex(index)

        if hasattr(self, "proton_path"):
            self.default_proton_selector.blockSignals(True)
            self.default_proton_selector.clear()
            for tool in detected:
                self.default_proton_selector.addItem(proton_label(tool), str(tool))
            if not detected:
                self.default_proton_selector.addItem(tr("No Proton version detected"), "")
            default_index = self.default_proton_selector.findData(selected)
            if default_index >= 0:
                self.default_proton_selector.setCurrentIndex(default_index)
            self.default_proton_selector.blockSignals(False)
            self.proton_path.setText(self.state.get("proton_path", ""))
            self.update_proton_status()

    def select_default_proton(self, index):
        path = self.default_proton_selector.itemData(index)
        if path:
            self.proton_path.setText(path)

    def available_proton_tools(self):
        tools = set(find_proton_tools())
        if PROTON_GE_DIR.is_dir():
            tools.update(PROTON_GE_DIR.glob("*/proton"))
        for path in (
            self.state.get("proton_path", ""),
            *(game.get("proton_path", "") for game in self.state.get("games", [])),
        ):
            if path and Path(path).is_file():
                tools.add(Path(path))
        return sorted((tool for tool in tools if tool.is_file()), key=lambda item: str(item.parent).lower())

    def update_proton_status(self):
        if not hasattr(self, "proton_status"):
            return
        path = self.proton_path.text().strip()
        if not path:
            self.proton_status.setText(
                tr(
                    "Detected {count} Proton installation(s). Kbroton searches Steam's "
                    "common and compatibilitytools.d folders.",
                    count=len(self.available_proton_tools()),
                )
            )
        elif Path(path).is_file():
            self.proton_status.setText(tr("Ready to use {version}.", version=proton_label(path)))
        else:
            self.proton_status.setText(
                tr("That file does not exist. Select a valid Proton executable.")
            )

    def choose_executable(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("Select Windows game executable"),
            str(Path.home()),
            tr("Windows executable (*.exe)"),
        )
        if path:
            self.exe_path.setText(path)
            if not self.game_name.text().strip():
                self.game_name.setText(Path(path).stem)

    def choose_prefix_location(self):
        current = self.prefix_location.text().strip()
        start_directory = Path(current).expanduser()
        if not start_directory.is_dir():
            start_directory = Path.home()
        directory = QFileDialog.getExistingDirectory(
            self,
            tr("Choose folder for game prefixes"),
            str(start_directory),
        )
        if directory:
            self.prefix_location.setText(str(Path(directory).expanduser().resolve()))

    def update_name_from_path(self, path):
        if path and not self.game_name.text().strip():
            self.game_name.setText(Path(path).stem)

    def choose_proton(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Select Proton executable"), str(Path.home()), tr("All files (*)")
        )
        if path:
            self.proton_path.setText(path)

    def save_proton_setting(self):
        path = self.proton_path.text().strip()
        if not path or not Path(path).is_file():
            QMessageBox.warning(
                self, tr("Invalid Proton path"), tr("Choose an existing Proton executable.")
            )
            return
        previous_path = self.state["proton_path"]
        self.state["proton_path"] = str(Path(path).expanduser().resolve())
        if not self.persist_state():
            self.state["proton_path"] = previous_path
            return
        self.refresh_proton_options()
        self.update_install_access()
        QMessageBox.information(
            self,
            tr("Settings saved"),
            tr("New game or app installations will now use {version}.", version=proton_label(path)),
        )

    def save_proton_ge_update_preference(self, enabled):
        previous_value = self.state.get("automatic_proton_ge_updates", False)
        self.state["automatic_proton_ge_updates"] = enabled
        if not self.persist_state():
            self.state["automatic_proton_ge_updates"] = previous_value
            self.automatic_proton_ge_updates.blockSignals(True)
            self.automatic_proton_ge_updates.setChecked(previous_value)
            self.automatic_proton_ge_updates.blockSignals(False)

    def dismiss_artwork_notice_permanently(self):
        previous_value = self.state.get("artwork_notice_dismissed", False)
        self.state["artwork_notice_dismissed"] = True
        if self.persist_state():
            self.artwork_key_notice.hide()
        else:
            self.state["artwork_notice_dismissed"] = previous_value

    def save_artwork_settings(self):
        previous_key = self.state.get("steamgriddb_api_key", "")
        self.state["steamgriddb_api_key"] = self.steamgriddb_api_key.text().strip()
        if not self.persist_state():
            self.state["steamgriddb_api_key"] = previous_key
            self.steamgriddb_api_key.setText(previous_key)
            return
        QMessageBox.information(
            self,
            tr("Artwork settings saved"),
            (
                tr(
                    "The SteamGridDB API key was saved locally. Icons for existing "
                    "apps without artwork will now be looked up."
                )
                if self.state["steamgriddb_api_key"]
                else tr("The SteamGridDB API key was cleared. Online icon lookups are disabled.")
            ),
        )
        for game in self.state.get("games", []):
            self.request_game_icon(game)
        if self.state["steamgriddb_api_key"]:
            self.artwork_key_notice.hide()
        elif not self.state.get("artwork_notice_dismissed", False):
            self.artwork_key_notice.show()

    def add_game(self):
        name = self.game_name.text().strip()
        exe = Path(self.exe_path.text().strip()).expanduser()
        proton = self.install_proton.currentData()
        prefix_location = self.prefix_location.text().strip()
        category = self.app_category.currentData()
        if not name:
            QMessageBox.warning(self, tr("Game name required"), tr("Enter a name for this game."))
            return
        if not exe.is_file() or exe.suffix.lower() != ".exe":
            QMessageBox.warning(
                self, tr("Executable required"), tr("Choose an existing Windows .exe file.")
            )
            return
        if not proton or not Path(proton).is_file():
            QMessageBox.warning(
                self,
                tr("Proton required"),
                tr("Select a Proton installation in Settings before adding a game."),
            )
            return
        if not prefix_location:
            QMessageBox.warning(
                self,
                tr("Prefix location required"),
                tr("Choose a folder for this game's Proton prefix."),
            )
            return

        prefix_root = Path(prefix_location).expanduser()
        try:
            prefix_root.mkdir(parents=True, exist_ok=True)
            if not prefix_root.is_dir() or not os.access(prefix_root, os.W_OK | os.X_OK):
                raise PermissionError(
                    tr("The selected prefix location is not writable:\n{path}", path=prefix_root)
                )
        except OSError as error:
            QMessageBox.critical(self, tr("Cannot use prefix location"), str(error))
            return

        game_id = str(uuid.uuid4())
        prefix_base = prefix_root.resolve() / prefix_directory_name(name)
        used_prefix_paths = {
            str(self.game_compat_data_path(existing).resolve())
            for existing in self.state.get("games", [])
        }
        prefix_path = prefix_base
        if str(prefix_path) in used_prefix_paths or prefix_path.exists() or prefix_path.is_symlink():
            prefix_path = prefix_root.resolve() / (
                f"{prefix_directory_name(name)}{uuid.UUID(game_id).hex}"
            )
        if prefix_path.exists() or prefix_path.is_symlink():
            QMessageBox.critical(
                self,
                tr("Cannot create game prefix"),
                tr("A prefix folder already exists at the generated location:\n{path}", path=prefix_path),
            )
            return
        game = {
            "id": game_id,
            "name": name,
            "executable": str(exe.resolve()),
            "proton_path": str(Path(proton).resolve()),
            "compat_data_path": str(prefix_path),
            "category": category,
        }
        self.state["games"].append(game)
        if not self.persist_state():
            self.state["games"].pop()
            return
        try:
            self.write_game_desktop_entry(game)
        except OSError as error:
            QMessageBox.warning(
                self,
                tr("App added; desktop launcher unavailable"),
                tr(
                    "{name} was added to your library, but Kbroton could not "
                    "create its desktop launcher:\n\n{error}",
                    name=game["name"],
                    error=error,
                ),
            )
        self.game_name.clear()
        self.exe_path.clear()
        self.app_category.setCurrentIndex(self.app_category.findData("Game"))
        self.show_page("installed")
        self.request_game_icon(game)

    def refresh_games(self):
        self.game_status_labels.clear()
        self.game_play_buttons.clear()
        self.game_settings_buttons.clear()
        self.game_proton_labels.clear()
        self.game_winecfg_buttons.clear()
        self.game_icon_labels.clear()
        while self.game_list.count():
            item = self.game_list.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.deleteLater()

        games = self.state.get("games", [])
        count = len(games)
        self.game_count.setText(
            tr("1 app or game installed")
            if count == 1
            else tr("{count} apps and games installed", count=count)
        )
        if not games:
            empty_panel = QFrame()
            empty_panel.setObjectName("emptyGamePanel")
            empty_panel.setFrameShape(QFrame.Shape.Panel)
            empty_panel.setFrameShadow(QFrame.Shadow.Raised)
            empty_layout = QVBoxLayout(empty_panel)
            empty_layout.setContentsMargins(24, 22, 24, 22)
            empty_text = QLabel(tr("No game or application is installed yet"))
            empty_text.setObjectName("smallMuted")
            empty_text.setFont(self.font())
            empty_font = empty_text.font()
            empty_font.setWeight(QFont.Weight.DemiBold)
            empty_text.setFont(empty_font)
            empty_text.setWordWrap(True)
            empty_layout.addWidget(empty_text)
            self.game_list.addWidget(empty_panel)
            self.game_list.addStretch(1)
            return

        for game in games:
            card = QFrame()
            card.setObjectName("gameCard")
            card.setFrameShape(QFrame.Shape.Panel)
            card.setFrameShadow(QFrame.Shadow.Raised)
            card.setLineWidth(1)
            row = QHBoxLayout(card)
            row.setContentsMargins(16, 12, 16, 12)
            row.setSpacing(14)
            icon_frame = QFrame()
            icon_frame.setObjectName("gameIconFrame")
            icon_frame.setFrameShape(QFrame.Shape.StyledPanel)
            icon_frame.setFixedSize(76, 76)
            icon_layout = QVBoxLayout(icon_frame)
            icon_layout.setContentsMargins(3, 3, 3, 3)
            icon_label = QLabel(game.get("name", "?")[:1].upper())
            icon_label.setObjectName("gameIcon")
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_layout.addWidget(icon_label)
            self.game_icon_labels[game["id"]] = icon_label
            row.addWidget(icon_frame)
            self.load_game_icon(game, icon_label)

            info = QVBoxLayout()
            info.setSpacing(5)
            title = QLabel(game.get("name", tr("Untitled game")))
            title.setObjectName("gameTitle")
            title_font = title.font()
            title_font.setWeight(QFont.Weight.DemiBold)
            title.setFont(title_font)
            path = QLabel(game.get("executable", ""))
            path.setObjectName("smallMuted")
            path.setWordWrap(True)
            path.setTextInteractionFlags(path.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse)
            info.addWidget(title)
            info.addWidget(path)
            process_status = QLabel(tr("Not running"))
            process_status.setObjectName("gameProcessStatus")
            process_status.setProperty("statusState", "idle")
            self.game_status_labels[game["id"]] = process_status
            info.addWidget(process_status)
            row.addLayout(info, 1)

            separator = QFrame()
            separator.setFrameShape(QFrame.Shape.VLine)
            separator.setFrameShadow(QFrame.Shadow.Sunken)
            row.addWidget(separator)

            version_panel = QFrame()
            version_panel.setObjectName("versionPanel")
            version_panel.setFrameShape(QFrame.Shape.Panel)
            version_panel.setFrameShadow(QFrame.Shadow.Sunken)
            version_layout = QVBoxLayout(version_panel)
            version_layout.setContentsMargins(9, 5, 9, 5)
            version_layout.setSpacing(2)
            version_title = QLabel(tr("PROTON"))
            version_title.setObjectName("smallMuted")
            version_layout.addWidget(version_title)
            proton = QLabel(proton_label(game.get("proton_path", "")))
            proton.setObjectName("smallMuted")
            proton.setWordWrap(False)
            proton.setToolTip(game.get("proton_path", ""))
            self.game_proton_labels[game["id"]] = proton
            version_layout.addWidget(proton)
            row.addWidget(version_panel)
            play = QPushButton(tr("Play"))
            play.setObjectName("playButton")
            play.setMinimumWidth(88)
            self.game_play_buttons[game["id"]] = play
            play.clicked.connect(lambda checked=False, item=game: self.launch_game(item))
            row.addWidget(play)
            force_stop = QPushButton(tr("Force stop"))
            force_stop.setObjectName("forceStopButton")
            force_stop.setToolTip(tr("Stop this game and its tracked Wine processes."))
            force_stop.clicked.connect(
                lambda checked=False, item=game: self.force_stop_game(item)
            )
            force_stop.hide()
            self.game_force_stop_buttons[game["id"]] = force_stop
            row.addWidget(force_stop)
            settings = QPushButton(tr("Settings"))
            settings.setObjectName("secondaryButton")
            self.game_settings_buttons[game["id"]] = settings
            settings.clicked.connect(lambda checked=False, item=game: self.open_game_settings(item))
            row.addWidget(settings)
            self.game_list.addWidget(card)
            self.update_game_play_button(game["id"], False)
        self.game_list.addStretch(1)
        if self.selected_log_game not in {game["id"] for game in games}:
            self.selected_log_game = games[0]["id"] if games else None
        self.update_log_view()
        self.update_game_process_status()

    def game_icon_path(self, game):
        icon_id = uuid.UUID(game["id"]).hex
        revision = game.get("custom_icon_revision")
        filename = f"{icon_id}-{revision}.png" if revision else f"{icon_id}.png"
        return DATA_DIR / "icons" / filename

    def desktop_entry_path(self, game):
        game_id = uuid.UUID(game["id"]).hex
        applications_dir = DATA_HOME / "applications"
        return applications_dir / f"kbroton-{game_id}.desktop"

    @staticmethod
    def desktop_entry_string(value):
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )

    @staticmethod
    def desktop_exec_argument(value):
        escaped = (
            str(value)
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("`", "\\`")
            .replace("$", "\\$")
            .replace("%", "%%")
        )
        return f'"{escaped}"'

    def write_game_desktop_entry(self, game):
        desktop_path = self.desktop_entry_path(game)
        desktop_path.parent.mkdir(parents=True, exist_ok=True)
        if desktop_path.parent.is_symlink():
            raise OSError(f"Refusing to write through a symlink: {desktop_path.parent}")
        if desktop_path.is_symlink():
            raise OSError(f"Refusing to replace a symlink: {desktop_path}")

        executable = Path(game["executable"]).expanduser().resolve()
        command = " ".join(
            self.desktop_exec_argument(argument)
            for argument in (
                sys.executable,
                Path(__file__).resolve(),
                "--launch-game",
                game["id"],
            )
        )
        entry = [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={self.desktop_entry_string(game['name'])}",
            f"Exec={command}",
            f"Path={self.desktop_entry_string(str(executable.parent))}",
            "Terminal=false",
            f"Categories={game.get('category', 'Game')};",
            "StartupNotify=true",
            f"X-Kbroton-GameId={game['id']}",
        ]
        if self.has_cached_game_icon(game):
            entry.append(
                f"Icon={self.desktop_entry_string(str(self.game_icon_path(game).resolve()))}"
            )
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=desktop_path.parent,
                prefix=".kbroton-",
                suffix=".desktop.tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write("\n".join(entry) + "\n")
            os.chmod(temporary_path, 0o644)
            temporary_path.replace(desktop_path)
        except OSError:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
        return desktop_path

    def remove_game_desktop_entry(self, game):
        desktop_path = self.desktop_entry_path(game)
        if desktop_path.parent.is_symlink():
            raise OSError(f"Refusing to remove through a symlink: {desktop_path.parent}")
        if desktop_path.is_symlink():
            raise OSError(f"Refusing to remove a symlink: {desktop_path}")
        if desktop_path.exists():
            if not desktop_path.is_file():
                raise OSError(f"Desktop launcher is not a regular file: {desktop_path}")
            desktop_path.unlink()

    def load_game_icon(self, game, label):
        icon_path = self.game_icon_path(game)
        if not self.has_cached_game_icon(game):
            label.setToolTip(
                tr("Add a SteamGridDB API key in Settings to look up an icon.")
                if not self.state.get("steamgriddb_api_key")
                else tr("No icon cached yet. Kbroton will look it up online.")
            )
            return
        pixmap = QPixmap(str(icon_path))
        if pixmap.isNull():
            label.setToolTip(tr("Could not read cached app icon: {path}", path=icon_path))
            return
        self.display_game_icon(game, label, pixmap)
        label.setToolTip(
            tr("Custom app icon.")
            if game.get("custom_icon")
            else tr("App icon from SteamGridDB.")
        )

    @staticmethod
    def display_game_icon(game, label, pixmap):
        if pixmap.isNull():
            label.setPixmap(QPixmap())
            label.setText(game.get("name", "?")[:1].upper())
            return
        label.setText("")
        label.setPixmap(
            pixmap.scaled(
                68,
                68,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def has_cached_game_icon(self, game):
        icon_path = self.game_icon_path(game)
        return icon_path.is_file() and not QPixmap(str(icon_path)).isNull()

    def request_game_icon(self, game):
        game_id = game["id"]
        if (
            game.get("custom_icon")
            or self.has_cached_game_icon(game)
            or game_id in self.icon_lookup_workers
        ):
            return
        api_key = self.state.get("steamgriddb_api_key", "").strip()
        if not api_key:
            label = self.game_icon_labels.get(game_id)
            if label:
                label.setToolTip(
                    tr("Add a SteamGridDB API key in Settings to look up an icon.")
                )
            return
        label = self.game_icon_labels.get(game_id)
        if label:
            label.setToolTip(tr("Searching SteamGridDB for an app icon…"))

        worker = SteamGridDBIconLookup(game_id, game.get("name", ""), api_key, self)
        self.icon_lookup_workers[game_id] = worker
        worker.icon_found.connect(self.on_game_icon_found)
        worker.icon_not_found.connect(self.on_game_icon_not_found)
        worker.failed.connect(self.on_game_icon_lookup_failed)
        worker.finished.connect(
            lambda key=game_id, task=worker: self.finish_icon_lookup(key, task)
        )
        worker.start()

    def on_game_icon_found(self, game_id, image_data):
        game = next(
            (item for item in self.state.get("games", []) if item.get("id") == game_id),
            None,
        )
        if game is None:
            return
        if game.get("custom_icon"):
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(image_data):
            self.on_game_icon_lookup_failed(
                game_id, tr("SteamGridDB returned an invalid image.")
            )
            return
        icon_path = self.game_icon_path(game)
        try:
            icon_path.parent.mkdir(parents=True, exist_ok=True)
            if icon_path.parent.is_symlink() or icon_path.is_symlink():
                raise OSError(f"Refusing to write through a symlink: {icon_path}")
            temporary_path = icon_path.with_suffix(".tmp")
            if not pixmap.save(str(temporary_path), "PNG"):
                raise OSError(tr("Qt could not encode the downloaded icon as PNG."))
            temporary_path.replace(icon_path)
        except OSError as error:
            self.on_game_icon_lookup_failed(
                game_id, tr("Could not cache the icon: {error}", error=error)
            )
            return
        try:
            self.write_game_desktop_entry(game)
        except OSError as error:
            self.on_game_icon_lookup_failed(
                game_id,
                tr(
                    "Icon was downloaded, but the desktop launcher could not be updated: {error}",
                    error=error,
                ),
            )

        label = self.game_icon_labels.get(game_id)
        if label:
            self.load_game_icon(game, label)
        self.append_game_log(
            game_id,
            f"[{APP_NAME}] Downloaded an app icon from SteamGridDB.\n",
        )

    def on_game_icon_not_found(self, game_id):
        game = next(
            (item for item in self.state.get("games", []) if item.get("id") == game_id),
            None,
        )
        name = game.get("name", tr("this app")) if game else tr("this app")
        label = self.game_icon_labels.get(game_id)
        if label:
            label.setToolTip(
                tr(
                    "No SteamGridDB icon matched “{name}”. Kbroton will retry when this app launches.",
                    name=name,
                )
            )
        if game:
            self.append_game_log(
                game_id,
                f"[{APP_NAME}] No SteamGridDB icon matched “{name}”; will retry on next launch.\n",
            )

    def on_game_icon_lookup_failed(self, game_id, message):
        label = self.game_icon_labels.get(game_id)
        if label:
            label.setToolTip(tr("SteamGridDB icon lookup failed: {message}", message=message))
        if any(game.get("id") == game_id for game in self.state.get("games", [])):
            self.append_game_log(
                game_id,
                f"[{APP_NAME} warning] SteamGridDB icon lookup failed: {message}\n",
            )

    def finish_icon_lookup(self, game_id, worker):
        if self.icon_lookup_workers.get(game_id) is worker:
            self.icon_lookup_workers.pop(game_id, None)
        worker.deleteLater()

    def open_game_settings(self, game):
        self.update_game_process_status()
        if self.is_game_running(game["id"]):
            self.set_game_launch_status(
                tr(
                    "Close {game} before opening its settings.",
                    game=game.get("name", tr("this game")),
                )
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(tr("{game} settings", game=game.get("name", tr("Game"))))
        dialog.setModal(True)
        dialog.setMinimumWidth(400)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        version_label = QLabel(tr("Proton version"))
        layout.addWidget(version_label)

        proton = QComboBox()
        proton.setObjectName("gameProtonSelector")
        proton.setMinimumHeight(40)
        for tool in self.available_proton_tools():
            proton.addItem(proton_label(tool), str(tool))
        selected_index = proton.findData(game.get("proton_path", ""))
        if selected_index < 0:
            proton.addItem(proton_label(game.get("proton_path", "")), game.get("proton_path", ""))
            selected_index = proton.count() - 1
        proton.setCurrentIndex(selected_index)
        proton.currentIndexChanged.connect(
            lambda index, item=game, selector=proton: self.change_game_proton(
                item, selector.itemData(index), selector
            )
        )
        layout.addWidget(proton)

        icon_label = QLabel(tr("Current icon"))
        layout.addWidget(icon_label)
        icon_preview = QLabel(game.get("name", "?")[:1].upper())
        icon_preview.setObjectName("gameIcon")
        icon_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_preview.setFixedSize(88, 88)
        current_icon = QPixmap(str(self.game_icon_path(game)))
        if not self.has_cached_game_icon(game):
            current_icon = QPixmap()
        self.display_game_icon(game, icon_preview, current_icon)
        layout.addWidget(icon_preview, 0, Qt.AlignmentFlag.AlignHCenter)

        icon_actions = QHBoxLayout()
        choose_icon = QPushButton(tr("Select custom icon"))
        choose_icon.setObjectName("secondaryButton")
        save_icon = QPushButton(tr("Save icon"))
        save_icon.setObjectName("secondaryButton")
        save_icon.setEnabled(False)
        selected_icon_path = [""]

        def select_custom_icon():
            icon_directory = self.game_icon_path(game).parent
            try:
                icon_directory.mkdir(parents=True, exist_ok=True)
                if icon_directory.is_symlink():
                    raise OSError(f"Refusing to open a symlink: {icon_directory}")
            except OSError as error:
                QMessageBox.warning(
                    dialog,
                    tr("Could not open icon folder"),
                    str(error),
                )
                return
            selected_path, _ = QFileDialog.getOpenFileName(
                dialog,
                tr("Choose an image for the app icon"),
                str(icon_directory),
                tr("Image files (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.ico *.svg);;All files (*)"),
            )
            if not selected_path:
                return
            selected_pixmap = QPixmap(selected_path)
            if selected_pixmap.isNull():
                QMessageBox.warning(
                    dialog,
                    tr("Invalid app icon"),
                    tr("Select a readable image file."),
                )
                return
            selected_icon_path[0] = selected_path
            self.display_game_icon(game, icon_preview, selected_pixmap)
            save_icon.setEnabled(True)

        choose_icon.clicked.connect(select_custom_icon)
        save_icon.clicked.connect(
            lambda: self.save_custom_game_icon(
                dialog, game, selected_icon_path[0]
            )
        )
        icon_actions.addWidget(choose_icon)
        icon_actions.addWidget(save_icon)
        layout.addLayout(icon_actions)

        change_executable = QPushButton(tr("Change app/game executable"))
        change_executable.setObjectName("secondaryButton")
        change_executable.clicked.connect(
            lambda: self.choose_game_executable(dialog, game)
        )
        layout.addWidget(change_executable)

        winecfg = QPushButton(tr("Wine config"))
        winecfg.setObjectName("secondaryButton")
        self.game_winecfg_buttons[game["id"]] = winecfg
        session = self.game_sessions.get(game["id"], {})
        prefix_utility_running = any(
            session.get("active_by_kind", {}).get(kind)
            or session.get("pids_by_kind", {}).get(kind)
            for kind in ("winecfg", "run-exe", "winetricks")
        )
        winecfg_running = bool(
            session.get("active_by_kind", {}).get("winecfg")
            or session.get("pids_by_kind", {}).get("winecfg")
        )
        winecfg.setEnabled(not winecfg_running and not prefix_utility_running)
        winecfg.setToolTip(
            tr("Wine Configuration is already open for this game.")
            if winecfg_running
            else tr("A prefix utility is already running for this app.")
            if prefix_utility_running
            else tr("Open Wine Configuration for this game's prefix.")
        )
        dialog.finished.connect(
            lambda result, game_id=game["id"], button=winecfg:
            self.clear_game_winecfg_button(game_id, button)
        )
        winecfg.clicked.connect(lambda: self.open_wine_config_from_settings(dialog, game))
        layout.addWidget(winecfg)

        run_executable = QPushButton(tr("Run a .exe file inside the prefix"))
        run_executable.setObjectName("secondaryButton")
        run_executable.setEnabled(not prefix_utility_running)
        run_executable.setToolTip(
            tr("A prefix utility is already running for this app.")
            if prefix_utility_running
            else tr("Run a Windows executable in this app's Proton prefix.")
        )
        run_executable.clicked.connect(
            lambda: self.run_executable_from_settings(dialog, game)
        )
        layout.addWidget(run_executable)

        winetricks_path = shutil.which("winetricks")
        winetricks = QPushButton(tr("Run Winetricks"))
        winetricks.setObjectName("secondaryButton")
        winetricks.setEnabled(bool(winetricks_path) and not prefix_utility_running)
        winetricks.setToolTip(
            tr("Install Winetricks system-wide to enable this button.")
            if not winetricks_path
            else tr("A prefix utility is already running for this app.")
            if prefix_utility_running
            else tr("Run Winetricks in this app's Proton prefix.")
        )
        winetricks.clicked.connect(
            lambda: self.run_winetricks_from_settings(dialog, game)
        )
        layout.addWidget(winetricks)

        remove = QPushButton(tr("Remove game"))
        remove.setObjectName("removeGameButton")
        remove.clicked.connect(lambda: self.remove_game_from_settings(dialog, game))
        layout.addWidget(remove)
        dialog.exec()

    def save_custom_game_icon(self, dialog, game, selected_icon_path):
        if not selected_icon_path:
            return
        pixmap = QPixmap(selected_icon_path)
        if pixmap.isNull():
            QMessageBox.warning(
                dialog,
                tr("Invalid app icon"),
                tr("Select a readable image file."),
            )
            return

        previous_custom_icon = game.get("custom_icon")
        previous_icon_revision = game.get("custom_icon_revision")
        game["custom_icon"] = True
        game["custom_icon_revision"] = uuid.uuid4().hex
        icon_path = self.game_icon_path(game)
        temporary_path = None
        icon_installed = False
        try:
            icon_path.parent.mkdir(parents=True, exist_ok=True)
            if icon_path.parent.is_symlink() or icon_path.is_symlink():
                raise OSError(f"Refusing to write through a symlink: {icon_path}")
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=icon_path.parent,
                prefix=".kbroton-icon-",
                suffix=".png.tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
            if not pixmap.save(str(temporary_path), "PNG"):
                raise OSError(tr("Qt could not encode the selected app icon as PNG."))
            temporary_path.replace(icon_path)
            temporary_path = None
            icon_installed = True
            if not self.persist_state():
                self.restore_custom_icon(game, previous_custom_icon, previous_icon_revision)
                icon_path.unlink(missing_ok=True)
                return
        except OSError as error:
            self.restore_custom_icon(game, previous_custom_icon, previous_icon_revision)
            if icon_installed:
                icon_path.unlink(missing_ok=True)
            QMessageBox.critical(
                dialog,
                tr("Could not save app icon"),
                str(error),
            )
            return
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as error:
                    QMessageBox.warning(
                        dialog,
                        tr("Could not save app icon"),
                        str(error),
                    )

        try:
            self.write_game_desktop_entry(game)
        except OSError as error:
            QMessageBox.warning(
                dialog,
                tr("Could not create app menu entry"),
                tr(
                    "App icon saved, but the desktop launcher could not be updated: {error}",
                    error=error,
                ),
            )
        label = self.game_icon_labels.get(game["id"])
        if label:
            self.load_game_icon(game, label)
        dialog.accept()

    @staticmethod
    def restore_custom_icon(game, previous_custom_icon, previous_icon_revision):
        if previous_custom_icon is None:
            game.pop("custom_icon", None)
        else:
            game["custom_icon"] = previous_custom_icon
        if previous_icon_revision is None:
            game.pop("custom_icon_revision", None)
        else:
            game["custom_icon_revision"] = previous_icon_revision

    def game_compat_data_path(self, game):
        prefix_id = uuid.UUID(game["id"]).hex[:16]
        compat_data_path = game.get("compat_data_path")
        if compat_data_path:
            path = Path(compat_data_path)
            if not path.is_absolute() or not path.name.isalnum():
                raise ValueError("The game's compatibility-data path is invalid.")
            return path
        return DATA_DIR / "compatdata" / prefix_id

    def choose_game_executable(self, dialog, game):
        compat_data = self.game_compat_data_path(game)
        prefix_drive_c = compat_data / "pfx" / "drive_c"
        start_directory = next(
            (
                path for path in (prefix_drive_c, compat_data, Path(game["executable"]).parent)
                if path.is_dir()
            ),
            Path.home(),
        )
        executable, _ = QFileDialog.getOpenFileName(
            dialog,
            tr("Select game executable from its Proton prefix"),
            str(start_directory),
            tr("Windows executable (*.exe)"),
        )
        if not executable:
            return
        selected_path = Path(executable).expanduser()
        if not selected_path.is_file() or selected_path.suffix.lower() != ".exe":
            QMessageBox.warning(
                dialog,
                tr("Invalid executable"),
                tr("Select an existing Windows .exe file."),
            )
            return

        previous_path = game["executable"]
        game["executable"] = str(selected_path.resolve())
        if not self.persist_state():
            game["executable"] = previous_path
            return
        self.refresh_games()
        self.append_game_log(
            game["id"],
            f"[{APP_NAME}] Game executable changed to {game['executable']}.\n",
        )

    def run_executable_from_settings(self, dialog, game):
        prefix = self.game_compat_data_path(game)
        start_directory = next(
            (
                path for path in (prefix / "pfx" / "drive_c", prefix)
                if path.is_dir()
            ),
            Path.home(),
        )
        executable, _ = QFileDialog.getOpenFileName(
            dialog,
            tr("Select an .exe file to run inside the prefix"),
            str(start_directory),
            tr("Windows executable (*.exe)"),
        )
        if not executable:
            return
        selected_executable = Path(executable).expanduser()
        if not selected_executable.is_file() or selected_executable.suffix.lower() != ".exe":
            QMessageBox.warning(
                dialog,
                tr("Invalid executable"),
                tr("Select an existing Windows .exe file."),
            )
            return
        selected_executable = selected_executable.resolve()
        dialog.accept()
        self.launch_proton_command(
            game,
            "run-exe",
            ["run", str(selected_executable)],
            executable=selected_executable,
        )

    def run_winetricks_from_settings(self, dialog, game):
        winetricks_path = shutil.which("winetricks")
        if not winetricks_path:
            QMessageBox.warning(
                dialog,
                tr("Winetricks is not installed"),
                tr("Install Winetricks system-wide to enable this button."),
            )
            return

        proton_root = Path(game["proton_path"]).expanduser().resolve().parent
        wine_binary = next(
            (
                path for path in (
                    proton_root / "files/bin/wine",
                    proton_root / "dist/bin/wine",
                )
                if path.is_file()
            ),
            None,
        )
        if wine_binary is None:
            QMessageBox.critical(
                dialog,
                tr("Proton Wine executable not found"),
                tr("The selected Proton version does not contain a Wine executable."),
            )
            return

        wineserver = wine_binary.with_name("wineserver")
        dialog.accept()
        self.launch_proton_command(
            game,
            "winetricks",
            [],
            program=Path(winetricks_path),
            wine_binary=wine_binary,
            wineserver=wineserver if wineserver.is_file() else None,
        )

    def open_wine_config_from_settings(self, dialog, game):
        dialog.accept()
        self.launch_winecfg(game)

    def remove_game_from_settings(self, dialog, game):
        if self.remove_game(game, dialog):
            dialog.accept()

    def change_game_proton(self, game, proton_path, selector=None):
        if not proton_path or not Path(proton_path).is_file():
            return
        self.update_game_process_status()
        if self.is_game_running(game["id"]):
            QMessageBox.warning(
                self,
                tr("Game is running"),
                tr(
                    "Close {game} before changing its Proton version.",
                    game=game.get("name", tr("this game")),
                ),
            )
            return
        previous_path = game["proton_path"]
        selected_path = str(Path(proton_path).resolve())
        if previous_path == selected_path:
            return
        game["proton_path"] = selected_path
        if not self.persist_state():
            game["proton_path"] = previous_path
            label = self.game_proton_labels.get(game["id"])
            if label:
                label.setText(proton_label(previous_path))
                label.setToolTip(previous_path)
            if selector:
                previous_index = selector.findData(previous_path)
                if previous_index >= 0:
                    selector.blockSignals(True)
                    selector.setCurrentIndex(previous_index)
                    selector.blockSignals(False)
            return
        label = self.game_proton_labels.get(game["id"])
        if label:
            label.setText(proton_label(selected_path))
            label.setToolTip(selected_path)
        self.append_game_log(
            game["id"],
            f"[{APP_NAME}] Proton version changed to {proton_label(selected_path)}. "
            "Existing prefixes are kept and will be updated by Proton on next launch.\n",
        )

    def launch_winecfg(self, game):
        self.launch_proton_command(game, "winecfg", ["run", "winecfg"])

    def launch_game(self, game):
        if not self.has_cached_game_icon(game):
            self.request_game_icon(game)
        self.launch_proton_command(game, "game", ["run", str(Path(game["executable"]).expanduser().resolve())])

    def launch_game_by_id(self, game_id):
        game = next(
            (item for item in self.state.get("games", []) if item["id"] == game_id),
            None,
        )
        if game is None:
            QMessageBox.critical(
                self,
                tr("Game not found"),
                tr("This desktop launcher refers to a game that is no longer in the library."),
            )
            return
        self.launch_game(game)

    def launch_proton_command(
        self,
        game,
        process_kind,
        arguments,
        *,
        executable=None,
        program=None,
        wine_binary=None,
        wineserver=None,
    ):
        proton = Path(game.get("proton_path", ""))
        exe = Path(executable or game.get("executable", ""))
        game_id = game.get("id", "")
        session = self.game_sessions.get(game_id, {})
        if process_kind == "winecfg" and (
            session.get("active_by_kind", {}).get("winecfg")
            or session.get("pids_by_kind", {}).get("winecfg")
        ):
            self.set_game_launch_status(
                tr(
                    "Wine Configuration is already open for {game}.",
                    game=game.get("name", tr("this game")),
                )
            )
            return
        if process_kind == "winecfg" and any(
            session.get("active_by_kind", {}).get(kind)
            or session.get("pids_by_kind", {}).get(kind)
            for kind in ("run-exe", "winetricks")
        ):
            self.set_game_launch_status(
                tr("A prefix utility is already running for this app.")
            )
            return
        if process_kind == "game" and (
            game_id in self.game_processes
            or session.get("active")
            or session.get("pids_by_kind", {}).get("game")
            or any(
                session.get("pids_by_kind", {}).get(kind)
                for kind in ("winecfg", "run-exe", "winetricks")
            )
        ):
            return
        if process_kind in ("run-exe", "winetricks") and any(
            session.get("active_by_kind", {}).get(kind)
            or session.get("pids_by_kind", {}).get(kind)
            for kind in ("winecfg", "run-exe", "winetricks")
        ):
            self.set_game_launch_status(
                tr("A prefix utility is already running for this app.")
            )
            return
        if not proton.is_file():
            QMessageBox.critical(
                self,
                tr("Proton not found"),
                tr(
                    "Could not find Proton at:\n{path}\n\nOpen Settings and select a valid Proton executable.",
                    path=proton,
                ),
            )
            return
        if process_kind not in ("run-exe", "winetricks") and not exe.is_file():
            QMessageBox.critical(
                self,
                tr("Game not found"),
                tr(
                    "Could not find the game executable:\n{path}\n\nEdit this game's library entry and choose its current .exe file.",
                    path=exe,
                ),
            )
            return

        try:
            prefix_id = uuid.UUID(game_id).hex[:16]
        except (ValueError, AttributeError, TypeError):
            QMessageBox.critical(
                self, tr("Invalid game entry"), tr("This game's ID is invalid.")
            )
            return
        if not prefix_id:
            QMessageBox.critical(
                self, tr("Invalid game entry"), tr("This game's ID is invalid.")
            )
            return

        prefix = self.game_compat_data_path(game)
        try:
            prefix.mkdir(parents=True, exist_ok=True)
            if not os.access(prefix, os.W_OK | os.X_OK):
                raise PermissionError(
                    tr("The compatibility data directory is not writable:\n{path}", path=prefix)
                )
        except OSError as error:
            QMessageBox.critical(self, tr("Cannot create game prefix"), str(error))
            return

        env = QProcessEnvironment.systemEnvironment()
        env.insert("STEAM_COMPAT_DATA_PATH", str(prefix))
        env.insert("STEAM_COMPAT_CLIENT_INSTALL_PATH", str(steam_root_for(proton)))
        env.insert("STEAM_COMPAT_APP_ID", str(int(prefix_id[:8], 16) % 2_000_000_000 or 1))
        env.insert("ASTER_GAME_ID", game_id)
        env.insert("ASTER_PROCESS_KIND", process_kind)
        if process_kind == "winetricks":
            prefix_wine = wine_binary or proton.parent / "files/bin/wine"
            env.insert("WINEPREFIX", str(prefix / "pfx"))
            env.insert("WINE", str(prefix_wine))
            if wineserver:
                env.insert("WINESERVER", str(wineserver))

        process = QProcess(self)
        process.setObjectName(f"{process_kind}-{game_id}")
        process.setProcessEnvironment(env)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        working_directory = exe.resolve().parent if exe.is_file() else prefix
        process.setWorkingDirectory(str(working_directory))
        process.setProgram(str(Path(program).resolve()) if program else str(proton.resolve()))
        process.setArguments(arguments)
        process.setProperty(
            "launchedExecutable",
            str(program) if process_kind == "winetricks" else str(exe),
        )
        process.started.connect(
            lambda item=game, kind=process_kind:
            self.on_game_process_started(item, kind, prefix)
        )
        process.readyReadStandardOutput.connect(
            lambda proc=process, item=game, kind=process_kind: self.read_game_process_output(proc, item, kind)
        )
        process.errorOccurred.connect(
            lambda error, proc=process, item=game, kind=process_kind:
            self.on_game_process_error(proc, item, error, kind)
        )
        process.finished.connect(
            lambda exit_code, exit_status, proc=process, item=game, kind=process_kind:
            self.on_game_process_finished(proc, item, exit_code, exit_status, kind)
        )
        session = self.game_sessions.setdefault(
            game_id,
            {"pids": set(), "pids_by_kind": {}, "active": 0, "active_by_kind": {}},
        )
        session["active"] += 1
        active_by_kind = session.setdefault("active_by_kind", {})
        active_by_kind[process_kind] = active_by_kind.get(process_kind, 0) + 1
        session.setdefault("processes", []).append(process)
        if process_kind == "game":
            self.game_processes[game_id] = process
        elif process_kind == "winecfg":
            winecfg_button = self.game_winecfg_buttons.get(game_id)
            if winecfg_button:
                winecfg_button.setEnabled(False)
                winecfg_button.setToolTip(
                    tr("Wine Configuration is already open for this game.")
                )
        self.select_game_logs(game)
        self.set_game_launch_status(
            tr(
                "Starting {game} with {version}…",
                game=game.get("name", tr("game")),
                version=proton_label(game["proton_path"]),
            )
        )
        process.start()
        self.update_game_play_button(
            game_id,
            process_kind == "game",
            prefix_busy=process_kind != "game",
        )

    def on_game_process_started(self, game, process_kind, prefix):
        action = {
            "winecfg": "Wine Configuration",
            "run-exe": "Windows executable",
            "winetricks": "Winetricks",
        }.get(process_kind, "game")
        self.set_game_launch_status(
            tr(
                "Starting {action} for {game} with {version}. Proton is using {prefix}.",
                action=tr(action),
                game=game.get("name", tr("game")),
                version=proton_label(game["proton_path"]),
                prefix=prefix / "pfx",
            )
        )
        self.append_game_log(
            game["id"],
            f"[{APP_NAME}] Started {action} using {game['proton_path']}\n"
            f"[{APP_NAME}] Compatibility data: {prefix}\n",
        )

    def read_game_process_output(self, process, game, process_kind):
        output = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if output:
            prefix = f"[{process_kind}] " if process_kind != "game" else ""
            self.append_game_log(game["id"], prefix + output)

    def on_game_process_error(self, process, game, error, process_kind):
        if error != QProcess.ProcessError.FailedToStart:
            return
        game_id = game["id"]
        if process_kind == "game":
            self.game_processes.pop(game_id, None)
        self.finish_game_process(game_id, process_kind)
        if process_kind == "game":
            details = tr(
                "{app} could not start Proton for {game}.\n\n"
                "Proton: {proton}\nExecutable: {executable}\nError: {error}",
                app=APP_NAME,
                game=game.get("name", tr("this game")),
                proton=process.program(),
                executable=process.property("launchedExecutable"),
                error=process.errorString(),
            )
            action = tr("game")
        else:
            action = tr(
                "Wine Configuration"
                if process_kind == "winecfg"
                else "Windows executable"
                if process_kind == "run-exe"
                else "Winetricks"
            )
            details = tr(
                "{app} could not start {action} for {game}.\n\n"
                "Program: {program}\nError: {error}",
                app=APP_NAME,
                action=action,
                game=game.get("name", tr("this game")),
                program=process.program(),
                error=process.errorString(),
            )
        self.set_game_launch_status(
            tr(
                "Could not start {game}: {error}",
                game=game.get("name", tr("game")),
                error=process.errorString(),
            )
            if process_kind == "game"
            else tr(
                "Could not start {action} for {game}: {error}",
                action=action,
                game=game.get("name", tr("game")),
                error=process.errorString(),
            )
        )
        self.append_game_log(game_id, f"[{APP_NAME} error] {process.errorString()}\n")
        QMessageBox.critical(
            self,
            tr("Could not start game")
            if process_kind == "game"
            else tr("Prefix utility failed"),
            details,
        )

    def on_game_process_finished(self, process, game, exit_code, exit_status, process_kind):
        game_id = game["id"]
        output = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
        if output:
            prefix = f"[{process_kind}] " if process_kind != "game" else ""
            self.append_game_log(game_id, prefix + output + "\n")
        if process_kind == "game":
            self.game_processes.pop(game_id, None)
        self.finish_game_process(game_id, process_kind)
        if process_kind == "game" and game_id in self.force_stop_pending:
            self.append_game_log(
                game_id,
                f"[{APP_NAME}] {game.get('name', 'Game')} was force stopped.\n",
            )
        elif exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0:
            if process_kind == "winecfg":
                self.set_game_launch_status(
                    tr("Wine Configuration closed for {game}.", game=game.get("name", tr("game")))
                )
                self.append_game_log(game_id, f"[{APP_NAME}] Wine Configuration closed.\n")
            elif process_kind in ("run-exe", "winetricks"):
                action = tr(
                    "Windows executable"
                    if process_kind == "run-exe"
                    else "Winetricks"
                )
                self.set_game_launch_status(
                    tr("{action} closed for {game}.", action=action, game=game.get("name", tr("game")))
                )
                self.append_game_log(
                    game_id,
                    f"[{APP_NAME}] {action} closed.\n",
                )
            else:
                self.append_game_log(game_id, f"[{APP_NAME}] {game.get('name', 'Game')} exited.\n")
        else:
            reason = (
                tr("Proton exited with code {code}.", code=exit_code)
                if process_kind == "game" and exit_status == QProcess.ExitStatus.NormalExit
                else tr("Proton crashed while launching the game.")
                if process_kind == "game"
                else tr("Process exited with code {code}.", code=exit_code)
                if exit_status == QProcess.ExitStatus.NormalExit
                else tr("The prefix utility crashed.")
            )
            if not output:
                output = self.log_buffers.get(game_id, "").strip()
            if process_kind == "game":
                details = tr(
                    "{game} did not start successfully.\n\n{reason}\n\n"
                    "Proton: {proton}\nExecutable: {executable}\n"
                    "Compatibility data: {compat_data}",
                    game=game.get("name", tr("this game")),
                    reason=reason,
                    proton=process.program(),
                    executable=process.property("launchedExecutable"),
                    compat_data=process.processEnvironment().value("STEAM_COMPAT_DATA_PATH"),
                )
                title = tr("Game launch failed")
                status = tr(
                    "Could not run {game}: {reason}",
                    game=game.get("name", tr("game")),
                    reason=reason,
                )
            else:
                action = tr(
                    "Wine Configuration"
                    if process_kind == "winecfg"
                    else "Windows executable"
                    if process_kind == "run-exe"
                    else "Winetricks"
                )
                details = tr(
                    "{action} did not complete successfully.\n\n{reason}\n\n"
                    "Program: {program}\nCompatibility data: {compat_data}",
                    action=action,
                    reason=reason,
                    program=process.program(),
                    compat_data=process.processEnvironment().value("STEAM_COMPAT_DATA_PATH"),
                )
                title = tr("Prefix utility failed")
                status = tr(
                    "Could not run {action} for {game}: {reason}",
                    action=action,
                    game=game.get("name", tr("game")),
                    reason=reason,
                )
            if output:
                details += (
                    f"\n\n{tr('Proton output' if process_kind == 'game' else 'Process output')}:\n"
                    f"{output[-8000:]}"
                )
            self.set_game_launch_status(status)
            self.append_game_log(game_id, f"[{APP_NAME} error] {reason}\n")
            QMessageBox.critical(self, title, details)

    def finish_game_process(self, game_id, process_kind):
        session = self.game_sessions.get(game_id)
        if session:
            session["active"] = max(0, session["active"] - 1)
            active_by_kind = session.setdefault("active_by_kind", {})
            active_by_kind[process_kind] = max(0, active_by_kind.get(process_kind, 0) - 1)
        self.update_game_process_status()

    def clear_game_winecfg_button(self, game_id, button):
        if self.game_winecfg_buttons.get(game_id) is button:
            self.game_winecfg_buttons.pop(game_id, None)

    def update_game_process_status(self):
        try:
            tracked_pids = self.scan_tracked_processes()
        except OSError as error:
            self.set_game_launch_status(
                tr("Could not inspect Wine processes: {error}", error=error)
            )
            return

        for game in self.state.get("games", []):
            game_id = game["id"]
            pids_by_kind = tracked_pids.get(game_id, {})
            pids = set().union(*pids_by_kind.values()) if pids_by_kind else set()
            session = self.game_sessions.setdefault(
                game_id,
                {"pids": set(), "pids_by_kind": {}, "active": 0, "active_by_kind": {}},
            )
            session["pids"] = pids
            session["pids_by_kind"] = pids_by_kind
            game_pids = pids_by_kind.get("game", set())
            config_pids = pids_by_kind.get("winecfg", set())
            status = self.game_status_labels.get(game_id)
            if status:
                if game_pids:
                    count = len(game_pids)
                    status.setText(
                        tr(
                            "Running · {count} tracked process"
                            if count == 1 else "Running · {count} tracked processes",
                            count=count,
                        )
                    )
                    status.setProperty("statusState", "running")
                elif config_pids:
                    status.setText(
                        tr("Wine Configuration open · {count} tracked processes", count=len(config_pids))
                    )
                    status.setProperty("statusState", "winecfg")
                elif session["active"]:
                    status.setText(tr("Starting · initializing Proton"))
                    status.setProperty("statusState", "starting")
                else:
                    if status.property("statusState") == "running":
                        status.setText(tr("Has exited"))
                    else:
                        status.setText(tr("Not running"))
                    status.setProperty("statusState", "idle")
                status.style().unpolish(status)
                status.style().polish(status)
                self.update_game_play_button(
                    game_id,
                    bool(game_pids) or game_id in self.game_processes,
                    prefix_busy=bool(session["active"])
                    or any(
                        pids_by_kind.get(kind)
                        for kind in ("winecfg", "run-exe", "winetricks")
                    ),
                )
            settings_button = self.game_settings_buttons.get(game_id)
            if settings_button:
                game_running = self.is_game_running(game_id)
                settings_button.setEnabled(not game_running)
                settings_button.setToolTip(
                    tr("Close this game before opening its settings.") if game_running else ""
                )
            winecfg_button = self.game_winecfg_buttons.get(game_id)
            if winecfg_button:
                winecfg_running = bool(
                    config_pids or session.get("active_by_kind", {}).get("winecfg")
                )
                winecfg_button.setEnabled(not winecfg_running)
                winecfg_button.setToolTip(
                    tr("Wine Configuration is already open for this game.")
                    if winecfg_running else tr("Open Wine Configuration for this game's prefix.")
                )

    @staticmethod
    def process_game_tags(pid):
        try:
            environment = (Path("/proc") / str(pid) / "environ").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            return None
        values = {}
        for item in environment.split(b"\0"):
            if item.startswith(b"ASTER_GAME_ID="):
                values["game_id"] = item[len(b"ASTER_GAME_ID="):].decode(
                    "utf-8", errors="replace"
                )
            elif item.startswith(b"ASTER_PROCESS_KIND="):
                values["process_kind"] = item[len(b"ASTER_PROCESS_KIND="):].decode(
                    "utf-8", errors="replace"
                )
        if "game_id" in values:
            values.setdefault("process_kind", "game")
            return values
        return None

    @classmethod
    def scan_tracked_processes(cls):
        tracked_pids = {}
        try:
            proc_entries = Path("/proc").iterdir()
            for entry in proc_entries:
                if not entry.name.isdigit():
                    continue
                tags = cls.process_game_tags(entry.name)
                if tags:
                    tracked_pids.setdefault(tags["game_id"], {}).setdefault(
                        tags["process_kind"], set()
                    ).add(int(entry.name))
        except OSError as error:
            raise OSError(f"Could not inspect Wine processes: {error}") from error
        return tracked_pids

    def is_game_running(self, game_id):
        session = self.game_sessions.get(game_id, {})
        return bool(
            game_id in self.game_processes
            or session.get("active_by_kind", {}).get("game")
            or session.get("pids_by_kind", {}).get("game")
        )

    def update_game_play_button(self, game_id, running, prefix_busy=False):
        running_or_stopping = running or game_id in self.force_stop_pending
        button = self.game_play_buttons.get(game_id)
        if button:
            button.setVisible(not running_or_stopping)
            button.setEnabled(not running_or_stopping and not prefix_busy)
            button.setText(tr("Running") if running_or_stopping else tr("Play"))
            button.setToolTip(
                tr("This game is running under Wine.")
                if running_or_stopping
                else tr("A prefix utility is already running for this app.")
                if prefix_busy
                else tr("Launch this game with Proton.")
            )
        force_stop = self.game_force_stop_buttons.get(game_id)
        if force_stop:
            force_stop.setVisible(running_or_stopping)
            force_stop.setEnabled(running and game_id not in self.force_stop_pending)
            force_stop.setText(
                tr("Stopping…") if game_id in self.force_stop_pending else tr("Force stop")
            )
        settings_button = self.game_settings_buttons.get(game_id)
        if settings_button:
            settings_button.setVisible(not running_or_stopping)
            settings_button.setEnabled(not running_or_stopping)
            settings_button.setToolTip(
                tr("Close this game before opening its settings.")
                if running_or_stopping else ""
            )

    def force_stop_game(self, game):
        game_id = game["id"]
        if game_id in self.force_stop_pending:
            return
        self.update_game_process_status()
        if not self.is_game_running(game_id):
            return
        answer = QMessageBox.question(
            self,
            tr("Force stop game"),
            tr(
                "Force stop {game} and all of its tracked Wine processes?",
                game=game.get("name", tr("this game")),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            tracked = self.scan_tracked_processes()
        except OSError as error:
            QMessageBox.critical(self, tr("Could not inspect game processes"), str(error))
            return
        pids = set(tracked.get(game_id, {}).get("game", set()))
        process = self.game_processes.get(game_id)
        if process and process.state() != QProcess.ProcessState.NotRunning:
            root_pid = process.processId()
            if root_pid:
                pids.add(root_pid)
        if not pids:
            QMessageBox.warning(
                self,
                tr("No tracked game processes"),
                tr(
                    "Kbroton could not find any tracked processes for {game}.",
                    game=game.get("name", tr("this game")),
                ),
            )
            self.update_game_process_status()
            return

        self.force_stop_pending.add(game_id)
        self.update_game_play_button(game_id, True)
        if process and process.state() != QProcess.ProcessState.NotRunning:
            process.terminate()
        self.append_game_log(
            game_id,
            f"[{APP_NAME}] Force stop requested; sending SIGTERM to {len(pids)} process(es).\n",
        )
        failures = self.signal_game_processes(game_id, pids, signal.SIGTERM)
        if failures:
            self.append_game_log(
                game_id,
                f"[{APP_NAME} warning] Could not stop some processes: {'; '.join(failures)}\n",
            )
        QTimer.singleShot(
            2500,
            lambda item=game: self.escalate_game_force_stop(item),
        )

    def signal_game_processes(self, game_id, pids, sent_signal):
        failures = []
        for pid in sorted(pids):
            tags = self.process_game_tags(pid)
            if not tags or tags["game_id"] != game_id or tags["process_kind"] != "game":
                continue
            try:
                os.kill(pid, sent_signal)
            except ProcessLookupError:
                continue
            except PermissionError as error:
                failures.append(f"PID {pid}: {error}")
            except OSError as error:
                failures.append(f"PID {pid}: {error}")
        return failures

    def escalate_game_force_stop(self, game):
        game_id = game["id"]
        try:
            tracked = self.scan_tracked_processes()
        except OSError as error:
            self.append_game_log(
                game_id,
                f"[{APP_NAME} error] Could not check for remaining game processes: {error}\n",
            )
            self.force_stop_pending.discard(game_id)
            self.update_game_process_status()
            return

        remaining = set(tracked.get(game_id, {}).get("game", set()))
        process = self.game_processes.get(game_id)
        if process and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
        failures = self.signal_game_processes(game_id, remaining, signal.SIGKILL)
        if failures:
            self.append_game_log(
                game_id,
                f"[{APP_NAME} error] Could not force-stop some processes: {'; '.join(failures)}\n",
            )
            QMessageBox.warning(
                self,
                tr("Some game processes are still running"),
                tr(
                    "Kbroton could not stop every tracked process:\n\n{errors}",
                    errors="\n".join(failures),
                ),
            )
        elif remaining:
            self.append_game_log(
                game_id,
                f"[{APP_NAME}] Sent SIGKILL to {len(remaining)} unresponsive process(es).\n",
            )
        else:
            self.append_game_log(game_id, f"[{APP_NAME}] Game processes stopped.\n")
        self.force_stop_pending.discard(game_id)
        self.update_game_process_status()

    def append_game_log(self, game_id, text):
        buffer = self.log_buffers.setdefault(game_id, "")
        self.log_buffers[game_id] = (buffer + text)[-500_000:]
        if self.selected_log_game == game_id:
            self.update_log_view()

    def set_game_launch_status(self, message):
        self.statusBar().showMessage(message, 5000)

    def select_game_logs(self, game):
        self.selected_log_game = game["id"]
        self.update_log_view()

    def update_log_view(self):
        if not hasattr(self, "game_log_view"):
            return
        game = next(
            (item for item in self.state.get("games", []) if item["id"] == self.selected_log_game),
            None,
        )
        self.log_title.setText(
            tr("Logs · {name}", name=game["name"]) if game else tr("Game logs")
        )
        text = self.log_buffers.get(self.selected_log_game, "") if self.selected_log_game else ""
        if self.game_log_view.toPlainText() != text:
            self.game_log_view.setPlainText(text)
            self.game_log_view.moveCursor(QTextCursor.MoveOperation.End)

    def toggle_logs(self):
        visible = self.game_log_view.isVisible()
        self.game_log_view.setVisible(not visible)
        self.hide_logs_button.setText(
            tr("Show logs") if visible else tr("Hide logs")
        )


    def remove_game(self, game, parent=None):
        answer = QMessageBox.question(
            parent or self,
            tr("Remove game"),
            tr(
                "Remove {game} and delete its Proton prefix and compatibility data?",
                game=game.get("name", tr("this game")),
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False

        self.update_game_process_status()
        session = self.game_sessions.get(game["id"], {})
        if session.get("active") or session.get("pids"):
            QMessageBox.warning(
                parent or self,
                tr("Game is running"),
                tr(
                    "Close {game} and its Wine processes before removing its Proton prefix.",
                    game=game.get("name", tr("this game")),
                ),
            )
            return False

        try:
            game_data = self.game_compat_data_path(game)
        except (ValueError, TypeError) as error:
            QMessageBox.critical(
                parent or self,
                tr("Invalid game prefix location"),
                tr("The game's compatibility-data path is invalid:\n\n{error}", error=error),
            )
            return False
        compat_root = game_data.parent
        staged_data = None
        try:
            if (
                not game_data.name.isalnum()
                or compat_root.is_symlink()
                or game_data.is_symlink()
            ):
                raise OSError(
                    "Refusing to delete a non-alphanumeric or symlinked game compatibility-data path."
                )
            if game_data.exists():
                if not game_data.is_dir():
                    raise OSError(f"The game compatibility-data path is not a directory:\n{game_data}")
                staged_data = compat_root / f".delete-{uuid.uuid4().hex}"
                game_data.replace(staged_data)
        except OSError as error:
            QMessageBox.critical(
                parent or self,
                tr("Could not remove game prefix"),
                tr(
                    "The game was not removed because its Proton prefix could not be prepared for deletion.\n\n{error}",
                    error=error,
                ),
            )
            return False

        previous_games = self.state["games"]
        self.state["games"] = [item for item in previous_games if item["id"] != game["id"]]
        if self.persist_state():
            self.refresh_games()
            cleanup_errors = []
            try:
                self.remove_game_desktop_entry(game)
            except OSError as error:
                cleanup_errors.append(f"Desktop launcher: {error}")
            icon_path = self.game_icon_path(game)
            try:
                if icon_path.parent.is_symlink():
                    raise OSError(
                        f"Refusing to remove through a symlink: {icon_path.parent}"
                    )
                if icon_path.is_symlink():
                    raise OSError(f"Refusing to remove a symlink: {icon_path}")
                if icon_path.exists():
                    if not icon_path.is_file():
                        raise OSError(f"Cached icon is not a regular file: {icon_path}")
                    icon_path.unlink()
            except OSError as error:
                cleanup_errors.append(f"Cached icon: {error}")
            if staged_data is not None:
                try:
                    shutil.rmtree(staged_data)
                except OSError as error:
                    cleanup_errors.append(
                        f"Proton data ({staged_data}): {error}"
                    )
            if cleanup_errors:
                QMessageBox.warning(
                    parent or self,
                    tr("Game removed; cleanup incomplete"),
                    tr(
                        "The game was removed from the library, but some files could not "
                        "be cleaned up:\n\n{errors}",
                        errors="\n".join(cleanup_errors),
                    ),
                )
            return True
        self.state["games"] = previous_games
        if staged_data is not None:
            try:
                staged_data.replace(game_data)
            except OSError as error:
                QMessageBox.critical(
                    parent or self,
                    tr("Could not restore game prefix"),
                    tr(
                        "The library could not be saved and the Proton data could not be restored to:\n\n{path}\n\n{error}",
                        path=game_data,
                        error=error,
                    ),
                )
        return False

    def persist_state(self):
        try:
            save_state(self.state)
            return True
        except OSError as error:
            QMessageBox.critical(self, tr("Could not save library"), str(error))
            return False


ROUNDING_STYLESHEET = """
QFrame#settingsHeaderPanel,
QFrame#libraryHeaderPanel,
QFrame#settingsFormPanel,
QFrame#installHeaderPanel,
QFrame#installFormPanel,
QFrame#artworkKeyNotice {
    border: 1px solid palette(mid);
    border-radius: 10px;
}
QFrame#settingsHeaderPanel {
    background-color: palette(alternate-base);
}
QLabel#appVersionBadge {
    border: 1px solid palette(mid);
    border-radius: 8px;
    padding: 3px 8px;
    font-weight: 600;
}
QFrame#libraryHeaderPanel {
    background-color: palette(alternate-base);
}
QFrame#settingsFormPanel {
    background-color: palette(window);
}
QFrame#installHeaderPanel {
    background-color: palette(alternate-base);
}
QFrame#installFormPanel {
    background-color: palette(window);
}
QFrame#artworkKeyNotice {
    background-color: #ed8b00;
    border-color: #c97700;
}
QLabel#artworkKeyNoticeText {
    color: #1f1f1f;
}
QPushButton[noticeClose="true"] {
    color: white;
    background-color: #b3261e;
    border: 1px solid #9f1f19;
    border-radius: 6px;
    font-weight: 600;
}
QPushButton[noticeClose="true"]:hover {
    color: white;
    background-color: #c73830;
    border-color: #b3261e;
}
QFrame#protonUpdatePanel,
QFrame#countPanel,
QFrame#logsPanel,
QFrame#gameCard,
QFrame#emptyGamePanel,
QFrame#gameIconFrame {
    border: 1px solid palette(mid);
    border-radius: 10px;
    background-color: palette(window);
}
QFrame#protonUpdatePanel {
    border-color: #0d47a1;
    background-color: #1565c0;
}
QFrame#protonUpdatePanel QLabel {
    color: white;
}
QProgressBar#protonProgress {
    border: 0;
    background-color: #0d47a1;
}
QProgressBar#protonProgress::chunk {
    background-color: #64b5f6;
}
QPushButton#protonUpdateDismiss {
    font-size: 20px;
    padding: 0;
}
QFrame#gameIconFrame {
    background-color: palette(base);
}
QLabel#gameIcon {
    color: palette(mid);
    font-size: 26px;
    font-weight: 600;
}
QFrame#versionPanel {
    border: 1px solid palette(mid);
    border-radius: 7px;
    background-color: palette(base);
}
QLabel#gameProcessStatus[statusState="running"] {
    color: white;
    background-color: #2e7d32;
    border: 1px solid #276b2b;
    border-radius: 6px;
    padding: 3px 8px;
}
QLabel#gameProcessStatus[statusState="winecfg"] {
    color: white;
    background-color: #ed8b00;
    border-radius: 6px;
    padding: 3px 8px;
}
QPushButton#playButton {
    color: white;
    background-color: #27864a;
    border: 1px solid #237a42;
    border-radius: 7px;
    padding: 7px 16px;
    font-weight: 600;
}
QPushButton#playButton:hover:enabled {
    background-color: #2f9956;
    border-color: #27864a;
}
QPushButton#playButton:pressed:enabled {
    background-color: #1f713d;
    border-color: #1b6335;
}
QPushButton#playButton:disabled {
    color: palette(mid);
    background-color: palette(button);
    border-color: palette(mid);
}
QPushButton#forceStopButton {
    color: white;
    background-color: #b3261e;
    border: 1px solid #9f1f19;
    border-radius: 7px;
    padding: 7px 12px;
    font-weight: 600;
}
QPushButton#forceStopButton:hover:enabled {
    background-color: #c73830;
    border-color: #b3261e;
}
QPushButton#forceStopButton:disabled {
    color: palette(mid);
    background-color: palette(button);
    border-color: palette(mid);
}
QPushButton#removeGameButton {
    color: white;
    background-color: #b3261e;
    border: 1px solid #9f1f19;
    border-radius: 7px;
    padding: 7px 16px;
    font-weight: 600;
}
QPushButton#removeGameButton:disabled {
    color: palette(mid);
    background-color: palette(button);
    border-color: palette(mid);
}
QPushButton#removeGameButton:hover:enabled {
    background-color: #c73830;
    border-color: #b3261e;
}
QPushButton#removeGameButton:pressed:enabled {
    background-color: #8f1d17;
    border-color: #7d1914;
}
"""


def main():
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument(
        "--launch-game",
        metavar="GAME_ID",
        help=argparse.SUPPRESS,
    )
    arguments = parser.parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    app.setStyleSheet(ROUNDING_STYLESHEET)
    window = LauncherWindow()
    if arguments.launch_game:
        window.showMinimized()
        QTimer.singleShot(
            0,
            lambda game_id=arguments.launch_game: window.launch_game_by_id(game_id),
        )
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
